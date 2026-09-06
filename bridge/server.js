/**
 * Bridge — VPS 中转站
 *
 * 前端连这里 (SSE)，收到消息转给 Nox Core (:8100)，回来的流再推给前端。
 * WebSocket 现在只剩 /ws/stt（实时语音识别）。
 *
 * 模块：日记 / 相册 / 书架 / 清单 / 搜索 / 推送 / 共读回调
 *
 * 已下线：
 *   本地 Agent (WebSocket /ws) —— 消息转发给她电脑上的 Claude Code 跑，
 *     要求 PC 常开。从来没用过，2026-08-05 整条删除。
 *   apiMode —— bridge 里自己拼 prompt + 接工具，和 Core 是两份平行实现，
 *     2026-07-28 删除。
 */
import express from "express";
import cors from "cors";
import crypto, { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import http from "node:http";
import fs from "node:fs";
import { Readable } from "node:stream";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WebSocketServer, WebSocket } from "ws";
import Database from "better-sqlite3";
import multer from "multer";
import { lookupMovie, longEnough } from "./lib/movie-meta.js";
import webpush from "web-push";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = process.env.PORT || 3003;
const server = http.createServer(app);

app.use(cors());
app.use(express.json({ limit: "30mb" }));

const AUTH_TOKEN = process.env.NOX_TOKEN || "";
const LOGIN_PASSWORD = process.env.NOX_LOGIN_PASSWORD || AUTH_TOKEN;

function readAuthToken(req) {
  try {
    const url = new URL(req.url || "/", "http://127.0.0.1");
    return String(
      req.headers["x-nox-token"] ||
      (req.headers.authorization || "").replace(/^Bearer\s+/i, "") ||
      url.searchParams.get("token") ||
      "",
    );
  } catch {
    return String(req.headers["x-nox-token"] || (req.headers.authorization || "").replace(/^Bearer\s+/i, "") || "");
  }
}

// ── 音乐流的短期签名 ─────────────────────────────────────────
// 聊天里的音乐卡片要能边下边播。`<audio src>` **带不了 header**，
// 所以拿不到前端注入的 X-Nox-Token —— 只能把授权放进 URL。
//
// 不直接把 NOX_TOKEN 塞进 URL（那等于把万能钥匙写进 <audio> 标签，
// 会进浏览器历史、也可能被分享出去）。改成 HMAC 签名：
// 一个签名只授权**一首歌**，7 天过期。
const MUSIC_SIG_TTL_MS = 7 * 24 * 3600 * 1000;

function musicSig(id, exp) {
  return crypto.createHmac("sha256", AUTH_TOKEN || "nox-music")
    .update(`music:${id}:${exp}`).digest("hex").slice(0, 24);
}

function musicStreamPath(id) {
  const exp = Date.now() + MUSIC_SIG_TTL_MS;
  return `/api/music/stream?id=${encodeURIComponent(id)}&exp=${exp}&sig=${musicSig(id, exp)}`;
}

function verifyMusicSig(q) {
  const id = q?.id, exp = q?.exp, sig = q?.sig;
  if (!id || !exp || !sig) return false;
  if (!/^\d+$/.test(String(exp)) || Number(exp) < Date.now()) return false;
  const expected = musicSig(String(id), String(exp));
  if (String(sig).length !== expected.length) return false;
  try {
    return crypto.timingSafeEqual(Buffer.from(String(sig)), Buffer.from(expected));
  } catch { return false; }
}

// ── 玩具中继页的专用钥匙 ────────────────────────────────────
//
// 🔴 **这个东西的来历：2026-08-26 发现 NOX_TOKEN 在公网上裸奔。**
//
//   https://noxtang.com/toy.html  →  200，不需要任何认证
//   页面源码里                     →  明文写着 NOX_TOKEN
//
// 拿它能打 `/api/*` 的全部路由：聊天记录、健康数据、待办、记忆，
// 还有往她手机推通知。**一个玩具中继页泄露了整套系统的钥匙。**
//
// 根因是这个页面必须在浏览器里跑（Web Bluetooth 只能在页面里用），
// 而它要轮询 `/api/toy/state` —— 于是当初直接把万能钥匙写了进去。
//
// 现在给它一把**只能开玩具**的钥匙。页面泄露的话，
// 最坏情况是别人能动那个设备（已经很糟，所以页面本身也换了随机路径），
// 但至少不再是整套系统。
const TOY_TOKEN = process.env.NOX_TOY_TOKEN || "";

function ensureApiAuth(req, res, next) {
  if (req.path === "/auth/login") return next();
  // 探活豁免：/api/health 只回各服务状态字和延迟，不含用户数据——
  // doctor.sh、cron、OS 状态页都吃它，不值得为它发 token
  if (req.path === "/health") return next();
  // 音乐流带有效签名就放行。签名只对这一首歌有效，过期即失效
  if (req.path === "/music/stream" && verifyMusicSig(req.query)) return next();
  // 🔴 玩具钥匙**只开玩具**。`startsWith("/toy/")` 而不是 includes ——
  // 前者是路径前缀，后者能被 `/api/anything?x=/toy/` 这种糊弄过去
  if (TOY_TOKEN && req.path.startsWith("/toy/") && readAuthToken(req) === TOY_TOKEN) {
    return next();
  }
  // 书封公开直出：文件名是不可猜的 UUID，图也不敏感 —— img 标签带不了 token
  if (req.path.startsWith("/library/covers/")) return next();
  if (!AUTH_TOKEN) return next();
  if (readAuthToken(req) === AUTH_TOKEN) return next();
  res.status(403).json({ error: "forbidden" });
}

app.use("/api", ensureApiAuth);

app.post("/api/auth/login", (req, res) => {
  if (!AUTH_TOKEN || !LOGIN_PASSWORD) {
    return res.status(503).json({ error: "auth_unavailable" });
  }
  if ((req.body?.password || "") !== LOGIN_PASSWORD) {
    return res.status(403).json({ error: "密码不对，再试一次。" });
  }
  res.json({ ok: true, token: AUTH_TOKEN });
});

app.get("/api/auth/verify", (req, res) => {
  res.json({ ok: true });
});

// ==============================================================
// SQLite 初始化
// 2026-09-05：sql.js（WASM）换 better-sqlite3（原生）。
// 旧方案整库在内存、每次写全量 export 覆盖写盘——写放大 + 非原子写，
// 断电就是损坏。现在 WAL 自动落盘，saveDb() 整个退役。
// 文件本身是合法 SQLite，老库直接打开，零迁移。
// 下面的薄壳保持 sql.js 的 db.run / db.exec 形状，30+ 处 DDL 调用点不用动。
// ==============================================================
const DB_PATH = process.env.DB_PATH || path.join(__dirname, "..", "data", "nox-bridge.db");
const DATA_DIR = path.dirname(DB_PATH);
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

const _db = new Database(DB_PATH);
_db.pragma("journal_mode = WAL");
_db.pragma("synchronous = NORMAL");

const db = {
  run(sql, params = []) { _db.prepare(sql).run(...(Array.isArray(params) ? params : [params])); },
  exec(sql, params = []) {
    const rows = _db.prepare(sql).all(...(Array.isArray(params) ? params : [params]));
    if (!rows.length) return [];
    const columns = Object.keys(rows[0]);
    return [{ columns, values: rows.map(o => columns.map(c => o[c])) }];
  },
};

function dbRun(sql, params = []) { db.run(sql, params); }
// 迁移专用：列已存在是预期，其余失败必须留一行日志再继续启动
//（规范见 docs/LOGGING.md——「静默失败」是这个系统反复栽的形状）
function dbTry(sql) {
  try { db.run(sql); } catch (e) {
    if (/duplicate column/i.test(e.message)) return;
    console.warn("[Bridge] 迁移失败（继续启动）:", sql.slice(0, 60), "|", e.message);
  }
}
function dbAll(sql, params = []) {
  const r = db.exec(sql, params);
  if (!r.length) return [];
  return r[0].values.map(row => { const o = {}; r[0].columns.forEach((c, i) => o[c] = row[i]); return o; });
}

// 聊天消息落库（搜索 / 历史恢复用）；meta 存 thinking / 语音卡片 / 图片等附加信息
function saveMessage(sessionId, role, content, meta = "") {
  if (!content && !meta) return;
  try {
    const metaStr = typeof meta === "string" ? meta : JSON.stringify(meta);
    dbRun(`INSERT INTO conversations VALUES (?,?,?,?,?)`, [sessionId || "", role, content || "", new Date().toISOString(), metaStr]);
  } catch (e) { console.log("[Bridge] saveMessage failed:", e.message); }
}

// 情绪标签只给 TTS 用，进入聊天记录/字幕前必须剥掉
function stripVoiceTags(t) {
  return (t || "")
    .replace(/\[(?:whining|excited|pouting|softly|sniffling|laughing|eager|pause|whispers?|sighs?|giggles?)\]/gi, "")
    .replace(/\s{2,}/g, " ").trim();
}

// 通话指令已移到 Core 的 personality/scenes.py（中英两个情景各一份）。
// 以前 bridge 这儿也有一份，两处措辞不一致时电话里的语气会跟着飘。

// 建表
db.run(`CREATE TABLE IF NOT EXISTS diary (
  id TEXT PRIMARY KEY, date TEXT, time TEXT, mood TEXT, author TEXT, body TEXT, created_at TEXT
)`);
db.run(`CREATE TABLE IF NOT EXISTS diary_comments (
  id TEXT PRIMARY KEY, diary_id TEXT, author TEXT, text TEXT, created_at TEXT
)`);
// 本地 books / book_annotations 两张表 2026-08-05 删了。
// 书架实际走共读代理（/api/reading/* → co-reading:3100，BookReader 用 /reading/...），
// 这套本地表 2026-07 就没人读了，只留了一张永远空的表和三四个死端点。
// 对应端点 /api/books/list|upload|:id/progress|:id/annotation 一并删除。
db.run(`CREATE TABLE IF NOT EXISTS gallery (
  id TEXT PRIMARY KEY, url TEXT, thumbnail TEXT, favorited INTEGER DEFAULT 0, album TEXT DEFAULT '', created_at TEXT
)`);
// 2026-08-04 加 description 列 —— 相册图片的描述，让 Nox 能按内容选图而不是盲发。
// ALTER 在列已存在时抛错，try/catch 吞掉。
dbTry(`ALTER TABLE gallery ADD COLUMN description TEXT DEFAULT ''`)
// 共影的观影记录（2026-08-22，共影技术方案 v2.0 的 P1）。
//
// 一行 = 一次观影。**同时承担两件事**，别拆成两张表：
//   · 「她现在在不在看片」  —— ended_at 为空 且 last_seen_at 够新
//   · 「一起看过 XXX」      —— 全部历史
//
// ⚠️ 现场状态**不放内存**。放内存的话 bridge 一重启她就"没在看片"了，
// 而 Core 那边的抑制会跟着失效 —— 那正好是最不该出错的时刻（她在看片）。
// 同一张表既是现场也是历史，只有一个真相。
db.run(`CREATE TABLE IF NOT EXISTS watch_sessions (
  id TEXT PRIMARY KEY,
  title TEXT, episode TEXT, mode TEXT, url TEXT,
  duration_s INTEGER DEFAULT 0,
  position_ms INTEGER DEFAULT 0,
  play_state TEXT DEFAULT 'playing',
  started_at TEXT, last_seen_at TEXT, ended_at TEXT
)`);
// 🎟️ 电影票根（2026-09-04）。糖糖：看完电影生成一张票根。
//
// ⚠️ **一场只能有一张**（session_id 主键）。她可能点两次「生成」，
// 或者网络重试 —— 不设主键的话同一场会出现两张一模一样的票。
//
// meta_json 存维基拉到的元信息原文（片名/年份/导演/类型/海报…）。
// 存原文不存拆开的列：以后想多印一栏不用改表，而且拉到过什么
// 事后能追溯（万一哪天维基改了条目）。
db.run(`CREATE TABLE IF NOT EXISTS movie_tickets (
  session_id TEXT PRIMARY KEY,
  kind TEXT,
  title TEXT,
  review TEXT,
  meta_json TEXT,
  watched_from TEXT, watched_to TEXT, watched_minutes INTEGER DEFAULT 0,
  created_at TEXT
)`);
db.run(`CREATE TABLE IF NOT EXISTS todos (
  id TEXT PRIMARY KEY, text TEXT, time TEXT, done INTEGER DEFAULT 0, created_at TEXT
)`);
// 2026-08-05 加 synced 列 —— 这条有没有同步进 GitHub 的 todo.md。
// 加之前 App 的待办和 todo.md 是两个孤岛，晨报只看得见后者，
// 她在手机上记的东西第二天没人提。
dbTry(`ALTER TABLE todos ADD COLUMN synced INTEGER DEFAULT 0`)
// 2026-08-18 时间模型（Todo-Daily-Planner-设计.md 第一节）。
// 前端 todo 成为唯一活清单，GitHub todo.md 退役为只读存档。
//
//   repeat  = once | daily | weekly | weekly_count | anytime
//   at      = "HH:MM"，到点追人用
//   weekdays= "1,4" 周一周四（repeat=weekly 时有效，1=周一 … 7=周日）
//   due     = "YYYY-MM-DD"（repeat=once 时的到期日）
//   fired_on= "YYYY-MM-DD"，今天已经追过了没。**跨天重开靠它**
//   times   = 每周几次（repeat=weekly_count 时有效）
//   done_log= "2026-08-18,2026-08-20"，这条完成过的日期。算周配额用
//
// ⚠️ weekly 和 weekly_count 是**两种东西**（糖糖 2026-08-18 举的例子逼出来的）：
//   weekly       = 每周一、四 19:00 —— 绑定具体星期，那天没做就追
//   weekly_count = 每周 3 次，哪天都行 —— 是**一周的配额**，
//                  追的不是「今天该做」，而是「这周还差几次，周末快到了」
dbTry(`ALTER TABLE todos ADD COLUMN repeat TEXT DEFAULT ''`)
dbTry(`ALTER TABLE todos ADD COLUMN at TEXT DEFAULT ''`)
dbTry(`ALTER TABLE todos ADD COLUMN weekdays TEXT DEFAULT ''`)
dbTry(`ALTER TABLE todos ADD COLUMN due TEXT DEFAULT ''`)
dbTry(`ALTER TABLE todos ADD COLUMN fired_on TEXT DEFAULT ''`)
dbTry(`ALTER TABLE todos ADD COLUMN times INTEGER DEFAULT 0`)
dbTry(`ALTER TABLE todos ADD COLUMN done_log TEXT DEFAULT ''`)
// 2026-08-18：最后一次划掉的**完整时刻**。
// `done_log` 只存日期（周配额要按天算），落不到「Nox 的一天」的时间轴上 ——
// 那条轴要的是 14:48 这种精度，不是 2026-08-18。
dbTry(`ALTER TABLE todos ADD COLUMN last_done_at TEXT DEFAULT ''`)
// 2026-08-27：备注与分类标签（App / OS 双端 Today/Todo 重构加的）。
// tag 是轻量分类：没有后台字典，前端按名字取色画彩点；note 纯文本，
// 条目行上收起、点开才展示，列表接口原样透传即可。
dbTry(`ALTER TABLE todos ADD COLUMN note TEXT DEFAULT ''`)
dbTry(`ALTER TABLE todos ADD COLUMN tag TEXT DEFAULT ''`)
// 聊天消息表（搜索用 + Nox 兼容）
db.run(`CREATE TABLE IF NOT EXISTS conversations (
  id TEXT, role TEXT, content TEXT, timestamp TEXT, metadata TEXT
)`);
// 通用设置 KV（头像等，跨设备/跨部署持久化）
db.run(`CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)`);
// API 用量流水（Console 页统计用）
db.run(`CREATE TABLE IF NOT EXISTS usage_log (
  ts TEXT, tokens_in INTEGER, tokens_out INTEGER, cache_read INTEGER, cache_write INTEGER, cost REAL
)`);
// 2026-08-01 加 model 列：以前只有一个模型，现在 DeepSeek 和 Claude 混着用，
// 不记型号就没法分开算钱（两家单价差 10 倍以上）。老行的 model 为 NULL。
dbTry(`ALTER TABLE usage_log ADD COLUMN model TEXT`)
// Web Push 订阅（iOS PWA 锁屏推送）
db.run(`CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, sub TEXT, created_at TEXT)`);

// 订阅清单（Settings → Notifications 页）：endpoint 掩码，别把整条
// 订阅地址甩到前端 —— 它等同一把推送凭证
app.get("/api/push/subscriptions", (req, res) => {
  const rows = dbAll("SELECT endpoint, created_at FROM push_subs ORDER BY created_at DESC");
  res.json({
    ok: true,
    count: rows.length,
    items: rows.map((r) => ({
      endpoint: (r.endpoint || "").slice(0, 44) + "…",
      created_at: r.created_at,
    })),
  });
});

// ==============================================================
// 厂商账本快照（2026-09-01，Models 页的「真实口径」）
//
// DeepSeek 没有明细账单 API，只有余额接口；OpenRouter 的 credits 接口
// 给累计真实消耗。定时拉一笔存一行，「真实消耗」= 相邻两行差值——
// 这是页面上唯一能称为「真」的钱，token×单价 那套是估算。
//
// 新服务商 = PROVIDER_LEDGERS 加一个 fetcher，账本自己长出来。
// 没配 key 的账本安静地空着，不报错不刷屏。
// ==============================================================
db.run(`CREATE TABLE IF NOT EXISTS ledger_balance (
  provider TEXT NOT NULL, ts TEXT NOT NULL,
  balance REAL, usage_total REAL, topped_up REAL,
  currency TEXT DEFAULT '', is_available INTEGER DEFAULT 1,
  PRIMARY KEY (provider, ts)
)`);

const PROVIDER_LEDGERS = {
  deepseek: {
    label: "DeepSeek",
    // 余额接口和聊天共用同一把 key。充值用 topped_up 的增量剔除
    fetch: async () => {
      const key = process.env.DEEPSEEK_API_KEY || "";
      if (!key) return null;
      const r = await fetch("https://api.deepseek.com/user/balance", {
        headers: { Authorization: `Bearer ${key}` },
        signal: AbortSignal.timeout(10000),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      const info = (d.balance_infos || [])[0] || {};
      return {
        balance: parseFloat(info.total_balance) || 0,
        usage_total: null,
        topped_up: parseFloat(info.topped_up_balance) || 0,
        currency: info.currency || "CNY",
        is_available: d.is_available ? 1 : 0,
      };
    },
  },
  openrouter: {
    label: "OpenRouter",
    // credits 接口：total_usage 是累计真实消耗，只涨不跌，比余额差省心
    fetch: async () => {
      const key = process.env.OPENROUTER_API_KEY || process.env.API_KEY || "";
      if (!key) return null;
      const r = await fetch("https://openrouter.ai/api/v1/credits", {
        headers: { Authorization: `Bearer ${key}` },
        signal: AbortSignal.timeout(10000),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()).data || {};
      return {
        balance: (data.total_credits || 0) - (data.total_usage || 0),
        usage_total: data.total_usage || 0,
        topped_up: data.total_credits || 0,
        currency: "USD",
        is_available: 1,
      };
    },
  },
};

async function snapshotLedgers() {
  for (const [provider, ledger] of Object.entries(PROVIDER_LEDGERS)) {
    try {
      const s = await ledger.fetch();
      if (!s) continue;
      dbRun(`INSERT OR REPLACE INTO ledger_balance VALUES (?,?,?,?,?,?,?)`,
            [provider, new Date().toISOString(), s.balance, s.usage_total,
             s.topped_up, s.currency, s.is_available]);
    } catch (e) {
      console.log(`[Bridge] ${provider} 账本快照失败:`, e.message);
    }
  }
}

// ==============================================================
// 🥗 健身饮食记录（M2 表结构，2026-08-05）
// 糖糖的饮食/运动/体重，与 OB 情感记忆分离的第二套结构化存储。
// PRD：D:\WorkBuddy\健身饮食记录系统PRD.md
// ==============================================================
db.run(`PRAGMA foreign_keys = ON`);

// 1. foods 食物库
db.run(`CREATE TABLE IF NOT EXISTS foods (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL,
  aliases       TEXT DEFAULT '',
  category      TEXT DEFAULT '',
  cal_100g      REAL NOT NULL,
  protein_100g  REAL DEFAULT 0,
  carbs_100g    REAL DEFAULT 0,
  fat_100g      REAL DEFAULT 0,
  source        TEXT DEFAULT 'custom',
  is_cooked     INTEGER DEFAULT 1,
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(name)
)`);

// 2. meals 一餐
db.run(`CREATE TABLE IF NOT EXISTS meals (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  meal_date     TEXT NOT NULL,
  meal_type     TEXT NOT NULL,
  note          TEXT DEFAULT '',
  total_cal     REAL DEFAULT 0,
  total_protein REAL DEFAULT 0,
  total_carbs   REAL DEFAULT 0,
  total_fat     REAL DEFAULT 0,
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  updated_at    TEXT DEFAULT (datetime('now','localtime'))
)`);

// 3. meal_items 餐内条目
db.run(`CREATE TABLE IF NOT EXISTS meal_items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  meal_id       INTEGER NOT NULL REFERENCES meals(id) ON DELETE CASCADE,
  food_id       INTEGER REFERENCES foods(id),
  food_name     TEXT NOT NULL,
  amount        REAL NOT NULL,
  unit_type     TEXT NOT NULL,
  unit_name     TEXT DEFAULT '',
  est_grams     REAL DEFAULT NULL,
  cal           REAL DEFAULT 0,
  protein       REAL DEFAULT 0,
  carbs         REAL DEFAULT 0,
  fat           REAL DEFAULT 0,
  is_estimate   INTEGER DEFAULT 0,
  created_at    TEXT DEFAULT (datetime('now','localtime'))
)`);

// 4. unit_conversions 估量换算
db.run(`CREATE TABLE IF NOT EXISTS unit_conversions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_name     TEXT NOT NULL,
  food_category TEXT DEFAULT '',
  food_id       INTEGER REFERENCES foods(id),
  est_grams     REAL NOT NULL,
  note          TEXT DEFAULT ''
)`);

// 5. exercises 运动
db.run(`CREATE TABLE IF NOT EXISTS exercises (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  exercise_date TEXT NOT NULL,
  type          TEXT NOT NULL,
  source        TEXT DEFAULT 'manual',
  duration_min  REAL DEFAULT 0,
  calories      REAL DEFAULT 0,
  difficulty    INTEGER DEFAULT 0,
  mood          INTEGER DEFAULT 0,
  note          TEXT DEFAULT '',
  created_at    TEXT DEFAULT (datetime('now','localtime'))
)`);
// 迁移：旧表可能没有 difficulty / mood 列
dbTry(`ALTER TABLE exercises ADD COLUMN difficulty INTEGER DEFAULT 0`)
dbTry(`ALTER TABLE exercises ADD COLUMN mood INTEGER DEFAULT 0`)

// 6. body_weight 体重
db.run(`CREATE TABLE IF NOT EXISTS body_weight (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  weight_date   TEXT NOT NULL,
  weight_kg     REAL NOT NULL,
  note          TEXT DEFAULT '',
  created_at    TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(weight_date)
)`);

// 7. daily_budget 每日预算
db.run(`CREATE TABLE IF NOT EXISTS daily_budget (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  budget_date   TEXT NOT NULL,
  budget_kcal   REAL NOT NULL DEFAULT 1250,
  remaining     REAL DEFAULT 1250,
  note          TEXT DEFAULT '',
  updated_at    TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(budget_date)
)`);

// 8. location 位置上报（Nox Location Provider v2 Phase 1）
db.run(`CREATE TABLE IF NOT EXISTS location (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lat REAL NOT NULL,
  lng REAL NOT NULL,
  accuracy REAL DEFAULT 0,
  battery REAL,
  trigger TEXT DEFAULT 'manual',
  source TEXT DEFAULT 'caelum_pwa',
  timestamp TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now','localtime'))
)`);
// 24h 自动清理原始坐标（隐私：不存历史轨迹）
setInterval(() => {
  try { dbRun("DELETE FROM location WHERE created_at < datetime('now','localtime','-24 hours')"); }
  catch (e) { console.warn("[Bridge] 位置历史清理失败:", e.message); } // 清理失败=轨迹在堆积，必须留痕
}, 60 * 60e3);

// 索引
db.run(`CREATE INDEX IF NOT EXISTS idx_meals_date      ON meals(meal_date)`);
db.run(`CREATE INDEX IF NOT EXISTS idx_meal_items_meal ON meal_items(meal_id)`);
db.run(`CREATE INDEX IF NOT EXISTS idx_foods_name      ON foods(name)`);
db.run(`CREATE INDEX IF NOT EXISTS idx_exercises_date  ON exercises(exercise_date)`);

// 迁移：旧 meal_type 可能存了英文，统一转中文（2026-08-06）
try {
  db.run(`UPDATE meals SET meal_type='早餐' WHERE meal_type IN ('breakfast','Breakfast')`);
  db.run(`UPDATE meals SET meal_type='午餐' WHERE meal_type IN ('lunch','Lunch')`);
  db.run(`UPDATE meals SET meal_type='晚餐' WHERE meal_type IN ('dinner','Dinner')`);
  db.run(`UPDATE meals SET meal_type='加餐' WHERE meal_type IN ('snack','Snack')`);
} catch (e) { console.warn("[Bridge] meal_type 迁移跳过:", e.message); }

// ==============================================================
const VAPID_PUB = process.env.VAPID_PUB || "REDACTED-VAPID-PUB";
const VAPID_PRIV = process.env.VAPID_PRIV || "REDACTED-VAPID-PRIV";
webpush.setVapidDetails("mailto:i35768737@gmail.com", VAPID_PUB, VAPID_PRIV);

async function sendPushAll(title, body) {
  const rows = dbAll("SELECT endpoint, sub FROM push_subs");
  for (const r of rows) {
    try {
      await webpush.sendNotification(JSON.parse(r.sub), JSON.stringify({ title, body }));
    } catch (e) {
      // 订阅失效（换机/撤销授权）就清掉
      if (e.statusCode === 404 || e.statusCode === 410) dbRun("DELETE FROM push_subs WHERE endpoint=?", [r.endpoint]);
    }
  }
}
// FTS5 全文索引
dbTry(`CREATE VIRTUAL TABLE IF NOT EXISTS conv_fts USING fts5(content, content_rowid='rowid')`); // FTS5 挂了搜索会静默哑掉，必须留痕
// 文件上传
const uploadDir = path.join(DATA_DIR, "uploads");
if (!fs.existsSync(uploadDir)) fs.mkdirSync(uploadDir, { recursive: true });
// multer 的 originalname 是 latin1 编码，中文名需要转回 utf8
function decodeUploadName(name) {
  try { return Buffer.from(name, "latin1").toString("utf8"); } catch { return name; }
}
const storage = multer.diskStorage({
  destination: uploadDir,
  // 不使用 originalname 作为文件名，避免路径穿越和特殊字符问题，只保留扩展名
  filename: (req, file, cb) => {
    const ext = path.extname(decodeUploadName(file.originalname)).replace(/[^.\w]/g, "").slice(0, 10);
    cb(null, `${Date.now()}-${randomUUID().slice(0, 6)}${ext}`);
  },
});
const upload = multer({ storage, limits: { fileSize: 50 * 1024 * 1024 } }); // 50MB
const sttUpload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 12 * 1024 * 1024 } });
const DASHSCOPE_REALTIME_URL = process.env.DASHSCOPE_REALTIME_URL || "wss://dashscope.aliyuncs.com/api-ws/v1/realtime";
const DASHSCOPE_REALTIME_MODEL = process.env.DASHSCOPE_REALTIME_MODEL || "qwen3-asr-flash-realtime";

// ==============================================================
// WebSocket — 只剩语音识别（STT）
//
// 本机 Agent 那条长连接已于 2026-08-05 下线：糖糖从来没用过。
// 它原本是「消息 → 推给她电脑上的 Claude Code → 跑完回传」那套，
// 要求 PC 常开，实际一次都没走通过。连带删掉的还有 /ws 升级入口、
// agent 模式路由、/api/agents、/api/chat-json、/api/obsidian、
// /api/sessions。历史包袱清了，顺便少一个不带鉴权的 WS 入口。
// ==============================================================
const sttWss = new WebSocketServer({ noServer: true });

function rejectUpgrade(socket) {
  try {
    socket.write("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
  } catch {}
  try {
    socket.destroy();
  } catch {}
}

server.on("upgrade", (req, socket, head) => {
  const pathname = new URL(req.url || "/", "http://127.0.0.1").pathname;

  if (pathname === "/ws/stt") {
    if (AUTH_TOKEN && readAuthToken(req) !== AUTH_TOKEN) {
      rejectUpgrade(socket);
      return;
    }
    sttWss.handleUpgrade(req, socket, head, (ws) => {
      sttWss.emit("connection", ws, req);
    });
    return;
  }

  socket.destroy();
});

sttWss.on("connection", (clientWs) => {
  if (!DASHSCOPE_API_KEY) {
    clientWs.send(JSON.stringify({ type: "error", error: "stt_unavailable" }));
    clientWs.close();
    return;
  }

  const upstreamUrl = `${DASHSCOPE_REALTIME_URL.replace(/\/$/, "")}?model=${encodeURIComponent(DASHSCOPE_REALTIME_MODEL)}`;
  const upstreamWs = new WebSocket(upstreamUrl, {
    headers: {
      Authorization: `Bearer ${DASHSCOPE_API_KEY}`,
      "OpenAI-Beta": "realtime=v1",
    },
  });

  const safeSendClient = (payload) => {
    if (clientWs.readyState === clientWs.OPEN) {
      try { clientWs.send(JSON.stringify(payload)); } catch {}
    }
  };

  const safeSendUpstream = (payload) => {
    if (upstreamWs.readyState === upstreamWs.OPEN) {
      try { upstreamWs.send(JSON.stringify(payload)); } catch {}
    }
  };

  upstreamWs.on("open", () => {
    safeSendUpstream({
      event_id: randomUUID(),
      type: "session.update",
      session: {
        modalities: ["text"],
        input_audio_format: "pcm",
        sample_rate: 16000,
        input_audio_transcription: { language: "zh" },
        turn_detection: {
          type: "server_vad",
          threshold: 0.0,
          silence_duration_ms: 400,
        },
      },
    });
  });

  upstreamWs.on("message", (buf) => {
    let msg;
    try { msg = JSON.parse(buf.toString()); } catch { return; }
    if (!msg?.type) return;

    if (msg.type === "session.created" || msg.type === "session.updated") {
      safeSendClient({ type: "stt-ready", model: DASHSCOPE_REALTIME_MODEL });
      return;
    }
    if (msg.type === "conversation.item.input_audio_transcription.text") {
      safeSendClient({ type: "stt-delta", text: msg.text || "" });
      return;
    }
    if (msg.type === "conversation.item.input_audio_transcription.completed") {
      safeSendClient({ type: "stt-final", text: msg.transcript || "" });
      return;
    }
    if (msg.type === "input_audio_buffer.speech_started") {
      safeSendClient({ type: "speech-started" });
      return;
    }
    if (msg.type === "input_audio_buffer.speech_stopped") {
      safeSendClient({ type: "speech-stopped" });
      return;
    }
    if (msg.type === "session.finished") {
      safeSendClient({ type: "session-finished", transcript: msg.transcript || "" });
      return;
    }
    if (msg.type === "error") {
      console.error("[STT-RT] upstream error payload:", JSON.stringify(msg).slice(0, 500));
      safeSendClient({ type: "error", error: msg.error?.message || "stt_failed", detail: msg.error || msg });
    }
  });

  upstreamWs.on("error", (error) => {
    console.error("[STT-RT] upstream error:", error.message);
    safeSendClient({ type: "error", error: error.message || "stt_upstream_error" });
  });

  upstreamWs.on("close", () => {
    if (clientWs.readyState === clientWs.OPEN) clientWs.close();
  });

  clientWs.on("message", (buf) => {
    let msg;
    try { msg = JSON.parse(buf.toString()); } catch { return; }
    if (!msg?.type) return;

    if (msg.type === "append" && msg.audio) {
      safeSendUpstream({
        event_id: randomUUID(),
        type: "input_audio_buffer.append",
        audio: msg.audio,
      });
      return;
    }
    if (msg.type === "commit") {
      safeSendUpstream({ event_id: randomUUID(), type: "input_audio_buffer.commit" });
      return;
    }
    if (msg.type === "clear") {
      safeSendUpstream({ event_id: randomUUID(), type: "input_audio_buffer.clear" });
    }
  });

  clientWs.on("close", () => {
    try { upstreamWs.close(); } catch {}
  });

  clientWs.on("error", () => {
    try { upstreamWs.close(); } catch {}
  });
});

// ==============================================================
// 聊天端点 — SSE 流式返回
// ==============================================================
// ============================================================
// Nox Core 转发
// ============================================================
const NOX_CORE_URL = process.env.NOX_CORE_URL || "http://127.0.0.1:8100";

// ============================================================
// 发给模型前的图片降采样
// ============================================================
// 只作用于"喂给模型"这一步，**磁盘上的原图一个字节都不动** ——
// 糖糖的照片该是什么样还是什么样，压的只是这一次请求的副本。
//
// 为什么值得做：一张 343 KB 的照片转 base64 约 457 KB，按 token 算
// 1000+，每次发图都付一遍。而模型看 1024px 和看 4000px 的识别效果
// 几乎没差别 —— 多出来的像素纯粹在烧钱和等待。
const IMG_MAX_EDGE = Number(process.env.IMG_MAX_EDGE || 1024);
const IMG_QUALITY = Number(process.env.IMG_QUALITY || 80);

let _sharp = null;
async function getSharp() {
  if (_sharp === null) {
    try {
      _sharp = (await import("sharp")).default;
    } catch (e) {
      console.log("[Bridge] sharp 不可用，图片将原样发送:", e.message);
      _sharp = false;
    }
  }
  return _sharp;
}

/**
 * 把图片压到适合喂模型的尺寸。
 * 失败一律返回原图 —— 压缩是优化，不是必需，绝不能因为它让发图整个失败。
 */
async function downscaleForModel(buf) {
  const sharp = await getSharp();
  if (!sharp) return buf;
  try {
    const out = await sharp(buf)
      .rotate()  // 按 EXIF 摆正。手机竖拍的照片不转的话模型看到的是躺着的
      .resize(IMG_MAX_EDGE, IMG_MAX_EDGE, { fit: "inside", withoutEnlargement: true })
      .jpeg({ quality: IMG_QUALITY })
      .toBuffer();
    // 压完反而更大就用原图（小图或已压过的图会这样）
    return out.length < buf.length ? out : buf;
  } catch (e) {
    console.log("[Bridge] 图片降采样失败，用原图:", e.message);
    return buf;
  }
}


// 把 Core 的 SSE 翻译成 bridge 前端认识的格式。
// 两边帧格式不同：Core 用 {type:"text", text:...}，前端要 {type:"text", content:...}。
// 这层翻译让前端一行都不用改。
async function coreMode(req, res, requestId) {
  const { message, images, sessionId, voice, scene, model } = req.body;
  let fullReply = "";
  // 分段边界。落库时进 metadata.segments，前端重新拉库时照着拆回几个气泡。
  const segments = [];
  let curSegment = "";
  // 工具调用名列表。从 Core 的 done 事件里收，存进 metadata.toolsUsed
  // 和 segments 一起落库 —— 前端不管当场看还是翻历史都能展开看。
  let toolsUsed = [];
  // Core 收不到 session_id 时会自己 uuid4 生成一个，并在 done 帧里报出来。
  // 这里必须接住它回传给前端 —— 否则前端下一轮又传空，Core 又建一个新会话，
  // 表现成「每说一句话就多一个 Recents 窗口，而且他永远记不住上一句」。
  // （2026-08-02 实测：十分钟聊出 11 个会话，每个都只有一问一答 2 条消息。）
  let coreSid = sessionId || null;

  // 图片先降采样再转给 Core —— 同样只压这次请求的副本，原图不动。
  // 前端传的可能是 {b64:...} 对象，也可能是字符串。
  const imgs = [];
  for (const b of images || []) {
    const s = typeof b === "string" ? b : (b?.b64 || "");
    if (!s) continue;
    try {
      const raw = s.startsWith("data:") ? s.slice(s.indexOf(",") + 1) : s;
      const buf = await downscaleForModel(Buffer.from(raw, "base64"));
      imgs.push(`data:image/jpeg;base64,${buf.toString("base64")}`);
    } catch {
      imgs.push(s);   // 压不动就原样发
    }
  }

  try {
    const upstream = await fetch(`${NOX_CORE_URL}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: message || "",
        images: imgs,
        session_id: sessionId || undefined,
        // 语音模式：Core 会挂上该情景的通话指令并关掉分段。
        // 这里不需要再拼 VOICE_CALL_INSTRUCTION —— Core 自己有一份。
        voice: !!voice,
        // 通话情景：en 英文（带 ElevenLabs 情绪标签）/ zh 中文。
        // 不传则用 Core 的默认值（英文，保持原有电话行为）
        scene: scene || undefined,
        // 前端模型下拉。Core 认短名（sonnet-5 / opus-4-8 …），
        // 不传就用它自己的默认模型
        model: model || undefined,
      }),
    });

    if (!upstream.ok || !upstream.body) {
      throw new Error(`core responded ${upstream.status}`);
    }

    const reader = upstream.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      // SSE 以空行分帧，最后一段可能不完整，留在缓冲里等下一片
      const frames = buf.split("\n\n");
      buf = frames.pop() || "";

      for (const frame of frames) {
        const line = frame.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch { continue; }

        if (ev.type === "text") {
          fullReply += ev.text;
          curSegment += ev.text;
          res.write(`data: ${JSON.stringify({ type: "text", content: ev.text })}\n\n`);
        } else if (ev.type === "split") {
          // 分段点。content 里仍旧用换行分隔（搜索、导出、Recents 预览都读那一列，
          // 保持纯文本），分段边界另外记进 metadata.segments。
          //
          // ⚠️ 以前这里只写 "\n" 就完事了，理由是「跟 apiMode 一致」——
          // 而 apiMode 2026-07-28 就删干净了。后果是分段信息只活在这条 SSE 里：
          // 当时看是几个气泡，切走再回来重新拉库，就并成了一整段
          // （糖糖 2026-08-04 报的）。
          fullReply += "\n";
          segments.push(curSegment);
          curSegment = "";
          res.write(`data: ${JSON.stringify({ type: "split" })}\n\n`);
        } else if (ev.type === "attachment") {
          // Core 那边的工具（如 send_gallery_image）产生的附带产物。
          // Core 是独立进程，够不着这条 SSE 连接，所以把意图传出来由这里发。
          // kind 才是具体类型；外层 type 恒为 attachment
          if (ev.kind === "image" && ev.url) {
            res.write(`data: ${JSON.stringify({
              type: "image", url: ev.url,
              album: ev.album || "", favorited: !!ev.favorited,
            })}\n\n`);
            saveMessage(sessionId, "assistant", "", {
              image: ev.url, album: ev.album || "", favorited: !!ev.favorited,
            });
            console.log(`[Bridge] Core 发图 ${ev.url}`);
          } else if (ev.kind === "voice" && ev.tts) {
            // 语音条。合成在前端点播放时才发生（POST /api/tts），
            // 这里只把「这句要作为语音发」这个意图和文本传出去
            const display = stripVoiceTags(ev.tts);
            res.write(`data: ${JSON.stringify({
              type: "voice", text: display, tts: ev.tts, zh: ev.zh || "",
            })}\n\n`);
            saveMessage(sessionId, "assistant", "", {
              voice: { en: display, tts: ev.tts, zh: ev.zh || "" },
            });
            console.log(`[Bridge] Core 发语音 ${display.slice(0, 30)}`);
          } else if (ev.kind === "music" && ev.song_id) {
            // 音乐卡片（DESIGN.md 二·02 消息类型 4：封面 + 歌名 + 艺术家 + 进度条）。
            // 只传元数据，音频由前端走上面那个 /api/music/stream 代理拿 ——
            // 几 MB 的音频没必要经过 Core 和这条 SSE。
            const card = {
              songId: String(ev.song_id),
              name: ev.name || "", artist: ev.artist || "", cover: ev.cover || "",
            };
            res.write(`data: ${JSON.stringify({ type: "music", ...card })}\n\n`);
            saveMessage(sessionId, "assistant", "", { music: card });
            console.log(`[Bridge] Core 发音乐卡片 ${card.name || card.songId}`);
          } else if (ev.kind === "meme" && ev.tag) {
            // 表情包。前端 memes.js 按 tag 查图片 URL。
            // 落库 metadata 存 tag，翻历史时也能恢复显示。
            res.write(`data: ${JSON.stringify({ type: "meme", tag: ev.tag })}\n\n`);
            saveMessage(sessionId, "assistant", "", { meme: ev.tag });
            console.log(`[Bridge] Core 发表情 ${ev.tag}`);
          } else if (ev.kind === "order" && ev.order_id) {
            // 待确认单卡片（2026-09-06）。此时**还没有下单** ——
            // 她在卡上点「确认下单」才会真下，走 /api/nox/orders/:oid/confirm。
            //
            // ⚠️ 落库存的是 card 快照，翻历史时卡片还在；但状态是活的，
            // 前端渲染时要拿 order_id 去查当前状态，不能只信这份快照
            // （不然一张早就下过的单，翻回去还是「确认下单」按钮）。
            const order = { orderId: String(ev.order_id), card: ev.card || {} };
            res.write(`data: ${JSON.stringify({ type: "order", ...order })}\n\n`);
            saveMessage(sessionId, "assistant", "", { order });
            console.log(`[Bridge] Core 发待确认单 ${order.orderId}`);
          }
        } else if (ev.type === "error") {
          res.write(`data: ${JSON.stringify({ type: "error", message: ev.message })}\n\n`);
        } else if (ev.type === "done") {
          // 先认领会话 id，后面落库和回传都用它
          if (ev.session_id) coreSid = ev.session_id;
          // 工具调用展示 —— 前端 Chat.jsx 的 ToolsBlock 靠这个渲染。
          // Core 把每次流式里调过的工具名放进 tools_used，bridge 收到 done
          // 之后补发一条 tools 事件，前端就能看到「调了什么工具」。
          if (Array.isArray(ev.tools_used) && ev.tools_used.length) {
            toolsUsed = ev.tools_used;
            res.write(`data: ${JSON.stringify({
              type: "tools", tools: ev.tools_used,
            })}\n\n`);
          }
          // Core 内部有六种结局，失败时它已经把人话放进 message 了
          if (ev.ok === false && ev.message) {
            fullReply += (fullReply ? "\n\n" : "") + ev.message;
            res.write(`data: ${JSON.stringify({ type: "text", content: ev.message })}\n\n`);
          }
          // 用量落库（Console 页统计）。
          // ⚠️ 这段 2026-07-28 删 apiMode 时被一起删掉了，统计从那天起就冻住了，
          // 表面上完全看不出来 —— Console 照常显示，只是数字不再变。
          if (ev.input_tokens > 0 || ev.output_tokens > 0) {
            try {
              dbRun("INSERT INTO usage_log VALUES (?,?,?,?,?,?,?)", [
                new Date().toISOString(),
                ev.input_tokens || 0, ev.output_tokens || 0,
                ev.cached_tokens || 0, ev.cache_write_tokens || 0,
                0,                       // 成本在 /api/usage-stats 按型号现算，见 PRICING
                ev.model || "",
              ]);
            } catch (e) { console.log("[Bridge] usage_log 写入失败:", e.message); }
          }
          console.log(
            `[Bridge] Core ${requestId.slice(0, 6)} | ${ev.outcome} | ${ev.iterations} 轮 | ` +
            `${ev.model || "?"} | 输入 ${ev.input_tokens} 缓存 ${ev.cached_tokens}`
          );
        }
      }
    }
  } catch (e) {
    console.log(`[Bridge] Core mode failed: ${e.message}`);
    const friendly = "我这会儿连不上自己的脑子，等一下再跟我说一次。";
    res.write(`data: ${JSON.stringify({ type: "error", message: friendly })}\n\n`);
    fullReply = fullReply || friendly;
  }

  // 收尾：最后一段没有 split 事件收口，在这里补进去
  if (curSegment) segments.push(curSegment);
  if (fullReply) {
    // metadata 搭积木：segments / toolsUsed 按需往里面加，不为了传一个字段
    // 把另一个空数组也塞进去 —— 前端看到空数组和「没这个 key」行为一样，
    // 白占数据库一行。
    let meta = {};
    if (segments.length > 1) {
      meta.segments = segments.map((s) => s.trim()).filter(Boolean);
    }
    if (toolsUsed.length) {
      meta.toolsUsed = toolsUsed;
    }
    saveMessage(coreSid, "assistant", fullReply,
                Object.keys(meta).length ? meta : "");
  }
  res.write(`data: ${JSON.stringify({ type: "done", sessionId: coreSid })}\n\n`);
  res.end();
}

app.post("/api/chat", async (req, res) => {
  // merged：合并发图（微信 1:1）——前端勾了「发送后合并展示」，存进
  // metadata，重载时聊天页还原成折叠卡而不是一张张缩略图
  const { message, images, sessionId, mode, voice, merged } = req.body;
  if (!message && !images?.length) return res.status(400).json({ error: "empty" });

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");

  const requestId = randomUUID();

  // 聊天里发来的图片：存进相册（album=聊天，小克可收藏/回发）+ 随用户消息落库。
  //
  // ⚠️ 图片必须跟消息一起存 —— 前端每 60 秒轮询一次 /messages 重拉会话，
  // 服务端没图的话，本地带图的版本会被无图版本覆盖，图当场消失
  // （糖糖 2026-08-21 报的）。meta.images 存上传后的 URL 数组（前端缩略图直用）。
  const imgUrls = [];
  if (images?.length) {
    for (const b of images) {
      try {
        const s = typeof b === "string" ? b : (b?.b64 || "");
        const raw = s.startsWith("data:") ? s.slice(s.indexOf(",") + 1) : s;
        const fname = `${Date.now()}-${randomUUID().slice(0, 6)}.jpg`;
        fs.writeFileSync(path.join(uploadDir, fname), Buffer.from(raw, "base64"));
        const url = `/uploads/${fname}`;
        imgUrls.push(url);
        dbRun(`INSERT INTO gallery (id, url, thumbnail, favorited, album, created_at) VALUES (?,?,?,?,?,?)`, [randomUUID(), url, url, 0, "聊天", new Date().toISOString()]);
        autoDescribeImage(url);  // 异步识图写描述，不阻塞对话
      } catch (e) { console.log("[Bridge] chat image save failed:", e.message); }
    }
  }

  // 落库只存用户原话（语音指令不入库，否则前端会把整条隐藏掉）。
  // 只发图不带字的情况也落库（meta.images 非空，saveMessage 不会跳过）。
  const userMeta = imgUrls.length ? { images: imgUrls } : {};
  if (merged && imgUrls.length) userMeta.merged = true;
  if (message || imgUrls.length) {
    saveMessage(sessionId, "user", message || "", Object.keys(userMeta).length ? userMeta : "");
  }

  console.log(`[Bridge] Chat request ${requestId.slice(0,6)} | msg:${(message||"").slice(0,30)} | imgs:${images?.length||0}`);

  // 就一条路：Nox Core :8100。聊天/电话/语音条全走它。
  //
  // 历史上还有两条，都删了：
  //   apiMode —— bridge 里自己拼 prompt + 自己接工具，跟 Core 是两份
  //              平行实现，改一处忘另一处踩过好几次（2026-07-28 删）
  //   agent   —— 转发给她电脑上的 Claude Code，要求 PC 常开。
  //              从来没用过（2026-08-05 删）
  // 请求体里的 mode 字段现在被忽略，留着只为兼容还没刷新的老前端。
  return coreMode(req, res, requestId);
});



// ==============================================================
// 翻译 — 语音通话双语字幕用
// targetLang="zh": EN→ZH（AI 英文回复的中文字幕）
// targetLang="en": ZH→EN（默认）
// ==============================================================
app.post("/api/translate", async (req, res) => {
  try {
    const { text, targetLang = "en" } = req.body;
    if (!text) return res.json({ translation: "" });
    const API_KEY = process.env.OPENROUTER_API_KEY || process.env.API_KEY || "";
    if (!API_KEY) return res.json({ translation: "" });
    const instruction = targetLang === "zh"
      ? `Translate the following English text to Chinese. Output only the translation, nothing else:\n${text.slice(0, 600)}`
      : `Translate the following Chinese text to English. Output only the translation, nothing else:\n${text.slice(0, 600)}`;
    const r = await fetch(`${process.env.OPENROUTER_BASE_URL || process.env.API_BASE || "https://openrouter.ai/api/v1"}/chat/completions`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${process.env.OPENROUTER_API_KEY || process.env.API_KEY || ""}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: process.env.OPENROUTER_MODEL || process.env.API_MODEL || "claude-sonnet-4-6",
        max_tokens: 300,
        messages: [{ role: "user", content: instruction }],
      }),
      signal: AbortSignal.timeout(20000),
    });
    const d = await r.json();
    res.json({ translation: (d.choices?.[0]?.message?.content || "").trim() });
  } catch {
    res.json({ translation: "" });
  }
});

// ==============================================================
// TTS — ElevenLabs (Adam 男声, eleven_v3 + 情绪标签)
// ==============================================================
const ELEVEN_KEY = process.env.ELEVENLABS_API_KEY || process.env.ELEVEN_KEY || "";
const ELEVEN_VOICE = process.env.ELEVENLABS_VOICE_ID || process.env.ELEVEN_VOICE || "gGOcFXG638t1tfyhocY5";
const VOICE_TAG_RE = /\[(?:whining|excited|pouting|softly|sniffling|laughing|eager|pause|whispers?|sighs?|giggles?)\]/gi;
const DASHSCOPE_API_KEY = process.env.DASHSCOPE_API_KEY || process.env.QWEN_ASR_API_KEY || "";
const DASHSCOPE_BASE_URL = (process.env.DASHSCOPE_BASE_URL || process.env.QWEN_ASR_BASE_URL || "https://dashscope.aliyuncs.com/compatible-mode/v1").replace(/\/$/, "");
const DASHSCOPE_ASR_MODEL = process.env.DASHSCOPE_ASR_MODEL || process.env.QWEN_ASR_MODEL || "qwen3-asr-flash";
const DASHSCOPE_FILETRANS_URL = (process.env.DASHSCOPE_FILETRANS_URL || "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription").replace(/\/$/, "");
const DASHSCOPE_TASKS_BASE_URL = (process.env.DASHSCOPE_TASKS_BASE_URL || "https://dashscope.aliyuncs.com/api/v1/tasks").replace(/\/$/, "");
const DASHSCOPE_FILETRANS_MODEL = process.env.DASHSCOPE_FILETRANS_MODEL || "qwen3-asr-flash-filetrans";

function extractTextContent(content) {
  if (!content) return "";
  if (typeof content === "string") return content.trim();
  if (Array.isArray(content)) {
    return content
      .map((item) => {
        if (typeof item === "string") return item;
        if (item?.type === "text" && typeof item.text === "string") return item.text;
        return "";
      })
      .join(" ")
      .trim();
  }
  return "";
}

function inferPublicBaseUrl(req) {
  const forwardedProto = String(req.headers["x-forwarded-proto"] || "").split(",")[0].trim();
  const proto = forwardedProto || req.protocol || "https";
  const host = String(req.headers["x-forwarded-host"] || req.headers.host || "").split(",")[0].trim();
  return host ? `${proto}://${host}` : "";
}

function cleanupSoon(filePath) {
  setTimeout(() => {
    try { fs.unlinkSync(filePath); } catch {}
  }, 60_000);
}

function extractTranscriptionText(payload) {
  if (!payload) return "";
  if (typeof payload.text === "string") return payload.text.trim();
  if (Array.isArray(payload.transcripts)) {
    return payload.transcripts
      .flatMap((transcript) => Array.isArray(transcript?.sentences) ? transcript.sentences : [])
      .map((sentence) => String(sentence?.text || "").trim())
      .filter(Boolean)
      .join(" ")
      .trim();
  }
  return "";
}

app.post("/api/stt", sttUpload.single("audio"), async (req, res) => {
  if (!req.file?.buffer?.length) return res.status(400).json({ error: "audio required" });
  if (!DASHSCOPE_API_KEY) return res.status(500).json({ error: "stt unavailable" });

  try {
    const mimeType = req.file.mimetype || "audio/webm";
    const startedAt = Date.now();
    const publicBaseUrl = inferPublicBaseUrl(req);
    if (!publicBaseUrl) {
      return res.status(500).json({ error: "stt_error", message: "public_origin_unavailable" });
    }

    const ext = mimeType.includes("wav")
      ? ".wav"
      : mimeType.includes("mpeg") || mimeType.includes("mp3")
        ? ".mp3"
        : mimeType.includes("ogg")
          ? ".ogg"
          : mimeType.includes("mp4") || mimeType.includes("m4a")
            ? ".m4a"
            : ".webm";
    const tempName = `stt-${Date.now()}-${randomUUID()}${ext}`;
    const tempPath = path.join(uploadDir, tempName);
    fs.writeFileSync(tempPath, req.file.buffer);
    const publicAudioUrl = `${publicBaseUrl}/uploads/${encodeURIComponent(tempName)}`;

    const submitResponse = await fetch(DASHSCOPE_FILETRANS_URL, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${DASHSCOPE_API_KEY}`,
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
      },
      body: JSON.stringify({
        model: DASHSCOPE_FILETRANS_MODEL,
        input: {
          file_url: publicAudioUrl,
        },
        parameters: {
          channel_id: [0],
          language: "zh",
          enable_itn: true,
          enable_words: false,
        },
      }),
      signal: AbortSignal.timeout(20_000),
    });

    const submitPayload = await submitResponse.json().catch(() => ({}));
    if (!submitResponse.ok) {
      cleanupSoon(tempPath);
      console.error("[STT] DashScope submit failed:", submitResponse.status, JSON.stringify(submitPayload).slice(0, 400));
      return res.status(502).json({ error: "stt_failed", detail: submitPayload });
    }

    const taskId = submitPayload?.output?.task_id;
    if (!taskId) {
      cleanupSoon(tempPath);
      console.error("[STT] DashScope submit missing task_id:", JSON.stringify(submitPayload).slice(0, 400));
      return res.status(502).json({ error: "stt_failed", detail: submitPayload });
    }

    let taskPayload = null;
    const deadline = Date.now() + 45_000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 350));
      const taskResponse = await fetch(`${DASHSCOPE_TASKS_BASE_URL}/${encodeURIComponent(taskId)}`, {
        headers: {
          "Authorization": `Bearer ${DASHSCOPE_API_KEY}`,
          "Content-Type": "application/json",
        },
        signal: AbortSignal.timeout(15_000),
      });
      taskPayload = await taskResponse.json().catch(() => ({}));
      if (!taskResponse.ok) {
        cleanupSoon(tempPath);
        console.error("[STT] DashScope task query failed:", taskResponse.status, JSON.stringify(taskPayload).slice(0, 400));
        return res.status(502).json({ error: "stt_failed", detail: taskPayload });
      }
      const taskStatus = String(taskPayload?.output?.task_status || "").toUpperCase();
      if (taskStatus === "SUCCEEDED" || taskStatus === "FAILED" || taskStatus === "CANCELED" || taskStatus === "UNKNOWN") {
        break;
      }
    }

    const taskStatus = String(taskPayload?.output?.task_status || "").toUpperCase();
    const taskCode = String(taskPayload?.output?.code || "").toUpperCase();
    if (taskCode === "SUCCESS_WITH_NO_VALID_FRAGMENT") {
      cleanupSoon(tempPath);
      return res.json({
        text: "",
        noSpeech: true,
        serverElapsedMs: Date.now() - startedAt,
        model: DASHSCOPE_FILETRANS_MODEL,
      });
    }
    if (taskStatus !== "SUCCEEDED") {
      cleanupSoon(tempPath);
      console.error("[STT] DashScope task not successful:", JSON.stringify(taskPayload).slice(0, 400));
      return res.status(502).json({ error: "stt_failed", detail: taskPayload || { task_id: taskId, task_status: taskStatus || "TIMEOUT" } });
    }

    const transcriptionUrl = taskPayload?.output?.result?.transcription_url;
    if (!transcriptionUrl) {
      cleanupSoon(tempPath);
      console.error("[STT] DashScope missing transcription_url:", JSON.stringify(taskPayload).slice(0, 400));
      return res.status(502).json({ error: "stt_failed", detail: taskPayload });
    }

    const resultResponse = await fetch(transcriptionUrl, {
      signal: AbortSignal.timeout(20_000),
    });
    const resultPayload = await resultResponse.json().catch(() => ({}));
    cleanupSoon(tempPath);
    if (!resultResponse.ok) {
      console.error("[STT] DashScope transcription fetch failed:", resultResponse.status, JSON.stringify(resultPayload).slice(0, 400));
      return res.status(502).json({ error: "stt_failed", detail: resultPayload });
    }

    const text = extractTranscriptionText(resultPayload);
    return res.json({
      text,
      serverElapsedMs: Date.now() - startedAt,
      model: DASHSCOPE_FILETRANS_MODEL,
    });
  } catch (error) {
    console.error("[STT] request error:", error.message);
    return res.status(500).json({ error: "stt_error", message: error.message });
  }
});

// TTS 文本上限。以前是 500，语音条稍长一点就被**悄悄切掉半句** ——
// 不报错，只是话说到一半没了。放宽到 2000，超了在日志里说一声
const TTS_MAX_CHARS = 2000;

// 把 ElevenLabs 的音频流边收边转发给前端。
//
// 以前是 `await r.arrayBuffer()` 先收全再 res.end() —— 整段合成完才开始传，
// 首字出声要多等一到两秒。前端本来就用 MediaSource 在等着喂，是这一层拖了后腿。
async function pipeTts(upstream, res) {
  if (!res.headersSent) {
    res.setHeader("Content-Type", "audio/mpeg");
    // 关掉 Caddy/nginx 的缓冲，否则它会把流重新攒成一坨
    res.setHeader("X-Accel-Buffering", "no");
    res.setHeader("Cache-Control", "no-cache");
  }
  const reader = upstream.body.getReader();
  let bytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    bytes += value.length;
    // 下游断了（她挂了电话 / 切走了）就别再拉了，省 API 额度
    if (!res.write(Buffer.from(value))) {
      await new Promise((resolve) => res.once("drain", resolve));
    }
    if (res.destroyed) { try { await reader.cancel(); } catch {} break; }
  }
  res.end();
  return bytes;
}

app.post("/api/tts", async (req, res) => {
  const { text } = req.body;
  if (!text) return res.status(400).json({ error: "text required" });

  const raw = text.trim();
  if (raw.length > TTS_MAX_CHARS) {
    console.log(`[TTS] 文本 ${raw.length} 字，超过 ${TTS_MAX_CHARS} 会被截断`);
  }
  const withTags = raw.slice(0, TTS_MAX_CHARS);
  // 剥情绪标签的版本，给不认标签的降级模型用
  const clean = raw.replace(VOICE_TAG_RE, "").replace(/\s{2,}/g, " ").trim().slice(0, TTS_MAX_CHARS);

  // ElevenLabs v3 — 保留情绪标签，走 /stream 边合成边下发
  try {
    const r = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${ELEVEN_VOICE}/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "xi-api-key": ELEVEN_KEY },
      body: JSON.stringify({
        text: withTags,
        model_id: "eleven_v3",
        voice_settings: { stability: 0.34, style: 0.84 },
      }),
      signal: AbortSignal.timeout(30000),
    });
    if (r.ok && r.body) {
      const t0 = Date.now();
      const bytes = await pipeTts(r, res);
      console.log(`[TTS] v3 流式 ${bytes}B / ${Date.now() - t0}ms`);
      return;
    }
    console.error("[TTS] ElevenLabs v3 failed:", r.status, "- trying turbo fallback");
  } catch (e) {
    console.error("[TTS] ElevenLabs v3 error:", e.message, "- trying turbo fallback");
  }

  // 一旦开始写响应就不能再降级了 —— 前面已经吐了半截音频出去，
  // 再拼上另一个模型的音频会是两个声音接在一起
  if (res.headersSent) return res.end();

  // 降级: turbo 模型，剥掉情绪标签
  try {
    const r = await fetch(`https://api.elevenlabs.io/v1/text-to-speech/${ELEVEN_VOICE}/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "xi-api-key": ELEVEN_KEY },
      body: JSON.stringify({
        text: clean,
        model_id: "eleven_turbo_v2_5",
        voice_settings: { stability: 0.34, style: 0.84 },
      }),
      signal: AbortSignal.timeout(15000),
    });
    if (r.ok && r.body) {
      await pipeTts(r, res);
      console.log("[TTS] turbo 流式降级");
      return;
    }
    console.error("[TTS] ElevenLabs turbo also failed:", r.status);
  } catch (e) {
    console.error("[TTS] ElevenLabs turbo error:", e.message);
  }

  if (res.headersSent) return res.end();

  // 最终降级: Edge TTS
  //
  // ⚠️ 音色必须按语言选，不能写死。
  // 原来固定 zh-CN-YunxiNeural（中文男声），而语音通话的 EN 场景输出的是英文
  // ——等于一个中文发音人在念英文。而且 ElevenLabs 那把 key 一直是坏的
  // （配的是 key ID 不是 key），所以**每一次通话都走到了这里**，
  // 这个降级路径实际是主路径，藏了很久（2026-08-15 查出来）。
  const enRatio = clean.length
    ? (clean.match(/[A-Za-z]/g) || []).length / clean.length
    : 0;
  // 中文一个字顶一个字符，英文一个词好几个字母，所以阈值不用很高就能分开
  const edgeVoice = enRatio > 0.5 ? "en-US-ChristopherNeural" : "zh-CN-YunxiNeural";
  console.log(`[TTS] 降级到 edge-tts（${edgeVoice}，英文字母占比 ${(enRatio * 100).toFixed(0)}%）`);
  try {
    const proc = spawn("edge-tts", ["--voice", edgeVoice, "--text", clean, "--write-media", "-"], {
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 15000,
    });
    res.setHeader("Content-Type", "audio/mpeg");
    proc.stdout.on("data", chunk => res.write(Buffer.from(chunk)));
    proc.on("close", () => res.end());
    proc.on("error", e => {
      console.error("[TTS] edge-tts error:", e.message);
      if (!res.headersSent) res.status(500).json({ error: e.message }); else res.end();
    });
  } catch (e) { try { res.status(500).json({ error: e.message }); } catch { res.end(); } }
});

// ==============================================================
// Scribe v2 Realtime —— 给前端签发一次性 token
// ==============================================================
//
// 为什么要这个：现在的语音识别是
//     手机(国内) → 东京 bridge → 阿里云(国内) → 东京 → 手机
// 音频白白跨了**两个来回**。实测从东京打 dashscope：TLS 422ms、首字节 610ms，
// 比其他上游慢一个数量级（elevenlabs 从东京只要 TLS 93ms）。
//
// 换成 Scribe v2 Realtime 之后，浏览器**直连** ElevenLabs，
// bridge 只负责发一张 15 分钟的一次性票 —— 音频一个字节都不经过这台机器。
//
// ⚠️ **绝不能把 ELEVENLABS_API_KEY 发给浏览器。** 官方就是为这个提供了
// single-use token：15 分钟过期、用一次就作废。这个端点在 /api/* 下面，
// 已经吃 X-Nox-Token 鉴权，不会被外人拿去签票。
app.post("/api/scribe-token", async (req, res) => {
  if (!ELEVEN_KEY) return res.status(503).json({ error: "ElevenLabs 未配置" });
  try {
    const r = await fetch(
      "https://api.elevenlabs.io/v1/single-use-token/realtime_scribe",
      { method: "POST", headers: { "xi-api-key": ELEVEN_KEY } }
    );
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data?.token) {
      // 如实把上游的错带出去 —— 静默降级会让前端以为是自己的问题
      console.error("[Scribe] 签发失败:", r.status, JSON.stringify(data).slice(0, 200));
      return res.status(502).json({ error: "签发失败", status: r.status, detail: data?.detail });
    }
    console.log("[Scribe] 签发一次性 token（15 分钟）");
    res.json({ token: data.token });
  } catch (e) {
    console.error("[Scribe] 签发异常:", e.message);
    res.status(502).json({ error: e.message });
  }
});

// 健康检查
app.get("/health", (req, res) => {
  res.json({ status: "ok", tts: "available" });
});

// 这里原来有 /api/agents（在线 agent 列表）和 /api/chat-json
// （非 SSE 的 agent 测试口）。本机 agent 下线后两个都没有意义了，
// 2026-08-05 删。

// ==============================================================
// 📔 日记 Diary
// ==============================================================
app.get("/api/diary", (req, res) => {
  const month = req.query.month || new Date().toISOString().slice(0, 7);
  const entries = dbAll(`SELECT * FROM diary WHERE date LIKE ? ORDER BY created_at ASC`, [`${month}%`]);
  // 给每条日记附上评论
  for (const e of entries) {
    e.comments = dbAll("SELECT * FROM diary_comments WHERE diary_id=? ORDER BY created_at ASC", [e.id]);
  }
  const monthlyCount = entries.length;
  res.json({ entries, monthlyCount });
});

app.post("/api/diary", (req, res) => {
  const { date, mood, content, author } = req.body;
  if (!content) return res.status(400).json({ error: "content required" });
  const id = randomUUID();
  const now = new Date().toISOString();
  const time = now.slice(11, 16);
  // author 缺省是糖糖（前端就这么用的）。Core 的 write_diary 会传 Nox
  const who = author === "Nox" ? "Nox" : "糖糖";
  dbRun(`INSERT INTO diary (id, date, time, mood, author, body, created_at) VALUES (?,?,?,?,?,?,?)`, [id, date || now.slice(0,10), time, mood || "平静", who, content, now]);
  // 只给糖糖的日记配 AI 评论 —— 他自己写的那条再触发就是自问自答
  if (who === "糖糖") triggerAiComment(id, content, "diary");
  res.json({ id, ok: true });
});

app.post("/api/diary/:id/comment", async (req, res) => {
  const { content } = req.body;
  if (!content) return res.status(400).json({ error: "content required" });
  const id = randomUUID();
  dbRun(
    `INSERT INTO diary_comments (id, diary_id, author, text, created_at) VALUES (?,?,?,?,?)`,
    [id, req.params.id, "糖糖", content, new Date().toISOString()]
  );

  // 她回复之后他要接着回 —— 和共读批注一样，不用她再去 chat 唤醒。
  // 同步等（几秒），前端拿到 reply 就能直接渲染出来。
  // ⚠️ diary 表的正文列叫 body，不是 content
  const entry = dbAll("SELECT body FROM diary WHERE id=?", [req.params.id])[0];
  const reply = await triggerAiComment(req.params.id, entry?.body || content, "reply");
  res.json({ id, ok: true, reply: reply || null });
});

app.delete("/api/diary/:id", (req, res) => {
  dbRun("DELETE FROM diary_comments WHERE diary_id=?", [req.params.id]);
  dbRun("DELETE FROM diary WHERE id=?", [req.params.id]);
  res.json({ ok: true });
});

// 日记的 AI 批注 —— 走 Core，不依赖她电脑开机。
//
// ⚠️ 2026-08-04 之前这里打的是本机 Agent（agents.size 为 0 就直接 return），
// 所以她电脑关着就永远没有回复；而且绕过 Core 等于第二套人设 ——
// 没有人格、没有记忆、没有工具，跟他平时说话不是一个人
// （教训同 [Care]，见本文件 730 行附近）。
//
// 每篇日记一个独立会话（diary-<日记id>），和主 chat 隔开：
//   · 她在 chat 里提起这事，他是靠**记忆**想起来的，不是靠上下文堆着
//   · 她在同一篇日记下追问，他记得自己在这儿说过什么
async function triggerAiComment(diaryId, content, type = "diary") {
  try {
    // 把这篇日记已有的对话拼进去，他才知道上下文（她可能已经回过他）
    const prior = dbAll(
      "SELECT author, text FROM diary_comments WHERE diary_id=? ORDER BY created_at ASC",
      [diaryId]
    );
    const thread = prior.length
      ? "\n\n这篇日记下你们已经聊过：\n" +
        prior.map((c) => `${c.author}：${c.text}`).join("\n")
      : "";

    const isReply = type === "reply";
    const prompt = isReply
      ? `（系统提示：这不是聊天窗口。糖糖在她的日记下回复了你，你现在接着在日记的评论区回她一句。）\n\n` +
        `日记原文：\n${String(content).slice(0, 800)}${thread}\n\n` +
        `怎么回：\n` +
        `· 1 到 3 句，像在日记本边上接着聊，不是聊天寒暄\n` +
        `· 顺着她刚说的往下接，别复述她的话\n` +
        `· ⚠️ 不要调 write_diary 或任何日记工具 —— 落笔到评论区这一步程序替你做了，` +
        `自己再写一次她会看到两条`
      : `（系统提示：这不是聊天窗口。糖糖刚写了一篇日记，你现在在这篇日记下写一条批注。）\n\n` +
        `日记原文：\n${String(content).slice(0, 800)}${thread}\n\n` +
        `怎么回：\n` +
        `· 2 到 3 句，温暖但别腻，像在她日记边上写的\n` +
        `· 接住她当下的情绪，别评价、别说教\n` +
        `· ⚠️ 不要调 write_diary 或任何日记工具 —— 落笔这一步程序替你做了`;

    const r = await fetch(`${NOX_CORE_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: `diary-${diaryId}`, text: prompt }),
      signal: AbortSignal.timeout(90000),
    });
    const jd = await r.json();
    const text = stripVoiceTags(String(jd?.text || "")).replace(/\|\|\|/g, "\n").trim();
    if (!text) {
      console.log("[Diary] Core 没给出批注:", jd?.outcome || "unknown");
      return null;
    }

    const cid = randomUUID();
    dbRun(
      `INSERT INTO diary_comments (id, diary_id, author, text, created_at) VALUES (?,?,?,?,?)`,
      [cid, diaryId, "小克", text, new Date().toISOString()]
    );
    console.log(`[Diary] 批注已写入 (${isReply ? "回复" : "首条"}):`, text.slice(0, 40));
    return text;
  } catch (e) {
    // 批注失败不影响日记本身 —— 日记已经存好了，这是另一件事
    console.log("[Diary] 批注失败:", e.message);
    return null;
  }
}

// ==============================================================
// 🖼️ 相册 Gallery
// ==============================================================
// 图片进相册后，异步调 Core 识图，把描述写进 gallery.description。
// 这样每张相册图都有「是什么」，Nox 能按内容选图而不是盲发。
// 识图失败不影响图片本身 —— 描述空着，糖糖可以在相册里手动补。
async function autoDescribeImage(url) {
  try {
    const r = await fetch(`${NOX_CORE_URL}/api/describe-image`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
      signal: AbortSignal.timeout(45000),
    });
    const jd = await r.json();
    if (jd?.description) {
      dbRun(`UPDATE gallery SET description=? WHERE url=?`, [jd.description, url]);
      console.log(`[Gallery] 自动识图描述 ${String(url).slice(0, 30)}`);
    }
  } catch (e) {
    console.log("[Gallery] 自动识图失败:", e.message);
  }
}

app.get("/api/gallery/list", (req, res) => {
  const filter = String(req.query.filter || "all").toLowerCase();
  const album = String(req.query.album || "").trim();
  const q = String(req.query.q || "").trim();
  const conds = [];
  const params = [];
  if (["favorites", "favorite", "收藏"].includes(filter)) {
    conds.push("favorited=1");
  }
  if (album) {
    conds.push("album=?");
    params.push(album);
  }
  if (q) {
    // 描述或相册名模糊匹配 —— Nox 按内容选图用
    conds.push("(description LIKE ? OR album LIKE ?)");
    params.push(`%${q}%`, `%${q}%`);
  }
  let sql = "SELECT * FROM gallery";
  if (conds.length) sql += " WHERE " + conds.join(" AND ");
  sql += " ORDER BY created_at DESC LIMIT 100";
  res.json(dbAll(sql, params));
});

// 给相册图片加/改描述。Nox 选图就靠这个字段知道每张是什么。
app.post("/api/gallery/:id/description", (req, res) => {
  const { description } = req.body || {};
  if (typeof description !== "string") return res.status(400).json({ error: "description required" });
  dbRun(`UPDATE gallery SET description=? WHERE id=?`, [description, req.params.id]);
  res.json({ ok: true });
});

app.post("/api/images/upload", upload.single("file"), (req, res) => {
  if (!req.file) return res.status(400).json({ error: "no file" });
  const url = `/uploads/${req.file.filename}`;
  const id = randomUUID();
  dbRun(`INSERT INTO gallery (id, url, thumbnail, favorited, album, created_at) VALUES (?,?,?,?,?,?)`, [id, url, url, 0, "", new Date().toISOString()]);
  autoDescribeImage(url);  // 上传即识图写描述
  res.json({ url, id });
});

app.post("/api/gallery/save", (req, res) => {
  const { imageUrl, album } = req.body;
  if (!imageUrl) return res.status(400).json({ error: "imageUrl required" });
  const id = randomUUID();
  dbRun(`INSERT INTO gallery (id, url, thumbnail, favorited, album, created_at) VALUES (?,?,?,?,?,?)`, [id, imageUrl, imageUrl, 0, album || "", new Date().toISOString()]);
  // 外部图（http://...）识图要从公网读，只对本站上传的图自动识图
  if (imageUrl.startsWith("/uploads/")) autoDescribeImage(imageUrl);
  res.json({ id, ok: true });
});

app.post("/api/gallery/:id/favorite", (req, res) => {
  const { favorited } = req.body;
  dbRun(`UPDATE gallery SET favorited=? WHERE id=?`, [favorited ? 1 : 0, req.params.id]);
  res.json({ ok: true });
});

app.delete("/api/gallery/:id", (req, res) => {
  dbRun(`DELETE FROM gallery WHERE id=?`, [req.params.id]);
  res.json({ ok: true });
});

// 静态文件服务（上传的图片）— 文件名随机且不复用，可长缓存（修相册反复重载慢）
app.use("/uploads", express.static(uploadDir, { maxAge: "30d", immutable: true }));
// 也 serve 前端构建产物
// 默认是 `../frontend/dist`（线上 /root/frontend/dist）。
// ⚠️ 允许环境变量覆盖**只是为了能测** —— 这条兜底路由出过一次
// 「静态资源找不到回了 index.html」的事故（见下面那段），
// 而在测试环境里这个目录根本不存在、整块都不注册，等于测不到。
// 线上不要设这个变量。
const frontendDist = process.env.FRONTEND_DIST
  || path.join(__dirname, "..", "frontend", "dist");
if (fs.existsSync(frontendDist)) {
  // ⚠️ 缓存策略必须分两层，否则「改了没生效」会反复咬人。
  //
  // Vite 给 assets 里的文件名都带了内容哈希（`Music-DfPHUMlF.js`），
  // 内容一变文件名就变 —— 所以这些可以**永久缓存**。
  //
  // 但 `index.html` **绝对不能缓存**：它是那张「哪个哈希是最新的」的地图。
  // 它一旦被缓存，浏览器就永远按旧地图去找旧文件，
  // 我们部署多少次她都看不到新的。
  //
  // 2026-08-10 一下午因为这个白试了三次（上一首按钮、队列、记录页），
  // 每次都以为是代码问题，其实线上产物早就是对的。
  app.use(express.static(frontendDist, {
    maxAge: "1y",
    immutable: true,
    setHeaders(res, filePath) {
      if (filePath.endsWith("index.html")) {
        res.setHeader("Cache-Control", "no-cache, must-revalidate");
      }
    },
  }));
  // 🔴 **静态资源找不到就老实 404，绝不回 index.html。**
  //
  // 2026-09-04 糖糖的 app「发出去的消息和他回复我的都不显示了」，
  // 根因就在这条兜底路由：
  //
  //   她开着 app（已加载 index-旧.js）→ 点进 Chat
  //   → 浏览器取 Chat-旧哈希.js → 那个文件在部署时被换掉了
  //   → 这条兜底把 index.html 当结果返回，**状态码 200**
  //   → 浏览器以为拿到了 JS，解析 `<!doctype html>` → 语法错误
  //   → Chat 那个模块加载失败 → **整页不渲染**
  //
  // 最坑的是它**不报「文件没了」**，报的是一个指不到真因的语法错误。
  // 回 404 的话浏览器会说「chunk 加载失败」，那个错至少能指到地方。
  //
  // ⚠️ 这是**每次部署前端都会踩**的雷，只要她当时开着 app ——
  // 不是那天才有的偶发问题。
  //
  // 判据用后缀不用路径前缀：`/assets/` 之外还有 `/icons/*.png`、
  // `/manifest.webmanifest` 这些，它们同样不该被兜底成 HTML。
  // 而真正的页面路由（`/chat`、`/diary`）是没有后缀的。
  const STATIC_EXT = /\.(js|mjs|css|map|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|otf|webmanifest|json|mp3|mp4|wasm)$/i;
  app.get("*", (req, res, next) => {
    if (req.path.startsWith("/api") || req.path.startsWith("/uploads") || req.path === "/ws" || req.path === "/health") return next();
    if (STATIC_EXT.test(req.path)) {
      // 走到这儿说明 express.static 没找到它 —— 那就是真没有
      return res.status(404).type("text/plain").send("Not Found");
    }
    // 这条兜底路由也要显式设 —— sendFile 不走上面的 setHeaders
    res.setHeader("Cache-Control", "no-cache, must-revalidate");
    res.sendFile(path.join(frontendDist, "index.html"));
  });
}

// ==============================================================
// 📖 共读代理 Co-Reading（转发到本机 co-reading-mcp :3100，token 藏后端）
// 前端只调 /api/reading/*，鉴权由上面的 /api 中间件统一把关
// ==============================================================
// ── eryu 音频代理 ──────────────────────────────────────────────
// 聊天里的音乐卡片要能直接播。eryu 的 /music/* 全都要鉴权，
// 而 <audio src="..."> **带不了 header** —— 所以只能由 bridge 代理一层，
// token 留在后端，前端一个字的密钥都不碰（同下面 reading 那个透明代理）。
const ERYU_URL = process.env.ERYU_URL || "http://127.0.0.1:9090";
const ERYU_TOKEN = process.env.NOX_ERYU_TOKEN || process.env.ERYU_TOKEN || "";

// eryu 的透明代理。给 App 自己的播放器用 —— 它要上报「在听什么」、
// 「听完了」，还要轮询小克的点播队列，而这些接口全在 eryu 上、都要 token。
//
// 只放行白名单里的路径：这是给前端用的通道，不能变成一个能打 eryu
// 任意接口的万能洞（改歌单、删记忆那些不该从这儿走）。
const ERYU_PROXY_ALLOW = new Set([
  "/music/recent/add",       // 开始播放 → 让小克知道在听什么
  "/music/memory",           // 建立记忆条目，listen-complete 要靠它才计数
  "/music/listen-complete",  // 听完 → togetherCount
  "/music/remote",           // 轮询小克的点播
  "/music/search",
  "/music/lyric",
  "/music/recent",
  "/music/stats",
  // 记录页要的（糖糖的设计稿：歌单 / 每日推荐 / 小克给我放过的）
  "/music/playlists",
  "/music/daily",
]);

app.all(/^\/api\/music\/proxy(\/.*)$/, async (req, res) => {
  const sub = req.params[0];
  if (!ERYU_PROXY_ALLOW.has(sub)) {
    return res.status(403).json({ error: "path not allowed" });
  }
  const qi = req.originalUrl.indexOf("?");
  const qs = qi >= 0 ? req.originalUrl.slice(qi) : "";
  try {
    const init = { method: req.method, headers: { "X-Auth-Token": ERYU_TOKEN } };
    if (!["GET", "HEAD"].includes(req.method)) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(req.body || {});
    }
    const r = await fetch(`${ERYU_URL}${sub}${qs}`, init);
    const body = await r.text();
    res.status(r.status);
    res.setHeader("Content-Type", r.headers.get("content-type") || "application/json");
    res.send(body);
  } catch (e) {
    console.error("[Bridge] eryu 代理失败", sub, e.message);
    res.status(502).json({ error: String(e) });
  }
});

// ── 网易云歌单（走 nox-core 的 MCP 工具，不是 eryu）────────────
//
// 共听页的「歌单」那块本来读 eryu 的 /music/playlists，但那里面只有
// 她手动建的一个 Liked（4 首），基本是空的。真正的歌单在她网易云账号里：
// 337 首「喜欢的音乐」、561 首欧美北欧、215 首日语……
//
// ⚠️ netease 是 **MCP 协议**不是 HTTP，上面那个透明代理转发不过去，
// 所以走 nox-core 的 /tool 端点借它的 McpClient。
//
// ⚠️ MCP 返回的是**纯文本**（`ID:705727340 | 名字 | 337 songs (mine)`），
// 得在这里解析成结构化数据，前端拿不了原文。
// ⚠️ URL 必须带 `/mcp` 路径。少了它握手就失败，而报错是一句
// 什么信息都没有的 `TaskGroup (1 sub-exception)` —— nox-core 的 .env
// 就因为漏了这四个字符，八个 netease 工具断了三个月（2026-08-09 查出）。
const NETEASE_MCP = process.env.NETEASE_MCP_URL || "http://127.0.0.1:3456/mcp";

/**
 * 调一次 netease MCP 工具。
 *
 * MCP 的 streamable HTTP 说到底就是 JSON-RPC over POST，Node 直接发即可，
 * 不用绕 nox-core（它也没有通用的工具调用端点）。
 * 每次都重新 initialize —— 这些是低频只读调用，省掉维护会话的复杂度。
 */
async function neteaseCall(tool, args = {}) {
  const headers = {
    "Content-Type": "application/json",
    // 服务端可能用 SSE 回，两种都声明
    Accept: "application/json, text/event-stream",
  };
  const rpc = async (body) => {
    const r = await fetch(NETEASE_MCP, { method: "POST", headers, body: JSON.stringify(body) });
    const text = await r.text();
    // SSE 格式是 `data: {...}`，抠出 JSON 那段
    const line = text.split("\n").find((l) => l.startsWith("data:"));
    return JSON.parse(line ? line.slice(5).trim() : text);
  };

  await rpc({
    jsonrpc: "2.0", id: 1, method: "initialize",
    params: {
      protocolVersion: "2024-11-05", capabilities: {},
      clientInfo: { name: "caelum-bridge", version: "1" },
    },
  });
  const out = await rpc({
    jsonrpc: "2.0", id: 2, method: "tools/call",
    params: { name: tool, arguments: args },
  });
  // 返回是纯文本，包在 result.content[0].text 里
  return out?.result?.content?.[0]?.text || "";
}

// 网易云图床偶尔回 http://，https 页面加载就是 mixed content 直接被拦。
// 出口统一升 https（p*.music.126.net 支持 https，实测无差异）
const httpsCover = (u) => (u || "").replace(/^http:\/\//, "https://");

app.get("/api/music/netease/playlists", async (req, res) => {
  try {
    const text = await neteaseCall("list_my_playlists");
    // ⚠️ MCP 返回的是**纯文本**不是 JSON，得自己解析：
    // ID:705727340 | 茶茶茶茶叶蛋喜欢的音乐 | 337 songs (mine)
    // 2026-08-27 起尾部可能带封面段：… (mine) | https://p1.music.126.net/...
    const playlists = [];
    for (const line of String(text).split("\n")) {
      const m = line.match(/^ID:(\d+)\s*\|\s*(.+?)\s*\|\s*(\d+)\s*songs(?:\s*\((\w+)\))?(?:\s*\|\s*(\S+))?/);
      if (!m) continue;
      playlists.push({
        id: m[1], name: m[2].trim(),
        count: Number(m[3]), owned: m[4] === "mine",
        cover: httpsCover(m[5]),
      });
    }
    res.json({ ok: true, playlists });
  } catch (e) {
    console.error("[Bridge] 取网易云歌单失败", e.message);
    res.status(502).json({ error: String(e) });
  }
});

// 某个歌单里的歌。点开歌单、或者拿封面时用
app.get("/api/music/netease/playlist", async (req, res) => {
  const id = String(req.query.id || "");
  if (!/^\d+$/.test(id)) return res.status(400).json({ error: "bad playlist id" });
  try {
    const text = await neteaseCall("get_playlist_songs", { playlist_id: Number(id) });
    // 1. 不过失去了一点点 - 曾沛慈 (ID:29812781)
    // 2026-08-27 起尾部可能带封面段：… (ID:29812781) | https://p2.music.126.net/...
    const songs = [];
    for (const line of String(text).split("\n")) {
      // 同 history：贪婪吃到最后一个 " - "，防歌名连字符劈裂
      const m = line.match(/^\s*\d+\.\s*(.+)\s*-\s*(.*?)\s*\(ID:(\d+)\)(?:\s*\|\s*(\S+))?/);
      if (!m) continue;
      songs.push({ songId: m[3], name: m[1].trim(), artist: m[2].trim(), cover: httpsCover(m[4]) });
    }
    res.json({ ok: true, songs });
  } catch (e) {
    console.error("[Bridge] 取歌单歌曲失败", e.message);
    res.status(502).json({ error: String(e) });
  }
});

// 本周最听（网易云播放纪录，plays 是次数）。OS Music 右列的「本周最听」用。
// 尾部封面段 2026-08-27 起才有，老部署解析不出就空着
app.get("/api/music/netease/history", async (req, res) => {
  try {
    const limit = Math.min(Math.max(parseInt(req.query.limit) || 10, 1), 30);
    const text = await neteaseCall("get_play_history", { limit, all_time: false });
    // 1. 歌名 - 歌手 (plays:23, ID:456)[ | https://封面]
    const songs = [];
    for (const line of String(text).split("\n")) {
      // ⚠️ 名字段用贪婪匹配吃到**最后一个** " - " —— 歌名里带连字符的
      // （Merry-Go-Round）用懒惰匹配会被从中间劈开
      const m = line.match(/^\s*\d+\.\s*(.+)\s*-\s*(.*?)\s*\(plays:([^,)]*),\s*ID:(\d+)\)(?:\s*\|\s*(\S+))?/);
      if (!m) continue;
      songs.push({
        name: m[1].trim(), artist: m[2].trim(),
        plays: parseInt(m[3]) || 0, songId: m[4], cover: httpsCover(m[5]),
      });
    }
    res.json({ ok: true, songs });
  } catch (e) {
    console.error("[Bridge] 取本周最听失败", e.message);
    res.status(502).json({ error: String(e) });
  }
});

// 换一张能直接塞进 <audio src> 的签名票。前端在卡片渲染时取一次，
// 这样用户点播放时 play() 是**同步调用** —— iOS Safari 只认这种，
// 在 await 之后再 play() 会被当成非用户手势拒掉。
app.get("/api/music/ticket", (req, res) => {
  const id = String(req.query.id || "");
  if (!/^\d+$/.test(id)) return res.status(400).json({ error: "bad song id" });
  res.json({ url: musicStreamPath(id) });
});

app.get("/api/music/stream", async (req, res) => {
  const id = String(req.query.id || "");
  // 只放行纯数字 id —— 这个值会被拼进上游 URL，不校验就是个注入口子
  if (!/^\d+$/.test(id)) return res.status(400).json({ error: "bad song id" });
  try {
    const r = await fetch(`${ERYU_URL}/music/stream?id=${id}`, {
      headers: { "X-Auth-Token": ERYU_TOKEN },
    });
    if (!r.ok) {
      console.warn(`[Bridge] 取音频失败 id=${id} status=${r.status}`);
      return res.status(r.status).json({ error: `upstream ${r.status}` });
    }
    res.setHeader("Content-Type", r.headers.get("content-type") || "audio/mpeg");
    const len = r.headers.get("content-length");
    if (len) res.setHeader("Content-Length", len);
    // 同一首歌在对话里会被反复点，让浏览器自己缓存一小时
    res.setHeader("Cache-Control", "private, max-age=3600");
    // ⚠️ 必须流式转发，不能 await r.arrayBuffer() ——
    // 一首歌几 MB，全读进内存再发等于每次播放都在堆上开一块
    Readable.fromWeb(r.body).pipe(res);
  } catch (e) {
    console.error("[Bridge] 音频代理异常", e);
    res.status(502).json({ error: String(e) });
  }
});

const READING_URL = process.env.READING_URL || "http://127.0.0.1:3100";
const READING_TOKEN = process.env.CO_READING_TOKEN || process.env.READING_TOKEN || "";

// 小克读笔记 / 回页边现在走 Core 的 tools/reading.py。
// 这里只保留给前端阅读器用的透明代理（token 藏在后端）。

app.all(/^\/api\/reading\/(.*)/, async (req, res) => {
  const sub = req.params[0];
  const qi = req.originalUrl.indexOf("?");
  const qs = qi >= 0 ? req.originalUrl.slice(qi) : "";
  const target = `${READING_URL}/api/${sub}${qs}`;
  try {
    const init = { method: req.method, headers: { Authorization: `Bearer ${READING_TOKEN}` } };
    if (!["GET", "HEAD"].includes(req.method)) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(req.body || {});
    }
    const r = await fetch(target, init);
    const body = await r.text();
    res.status(r.status);
    const ct = r.headers.get("content-type");
    if (ct) res.set("Content-Type", ct);
    res.send(body);
  } catch (e) {
    res.status(502).json({ error: "co-reading 代理失败: " + e.message });
  }
});

// ==============================================================
// ✅ 清单 Today
// ==============================================================
app.get("/api/today", (req, res) => {
  // 带 ?date=YYYY-MM-DD 看任意一天（昨天/明天翻页用），缺省今天
  const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || "") ? req.query.date : new Date().toISOString().slice(0, 10);
  res.json(dbAll("SELECT * FROM todos WHERE created_at LIKE ? ORDER BY done ASC, created_at DESC", [`${date}%`]));
});

// ==============================================================
// 时间模型（2026-08-18，Todo-Daily-Planner-设计.md）
//
// 以前 /api/todo/list 转一手去读 GitHub 的 todo.md。**GitHub 退役了** ——
// 前端 todo 是唯一活清单，todo.md 留着当只读存档。
// ==============================================================

//: 中国时区的「今天」和「现在几点」。
//  ⚠️ 别用 toISOString().slice(0,10) —— 那是 UTC 的日期，
//  中国时间 00:30 会被算成昨天，跨天重开就会在半夜错一次。
function cnNow() {
  const s = new Date().toLocaleString("sv-SE", { timeZone: "Asia/Shanghai" });
  const date = s.slice(0, 10);
  // ⚠️ 用 getUTCDay 而不是 getDay：`new Date("2026-08-18")` 解析成 UTC 零点，
  // getDay() 会按**服务器本地时区**换算 —— 服务器不在东八区就会差一天。
  // 日期只是个日期，不该跟着机器的时区漂。
  return { date, hm: s.slice(11, 16), weekday: new Date(`${date}T00:00:00Z`).getUTCDay() || 7 };
}

//: 这周的周一是哪天（ISO 周，周一为一周之始）。weekly_count 按它算配额
function weekStart(dateStr, weekday) {
  // 纯 UTC 算术：日期只是个日期，别让服务器时区插一脚
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - (weekday - 1));
  return d.toISOString().slice(0, 10);
}

//: 这条 weekly_count 这周做了几次
function doneThisWeek(t, now) {
  const start = weekStart(now.date, now.weekday);
  return String(t.done_log || "")
    .split(",").map(s => s.trim()).filter(d => d && d >= start && d <= now.date).length;
}

//: 今天做过了没
function doneToday(t, now) {
  return String(t.done_log || "").split(",").map(s => s.trim()).includes(now.date);
}

//: 这条待办今天命中吗？（Daily Planner 的规则）
//
// ⚠️ 循环任务「做完」= 今天记一笔，**不是永久划掉**。
// 「每天 18:00 背单词」被勾一次就永远消失的话，第二天他就不会再提了。
function hitsToday(t, now) {
  const rep = t.repeat || (t.at ? "once" : "anytime");
  if (rep === "anytime") return false;
  if (rep === "daily") return !doneToday(t, now);
  if (rep === "weekly") {
    return String(t.weekdays || "").split(",").map(s => s.trim()).includes(String(now.weekday))
      && !doneToday(t, now);
  }
  if (rep === "weekly_count") {
    // 「每周 3 次」不绑定具体哪天 —— 只要这周还差着次数就算命中。
    // 配额满了这周就不再出现（她做够了，别再烦她）
    return doneThisWeek(t, now) < (t.times || 1);
  }
  // once：到期日就是今天。没写 due 的老数据退回 created_at 那天
  return (t.due || String(t.created_at || "").slice(0, 10)) === now.date;
}

//: 一条待办的人话描述。前端和早报都用它，省得两边各拼一套
function describeRepeat(t, now) {
  const rep = t.repeat || (t.at ? "once" : "anytime");
  const clock = t.at ? ` ${t.at}` : "";
  if (rep === "daily") return `每天${clock}`;
  if (rep === "weekly") {
    const names = String(t.weekdays || "").split(",").map(s => s.trim()).filter(Boolean)
      .map(n => "一二三四五六日"[Number(n) - 1] || n).join("、");
    return names ? `每周${names}${clock}` : `每周${clock}`;
  }
  if (rep === "weekly_count") {
    return `每周 ${t.times || 1} 次（这周已 ${doneThisWeek(t, now)} 次）${clock}`;
  }
  if (rep === "once") return `${t.due || ""}${clock}`.trim();
  return "随时";
}

// 清单。保持 { ok, sections } 的形状。
//
// ⚠️ **区名必须沿用「进行中 / 近期」** —— 手机上那个 App 的 TodoWidget
// 和桌面端的今日计划都是照这两个键取的（`s["进行中"]`）。
// 换成「今天/明天」这种更贴切的名字，她那两块当场变空，而且不报错。
app.get("/api/todo/list", (req, res) => {
  const now = cnNow();
  const rows = dbAll("SELECT * FROM todos WHERE done = 0 ORDER BY at ASC, created_at DESC");
  const sections = { "进行中": [], "近期": [], "随时": [] };
  // items 给能编辑的前端用（sections 是纯字符串，改不了）。
  // 两个都回，老前端读 sections，新前端读 items
  const items = [];
  for (const t of rows) {
    const rep = t.repeat || (t.at ? "once" : "anytime");
    const when = describeRepeat(t, now);
    const bucket = rep === "anytime" ? "随时" : (hitsToday(t, now) ? "进行中" : "近期");
    sections[bucket].push(rep === "anytime" ? t.text : `${when} ${t.text}`.trim());
    items.push({
      id: t.id, text: t.text, repeat: rep, at: t.at || "",
      weekdays: t.weekdays || "", due: t.due || "", times: t.times || 0,
      when, bucket,
      doneThisWeek: rep === "weekly_count" ? doneThisWeek(t, now) : undefined,
      // 2026-08-27 双端重构：备注 / 分类标签 / 他今天追过没（OS「他的喋喋」模块）
      note: t.note || "", tag: t.tag || "",
      chasedToday: (t.fired_on || "") === now.date,
      createdAt: t.created_at || "",
    });
  }
  res.json({ ok: true, sections, items, source: "local" });
});

// 到点该追的。**nox-core 的 TodoSource 轮询这个。**
//
// 只回「今天命中 + 时刻已过 + 没完成 + 今天还没追过」的条目。
// `fired_on` 是跨天重开的关键：追过就标今天，第二天自动重新可追
// （糖糖 2026-08-18 定的：「一直追」由「明天他还会再来」承担，不是今晚不停）。
app.get("/api/todo/due", (req, res) => {
  const now = cnNow();
  const rows = dbAll("SELECT * FROM todos WHERE done = 0");
  const due = rows.filter(t =>
    t.at && hitsToday(t, now) && t.at <= now.hm && (t.fired_on || "") !== now.date
  );
  res.json({ ok: true, date: now.date, now: now.hm, items: due });
});

// 某一天的待办事件，给「Nox 的一天」的时间轴用。
//
// 只回**带时刻**的两种：记下一件事（created_at）、划掉一件事（last_done_at）。
// ⚠️ 一条待办一天最多贡献一个「完成」事件 —— `last_done_at` 只留最后一次。
// 同一天划掉两次（循环任务）会丢掉前一次，这是有意的取舍：
// 为了完整历史加一张事件表，代价比收益大。
app.get("/api/todo/events", (req, res) => {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || "") ? req.query.date : cnNow().date;
  // ?range=N：顺带回最近 N 天（含当天）的完成计数，OS 的完成热力用。
  // 一发请求代替前端挨天打 N 发；上限 60 天，防着谁手滑传个 3650。
  const range = Math.min(Math.max(parseInt(req.query.range) || 0, 0), 60);
  const rows = dbAll("SELECT id, text, created_at, last_done_at, repeat FROM todos");
  const out = [];
  for (const t of rows) {
    // created_at 存的是 ISO（UTC），要按中国时区判断是不是「今天」
    const madeOn = t.created_at
      ? new Date(t.created_at).toLocaleString("sv-SE", { timeZone: "Asia/Shanghai" }).slice(0, 10)
      : "";
    if (madeOn === date) {
      out.push({ id: t.id, text: t.text, at: t.created_at, kind: "created" });
    }
    const doneOn = t.last_done_at
      ? new Date(t.last_done_at).toLocaleString("sv-SE", { timeZone: "Asia/Shanghai" }).slice(0, 10)
      : "";
    if (doneOn === date) {
      out.push({ id: t.id, text: t.text, at: t.last_done_at, kind: "completed", repeat: t.repeat });
    }
  }
  out.sort((a, b) => String(a.at).localeCompare(String(b.at)));
  let heat;
  if (range > 0) {
    heat = [];
    for (let i = range - 1; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86400000)
        .toLocaleString("sv-SE", { timeZone: "Asia/Shanghai" }).slice(0, 10);
      heat.push({
        date: d,
        count: rows.filter((t) => {
          const on = t.last_done_at
            ? new Date(t.last_done_at).toLocaleString("sv-SE", { timeZone: "Asia/Shanghai" }).slice(0, 10)
            : "";
          return on === d;
        }).length,
      });
    }
  }
  res.json({ ok: true, date, items: out, ...(heat ? { heat } : {}) });
});

// 最近的记忆。Ombre Brain 的 /recent 是 2026-08-18 专门为「给程序读」开的
// 结构化接口（MCP 那几个工具回的是给模型读的文本，解析它等于猜）。
//
// 只回元数据 + 预览，要看全文走 OB 的 trace。
const OMBRE_URL = process.env.OMBRE_URL || process.env.NOX_OB_URL || "http://127.0.0.1:8002";

app.get("/api/memory/recent", async (req, res) => {
  const limit = Math.min(parseInt(req.query.limit) || 20, 100);
  try {
    const r = await fetch(`${OMBRE_URL}/recent?limit=${limit}`, {
      signal: AbortSignal.timeout(10000),
    });
    const d = await r.json();
    res.json({ ok: !!d.ok, items: d.items || [] });
  } catch (e) {
    console.error("[memory] 读最近记忆失败:", e.message);
    res.json({ ok: false, error: e.message, items: [] });
  }
});

// 单条记忆的全文。OB 的 /memory/{id}（2026-08-30，星空页点星看详情用），
// 和 /recent 同一个先例：给程序读的结构化接口，只开在 OB 的本机端口上。
// ⚠️ 必须注册在 /api/memory/recent 之后 —— Express 按注册顺序匹配，
// 不然 "recent" 会被当成 :id 吞掉。
app.get("/api/memory/:id", async (req, res) => {
  try {
    const r = await fetch(`${OMBRE_URL}/memory/${encodeURIComponent(req.params.id)}`, {
      signal: AbortSignal.timeout(10000),
    });
    const d = await r.json();
    if (!r.ok || !d.ok) {
      return res
        .status(r.status === 404 ? 404 : 502)
        .json({ ok: false, error: d.error || `OB 回了 ${r.status}` });
    }
    res.json(d);
  } catch (e) {
    console.error("[memory] 读记忆详情失败:", e.message);
    res.status(502).json({ ok: false, error: e.message });
  }
});

// 共听最近放过什么。eryu 那边每首带 playedAt，正好能落到时间轴上。
// 拿不到就回空 —— 「今天没听歌」和「eryu 挂了」在时间线上都是「没有音乐事件」，
// 但 ok:false 让调用方能在日志里分出来
// ⚠️ eryu 的 /music/* 全都要 X-Auth-Token（见下面那个透明代理的注释）。
// 少这个头会拿到 401，而 401 的 body 不是 songs，表现成「今天没听歌」
app.get("/api/music/recent", async (req, res) => {
  try {
    const r = await fetch(`${ERYU_URL}/music/recent`, {
      headers: { "X-Auth-Token": ERYU_TOKEN },
      signal: AbortSignal.timeout(8000),
    });
    const d = await r.json();
    res.json({ ok: true, songs: d.songs || [] });
  } catch (e) {
    console.error("[music] 读播放历史失败:", e.message);
    res.json({ ok: false, error: e.message, songs: [] });
  }
});

// ==============================================================
// 🎬 共影观影状态 / 记录（2026-08-22，技术方案 v2.0 的 P1）
//
// 在这之前共影是一座孤岛：Movies.jsx 自己拼一段字符串塞进 /api/chat，
// **Nox 本人不知道有这回事** —— 他在别的对话里提不起「昨天我们看的那部」，
// Care 也不知道她在看片，可能在高潮处推一条消息进来。
// 这三个端点就是那座桥。
// ==============================================================

//: 心跳超过这个岁数就当她已经走了（关了浏览器不会发 ended）。
//  可配是为了让测试能验"过期"这条分支 —— 它最容易悄悄坏：
//  过期判断错了的表现是"她明明在看片，Care 却照常推消息"，线上看不出来
const WATCH_STALE_MS = Number(process.env.WATCH_STALE_MS) || 90 * 1000;

//: 当前这一场（没结束 + 心跳够新）。没有就 null
function liveWatch() {
  const row = dbAll(
    "SELECT * FROM watch_sessions WHERE ended_at IS NULL ORDER BY last_seen_at DESC LIMIT 1"
  )[0];
  if (!row) return null;
  const age = Date.now() - new Date(row.last_seen_at || 0).getTime();
  // ⚠️ 过期的**不删也不改**：她可能只是合上电脑去倒杯水。
  // 这里只判断"算不算在看"，清理交给下一次心跳或她自己点结束
  return age <= WATCH_STALE_MS ? row : null;
}

// 播放器的心跳。state: playing | paused | ended
app.post("/api/watch/state", (req, res) => {
  const b = req.body || {};
  const id = String(b.session_id || "").trim();
  if (!id) return res.status(400).json({ error: "session_id required" });
  const state = ["playing", "paused", "ended"].includes(b.state) ? b.state : "playing";
  const now = new Date().toISOString();

  const existing = dbAll("SELECT id, started_at FROM watch_sessions WHERE id=?", [id])[0];
  if (!existing) {
    dbRun(
      `INSERT INTO watch_sessions
       (id, title, episode, mode, url, duration_s, position_ms, play_state, started_at, last_seen_at, ended_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?)`,
      [id, String(b.title || "").slice(0, 200), String(b.episode || "").slice(0, 120),
       // stream=代理拉流 / iframe=番剧 / local=她硬盘里的片子（2026-08-22）
       ["iframe", "local"].includes(b.mode) ? b.mode : "stream",
       String(b.url || "").slice(0, 500),
       Math.max(0, parseInt(b.duration_s) || 0), Math.max(0, parseInt(b.position_ms) || 0),
       state, now, now, state === "ended" ? now : null]
    );
  } else {
    // 已经存在就只动会变的那几列 —— started_at 是这一场的开头，不许被心跳覆盖
    dbRun(
      `UPDATE watch_sessions SET position_ms=?, play_state=?, last_seen_at=?, ended_at=?
       WHERE id=?`,
      [Math.max(0, parseInt(b.position_ms) || 0), state, now,
       state === "ended" ? now : null, id]
    );
  }
  res.json({ ok: true, watching: state !== "ended" });
});

// 她现在在不在看片。**Core 的 Care 抑制读这个。**
//
// 回 watching:false 的两种情况要分开：没有这一场（她没看）、
// 心跳过期（她可能只是走开了）—— 后者带 stale_session，别当成"从来没看过"
app.get("/api/watch/state", (req, res) => {
  const live = liveWatch();
  if (live) {
    return res.json({
      ok: true, watching: true,
      session: {
        id: live.id, title: live.title, episode: live.episode, mode: live.mode,
        position_ms: live.position_ms, duration_s: live.duration_s,
        play_state: live.play_state, started_at: live.started_at,
      },
    });
  }
  const stale = dbAll(
    "SELECT id, title, last_seen_at FROM watch_sessions WHERE ended_at IS NULL ORDER BY last_seen_at DESC LIMIT 1"
  )[0];
  res.json({ ok: true, watching: false, session: null, stale_session: stale || null });
});

// 「一起看过 XXX」。新的在前
app.get("/api/watch/history", (req, res) => {
  const limit = Math.min(parseInt(req.query.limit) || 20, 100);
  // 带上 url 是为了「再看一遍」—— 前端拿它直接重新 import。
  // 心跳里带的就是她当初贴的那个链接
  const rows = dbAll(
    `SELECT id, title, episode, mode, url, duration_s, position_ms, started_at, ended_at
     FROM watch_sessions ORDER BY started_at DESC LIMIT ?`, [limit]
  );
  res.json({ ok: true, items: rows });
});

// ══════════════════════════════════════════════════════════════
// 🎟️ 电影票根（2026-09-04）
//
// 糖糖：「看完电影后共同看过的电影会生成一张票根」。
//
// 流程：一场看完（play_state=ended）→ 前端弹窗问她这是什么类型 →
// 选「电影」就填片名和影评 → 这儿去维基拉元信息 → 存一张票根。
//
// ⚠️ **只有电影出票**（她定的）。其余类型只记 kind，不生成票根，
// 但也要记下来 —— 记了才知道「这场问过了」，不会每次打开都再弹一次。
// ══════════════════════════════════════════════════════════════

const TICKET_KINDS = ["movie", "tv", "anime", "variety"];

//: 看够这么久才问她「这是什么」。点开两分钟就关的不算一场。
//: ⚠️ 允许环境变量覆盖**只是为了能测** —— 测试造的场次
//: 开始和结束只差几毫秒，不调低就永远测不到那条流程。线上不要设。
const TICKET_MIN_MINUTES = Number(process.env.TICKET_MIN_MINUTES ?? 10);

/** 还没问过「这是什么」的、已经看完的场次。前端据此决定弹不弹。 */
app.get("/api/tickets/pending", (req, res) => {
  //: 只看**真看完**的（ended），而且至少看了 10 分钟 ——
  //: 点开两分钟就关的不算一场，弹窗问她纯属打扰
  //: （同 shared_activities.py 里共影那条 ≥10 分钟的线）
  const rows = dbAll(
    `SELECT w.id, w.title, w.mode, w.started_at, w.ended_at
     FROM watch_sessions w
     LEFT JOIN movie_tickets t ON t.session_id = w.id
     WHERE w.play_state = 'ended' AND w.ended_at IS NOT NULL
       AND t.session_id IS NULL
     ORDER BY w.ended_at DESC LIMIT 5`
  ).filter(r => longEnough(r.started_at, r.ended_at, TICKET_MIN_MINUTES));
  res.json({ ok: true, items: rows });
});

/** 生成一张票根（或只登记类型）。 */
app.post("/api/tickets", async (req, res) => {
  const b = req.body || {};
  const sid = String(b.session_id || "").trim();
  const kind = TICKET_KINDS.includes(b.kind) ? b.kind : "";
  if (!sid) return res.status(400).json({ error: "session_id required" });
  if (!kind) return res.status(400).json({ error: `kind must be one of ${TICKET_KINDS}` });

  const sess = dbAll("SELECT * FROM watch_sessions WHERE id=?", [sid])[0];
  if (!sess) return res.status(404).json({ error: "没有这场" });

  //: 她填的片名优先；没填就用共影记的（本地文件是文件名，丑但总比空好）
  const title = String(b.title || sess.title || "").trim().slice(0, 200);
  const review = String(b.review || "").trim().slice(0, 2000);

  //: 🔴 **只有电影去拉元信息。** 电视剧/番剧/综艺现在只登记类型，
  //: 拉了也没地方印，白等一次网络往返
  let meta = null;
  if (kind === "movie" && title) {
    //: 拉不到不是错误 —— lookupMovie 永远 resolve，票根照样出
    meta = await lookupMovie(title);
  }

  const from = sess.started_at || null;
  const to = sess.ended_at || null;
  const mins = from && to
    ? Math.max(0, Math.round((new Date(to) - new Date(from)) / 60000)) : 0;

  dbRun(
    `INSERT OR REPLACE INTO movie_tickets
     (session_id, kind, title, review, meta_json,
      watched_from, watched_to, watched_minutes, created_at)
     VALUES (?,?,?,?,?,?,?,?,?)`,
    [sid, kind, title, review, meta ? JSON.stringify(meta) : null,
     from, to, mins, new Date().toISOString()]
  );

  res.json({ ok: true, ticket: readTicket(sid) });
});

/** 票根列表。**只回电影** —— 其余类型没有票根可看。 */
app.get("/api/tickets", (req, res) => {
  const limit = Math.min(parseInt(req.query.limit) || 50, 200);
  const rows = dbAll(
    `SELECT session_id FROM movie_tickets WHERE kind='movie'
     ORDER BY watched_to DESC LIMIT ?`, [limit]
  );
  res.json({ ok: true, items: rows.map(r => readTicket(r.session_id)) });
});

/** 一张票根的完整形态（meta_json 解开）。 */
function readTicket(sid) {
  const t = dbAll("SELECT * FROM movie_tickets WHERE session_id=?", [sid])[0];
  if (!t) return null;
  let meta = null;
  try { meta = t.meta_json ? JSON.parse(t.meta_json) : null; } catch { /* 存坏了就当没有 */ }
  return {
    session_id: t.session_id,
    kind: t.kind,
    title: t.title,
    review: t.review,
    meta,
    watched_from: t.watched_from,
    watched_to: t.watched_to,
    watched_minutes: t.watched_minutes,
    created_at: t.created_at,
  };
}

// 追过了，标记一下（今天不再重复追）。Core 追完调它。
app.post("/api/todo/fired", (req, res) => {
  const { id } = req.body || {};
  if (!id) return res.status(400).json({ error: "id required" });
  dbRun("UPDATE todos SET fired_on = ? WHERE id = ?", [cnNow().date, id]);
  res.json({ ok: true });
});

// 工作台状态。Core 内部聚合（attention / wakeups / 今日开口额度），
// 这里只是转一手给前端/桌面端轮询。拿不到就返回 ok:false ——
// 工作台只是展示，不能因为 Core 抽风让页面白屏。
app.get("/api/nox/state", async (req, res) => {
  try {
    const r = await fetch(`${NOX_CORE_URL}/api/nox/state`, { signal: AbortSignal.timeout(8000) });
    res.json(await r.json());
  } catch (e) {
    console.error("[nox-state] 读工作台状态失败:", e.message);
    res.json({ ok: false, error: e.message });
  }
});

// ---------------------------------------------------------------- 待确认单
//
// 🔴 前端点「确认下单」走这里（2026-09-06，见 Caelum-点单确认卡-设计.md）。
//
// 为什么要经 bridge 转一道：R7「前端只打 bridge」+ R3「跨进程走 REST」。
// 而且商家的 MCP token 在 nox-core 的 .env 里，bridge 拿不到也不该拿到 ——
// 这边只做透明代理，一个业务判断都不做。
//
// ⚠️ 下单是**不可逆**的动作，所以超时给得比别的宽（30s）：
// 超时重试会下两单，而这里宁可等。幂等由 nox-core 的 claim() 保证。
const noxOrderProxy = (path, method, timeoutMs) => async (req, res) => {
  const oid = String(req.params.oid || "");
  if (!/^ord-[a-z0-9]{6,}$/i.test(oid)) {
    return res.status(400).json({ ok: false, error: "订单号格式不对" });
  }
  try {
    const r = await fetch(`${NOX_CORE_URL}/api/nox/orders/${oid}${path}`, {
      method,
      signal: AbortSignal.timeout(timeoutMs),
    });
    res.status(r.status).json(await r.json());
  } catch (e) {
    console.error(`[nox-order] ${method} ${oid}${path} 失败:`, e.message);
    // 🔴 措辞要让她知道**这单可能已经下了** —— 超时不等于没下成。
    // 说成「失败了」的话她会再点一次，那才是真出事
    res.status(504).json({
      ok: false,
      error: e.message,
      detail: "没能确认结果，先别重复点。去瑞幸 App 看一眼订单列表。",
    });
  }
};
app.get("/api/nox/orders/:oid", noxOrderProxy("", "GET", 8000));
// 付款链接。⚠️ 只透传，**不落库不打日志** —— 它是支付凭证
app.get("/api/nox/orders/:oid/pay", noxOrderProxy("/pay", "GET", 8000));
app.post("/api/nox/orders/:oid/confirm", noxOrderProxy("/confirm", "POST", 30000));
app.post("/api/nox/orders/:oid/cancel", noxOrderProxy("/cancel", "POST", 8000));

// 可切换的模型清单 + 当前系统默认（Models 设置页）。
// 切换不走这里 —— 聊天请求带 model 短名，coreMode 本来就透传
app.get("/api/nox/models", async (req, res) => {
  try {
    const r = await fetch(`${NOX_CORE_URL}/api/nox/models`, { signal: AbortSignal.timeout(8000) });
    res.json(await r.json());
  } catch (e) {
    console.error("[nox-models] 读模型清单失败:", e.message);
    res.json({ ok: false, error: e.message });
  }
});

// World 页（户型图）：天 / 家里的设备 / 她在不在家 / 她在忙什么，一次取齐。
// ⚠️ 超时给到 12 秒 —— 这条要串 ha-mcp 拿清单 + 并发查 HA 状态，
// 比别的接口慢，8 秒会在设备多的时候偶发超时（那会让整张图无谓地黑掉）。
app.get("/api/nox/world", async (req, res) => {
  try {
    const r = await fetch(`${NOX_CORE_URL}/api/nox/world`, { signal: AbortSignal.timeout(12000) });
    res.json(await r.json());
  } catch (e) {
    console.error("[nox-world] 读世界状态失败:", e.message);
    res.json({ ok: false, error: e.message });
  }
});

// 他手上全部注册的工具（Studio → Tools 工具墙）与插口探活（Studio → MCP）。
// ⚠️ Core 那边加了接口这里就得加一条 —— 逐路由代理，通配会让「Core 挂了」
// 看起来像 404
app.get("/api/nox/tools", async (req, res) => {
  try {
    const r = await fetch(`${NOX_CORE_URL}/api/nox/tools`, { signal: AbortSignal.timeout(8000) });
    res.json(await r.json());
  } catch (e) {
    console.error("[nox-tools] 读工具清单失败:", e.message);
    res.json({ ok: false, error: e.message });
  }
});

app.get("/api/nox/integrations", async (req, res) => {
  try {
    const r = await fetch(`${NOX_CORE_URL}/api/nox/integrations`, { signal: AbortSignal.timeout(15000) });
    res.json(await r.json());
  } catch (e) {
    console.error("[nox-integrations] 读插口状态失败:", e.message);
    res.json({ ok: false, error: e.message });
  }
});

// 他此刻的内心驱动力（2026-08-29）。Attention 心跳线要画的就是这个。
//
// ⚠️ **bridge 是逐路由代理，不是通配。** Core 那边加了接口、这里不加，
// 前端拿到的是 bridge 自己的 404 —— 而那看起来像「Core 挂了」，
// 不像「少写了一行」。加 Core 接口时记得回来加这一条。
app.get("/api/nox/resonance", async (req, res) => {
  try {
    const r = await fetch(`${NOX_CORE_URL}/api/nox/resonance`, { signal: AbortSignal.timeout(8000) });
    res.json(await r.json());
  } catch (e) {
    console.error("[nox-resonance] 读内心状态失败:", e.message);
    res.json({ ok: false, error: e.message });
  }
});

// 他这一天里每一次动念，逐条带时刻（2026-08-29）。心跳线上的尖峰。
app.get("/api/nox/pulse", async (req, res) => {
  try {
    const since = Number(req.query.since) || 0;
    const r = await fetch(`${NOX_CORE_URL}/api/nox/pulse?since=${since}`, {
      signal: AbortSignal.timeout(8000),
    });
    res.json(await r.json());
  } catch (e) {
    console.error("[nox-pulse] 读脉搏失败:", e.message);
    res.json({ ok: false, error: e.message });
  }
});

// 同上，但是推的。**这条不能用 res.json() —— 它是一条不会结束的流。**
//
// 🔴 三个 header 一个都不能少：
//   text/event-stream   浏览器才当它是 SSE
//   no-cache            不然中间层会缓存住第一帧
//   X-Accel-Buffering   Caddy/nginx 会把流攒成一坨再发，表现是"心跳一顿一顿"
//
// ⚠️ 客户端关页面时要把上游也断掉，否则 Core 那边每开一次页面
// 就留一个永远读不完的生成器 —— 一天下来能攒出几十条。
app.get("/api/nox/pulse/stream", async (req, res) => {
  const since = Number(req.query.since) || 0;
  const ac = new AbortController();
  req.on("close", () => ac.abort());

  try {
    const upstream = await fetch(
      `${NOX_CORE_URL}/api/nox/pulse/stream?since=${since}`,
      { signal: ac.signal },
    );
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("X-Accel-Buffering", "no");
    for await (const chunk of upstream.body) res.write(chunk);
    res.end();
  } catch (e) {
    if (ac.signal.aborted) return;          // 她关页面了，不是错
    console.error("[nox-pulse] 脉搏流断了:", e.message);
    //: 已经开始写流之后就不能再发 JSON 了（头已经出去了），
    //: 只能在流里说一句然后收尾
    try {
      res.write(`data: ${JSON.stringify({ type: "error", error: e.message })}\n\n`);
      res.end();
    } catch {}
  }
});

// 生理期 → World Model（2026-08-19）。
//
// HealthKit 那条同步坏了：`health.db` 的 menstrual 表上一条停在 7-24，
// 而且 flow_level 被快捷指令写成了一堆换行。所以经期改由 Core 的
// World Model 收 —— 她可以在 App 里填，也可以直接跟他说，写的是同一个地方。
//
// ⚠️ **体重不在这儿。** 记体重只有一条路：Core 的 `log_weight` 工具 →
// 本文件的 `/api/diet/weight` → `body_weight` 表。
// 2026-08-19 差点又开一条并行的，见 `nox-core/tools/record.py` 开头。
app.post("/api/health/record/period", async (req, res) => {
  try {
    const r = await fetch(`${NOX_CORE_URL}/api/nox/record/period`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {}),
      signal: AbortSignal.timeout(15000),
    });
    res.status(r.status).json(await r.json());
  } catch (e) {
    console.error("[record] 写生理期失败:", e.message);
    res.status(502).json({ ok: false, error: e.message });
  }
});

// 某类事实的历史（体重曲线 / 周期记录）
app.get("/api/health/facts", async (req, res) => {
  const type = String(req.query.type || "").trim();
  const days = Math.min(parseInt(req.query.days) || 30, 365);
  if (!type) return res.status(400).json({ error: "type required" });
  try {
    const r = await fetch(
      `${NOX_CORE_URL}/api/nox/facts?type=${encodeURIComponent(type)}&days=${days}`,
      { signal: AbortSignal.timeout(15000) }
    );
    res.json(await r.json());
  } catch (e) {
    console.error("[facts] 读事实失败:", e.message);
    res.json({ ok: false, error: e.message, items: [] });
  }
});

// 「Nox 的一天」—— 当日活动的只读投影（Nox 的一天 架构设计文档.md v1.1）。
// Core 那边做多源聚合，这里只转一手。
// 拿不到就回 ok:false + 空事件：时间线是展示，不能因为 Core 抽风让整页白掉。
app.get("/api/nox/day", async (req, res) => {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || "") ? req.query.date : "";
  try {
    const r = await fetch(
      `${NOX_CORE_URL}/api/nox/day${date ? `?date=${date}` : ""}`,
      { signal: AbortSignal.timeout(12000) }
    );
    res.json(await r.json());
  } catch (e) {
    console.error("[nox-day] 读一天时间线失败:", e.message);
    res.json({ ok: false, error: e.message, events: [], summary: {}, sources: {} });
  }
});

// 这里原来有 todoSync() —— 把待办的增/删/改转给 Core 去写 GitHub 的 todo.md。
// 2026-08-18 GitHub 退役（Todo-Daily-Planner-设计.md），三个调用点全部改成
// 直接读写本地表，这个函数就没人用了，删。
// todo.md 留在仓库里当只读存档，不再有任何代码写它。

// 按关键词划掉。Core 的 complete_todo 工具走这条 ——
// 糖糖说「运动做完了」，他就该能划掉，不用她自己去点。
//
// 2026-08-18：以前这里转给 Core 去改 GitHub 的 todo.md，**GitHub 退役了**，
// 现在直接改本地表（前端 todo 是唯一活清单）。
//: 划掉一条。**循环任务只记一笔，不永久划掉。**
function markDone(t) {
  const now = cnNow();
  const rep = t.repeat || (t.at ? "once" : "anytime");
  const log = String(t.done_log || "").split(",").map(s => s.trim()).filter(Boolean);
  if (!log.includes(now.date)) log.push(now.date);
  // 只留最近 60 条，weekly_count 只看本周，daily 只看今天
  const trimmed = log.slice(-60).join(",");

  const nowIso = new Date().toISOString();
  if (rep === "once" || rep === "anytime") {
    dbRun("UPDATE todos SET done = 1, done_log = ?, last_done_at = ? WHERE id = ?",
          [trimmed, nowIso, t.id]);
    return { repeat: rep, closed: true };
  }
  // 循环的：记一笔就行，它明天/下周还会回来
  dbRun("UPDATE todos SET done_log = ?, last_done_at = ?, fired_on = '' WHERE id = ?",
        [trimmed, nowIso, t.id]);
  return { repeat: rep, closed: false, doneThisWeek: doneThisWeek({ ...t, done_log: trimmed }, now) };
}

app.post("/api/todo/complete", (req, res) => {
  const { keyword } = req.body;
  if (!keyword) return res.status(400).json({ error: "keyword required" });
  const hit = dbAll(
    "SELECT * FROM todos WHERE done = 0 AND text LIKE ? ORDER BY created_at DESC LIMIT 1",
    [`%${keyword}%`]
  )[0];
  if (!hit) return res.json({ ok: false, error: `清单里没找到「${keyword}」` });
  const r = markDone(hit);
  res.json({ ok: true, id: hit.id, text: hit.text, ...r });
});

app.post("/api/today", (req, res) => {
  // repeat/at/weekdays/due 是 2026-08-18 的时间模型，都可选：
  // 什么都不给就是「随时」档，只进清单、不占提醒名额
  // note/tag 是 2026-08-27 双端重构加的：条目备注与轻量分类
  const { text, time, date, repeat, at, weekdays, due, times, note, tag } = req.body;
  if (!text) return res.status(400).json({ error: "text required" });
  const id = randomUUID();
  // 指定日期时把 created_at 锚到那天（比如在"明天"页加的计划就落在明天）
  let createdAt = new Date().toISOString();
  if (/^\d{4}-\d{2}-\d{2}$/.test(date || "")) createdAt = `${date}T${createdAt.slice(11)}`;

  const clock = /^\d{1,2}:\d{2}$/.test(at || time || "") ? (at || time) : "";
  const rep = ["once", "daily", "weekly", "weekly_count", "anytime"].includes(repeat)
    ? repeat
    : (clock ? "once" : "anytime");

  dbRun(
    `INSERT INTO todos (id, text, time, done, created_at, synced, repeat, at, weekdays, due, times, note, tag)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    [id, text, time || "", 0, createdAt, 0, rep, clock,
     weekdays || "", due || (rep === "once" ? createdAt.slice(0, 10) : ""),
     rep === "weekly_count" ? Math.max(1, parseInt(times) || 1) : 0,
     String(note || "").slice(0, 2000), String(tag || "").trim().slice(0, 24)]
  );
  res.json({ id, ok: true, repeat: rep, at: clock, times: parseInt(times) || 0 });
});

app.patch("/api/today/:id", (req, res) => {
  const { done } = req.body;
  const row = dbAll("SELECT * FROM todos WHERE id=?", [req.params.id])[0];
  if (!row) return res.status(404).json({ error: "no such todo" });

  if (!done) {
    // 取消勾选：把今天那一笔从 done_log 里撤掉
    const now = cnNow();
    const log = String(row.done_log || "").split(",").map(s => s.trim())
      .filter(d => d && d !== now.date).join(",");
    dbRun("UPDATE todos SET done = 0, done_log = ? WHERE id = ?", [log, req.params.id]);
    return res.json({ ok: true, done: false });
  }
  res.json({ ok: true, ...markDone(row) });
});

app.delete("/api/today/:id", (req, res) => {
  dbRun(`DELETE FROM todos WHERE id=?`, [req.params.id]);
  res.json({ ok: true });
});

// ==============================================================
// 🥗 健身饮食记录 Diet（M2 2026-08-05）
// 糖糖的饮食/运动/体重，PRD：WorkBuddy/健身饮食记录系统PRD.md
// ==============================================================

// ---- 食物库 ----

app.get("/api/diet/foods", (req, res) => {
  const q = (req.query.q || "").trim();
  if (!q) return res.json(dbAll("SELECT * FROM foods ORDER BY category, name LIMIT 50"));
  const like = `%${q}%`;
  res.json(dbAll(
    `SELECT * FROM foods WHERE name LIKE ? OR aliases LIKE ? ORDER BY
     CASE WHEN name = ? THEN 0 WHEN name LIKE ? THEN 1 ELSE 2 END, category
     LIMIT 20`,
    [like, like, q, `${q}%`]
  ));
});

app.post("/api/diet/foods", (req, res) => {
  const { name, aliases, category, cal_100g, protein_100g, carbs_100g, fat_100g, is_cooked } = req.body || {};
  if (!name || cal_100g == null) return res.status(400).json({ error: "name 和 cal_100g 必填" });
  dbRun(
    `INSERT OR REPLACE INTO foods (name, aliases, category, cal_100g, protein_100g, carbs_100g, fat_100g, source, is_cooked)
     VALUES (?,?,?,?,?,?,?,?,?)`,
    [name, aliases || "", category || "", cal_100g, protein_100g || 0, carbs_100g || 0, fat_100g || 0, "custom", is_cooked != null ? is_cooked : 1]
  );
  // 不用 last_insert_rowid：sql.js 的 PRAGMA 模式偶有竞态，显式查最稳
  const row = dbAll("SELECT id FROM foods WHERE name=?", [name])[0];
  res.json({ id: row?.id || 0, ok: true });
});

// 最近食用 / 常吃的食物（按 meal_items 出现频率排序）
app.get("/api/diet/foods/recent", (_req, res) => {
  // 最近 30 天出现过的食物，按使用次数降序
  const rows = dbAll(
    `SELECT f.*, COUNT(mi.id) AS use_count, MAX(mi.created_at) AS last_used
     FROM foods f
     JOIN meal_items mi ON mi.food_id = f.id
     JOIN meals m ON m.id = mi.meal_id
     WHERE m.meal_date >= date('now','localtime','-30 days')
     GROUP BY f.id
     ORDER BY use_count DESC, last_used DESC
     LIMIT 30`
  );
  res.json(rows);
});

// ---- 估量换算 ----

app.get("/api/diet/conversions", (req, res) => {
  res.json(dbAll("SELECT * FROM unit_conversions ORDER BY unit_name, food_category"));
});

app.post("/api/diet/conversions", (req, res) => {
  const { unit_name, food_category, food_id, est_grams, note } = req.body || {};
  if (!unit_name || est_grams == null) return res.status(400).json({ error: "unit_name 和 est_grams 必填" });
  dbRun(
    `INSERT INTO unit_conversions (unit_name, food_category, food_id, est_grams, note) VALUES (?,?,?,?,?)`,
    [unit_name, food_category || "", food_id || null, est_grams, note || ""]
  );
  const row = dbAll("SELECT id FROM unit_conversions WHERE unit_name=? AND (food_id=? OR (food_id IS NULL AND ? IS NULL)) ORDER BY id DESC LIMIT 1",
    [unit_name, food_id || null, food_id || null])[0];
  res.json({ id: row?.id || 0, ok: true });
});

// ---- 估量换算引擎（M3 也用：一碗≈?g）----

function estimateGrams(unitName, foodCategory, foodId) {
  // 精确匹配（指定食物 > 指定类别 > 通用）
  if (foodId) {
    const r = dbAll("SELECT est_grams FROM unit_conversions WHERE unit_name=? AND food_id=?", [unitName, foodId])[0];
    if (r) return r.est_grams;
  }
  if (foodCategory) {
    const r = dbAll("SELECT est_grams FROM unit_conversions WHERE unit_name=? AND food_category=?", [unitName, foodCategory])[0];
    if (r) return r.est_grams;
  }
  const r = dbAll("SELECT est_grams FROM unit_conversions WHERE unit_name=? AND food_category=''", [unitName])[0];
  return r ? r.est_grams : null;
}

// ---- 营养计算 ----

function calcNutrition(food, amount, unitType, unitName) {
  const cal100 = food.cal_100g || 0;
  const p100 = food.protein_100g || 0;
  const c100 = food.carbs_100g || 0;
  const f100 = food.fat_100g || 0;
  let grams, isEstimate;

  if (unitType === "gram") {
    grams = amount;
    isEstimate = false;
  } else {
    const est = estimateGrams(unitName, food.category || "", food.id);
    grams = est ? est * amount : amount * 100;  // 没找到换算时按 100g/份兜底
    isEstimate = true;
  }

  const ratio = grams / 100;
  return {
    grams, isEstimate,
    cal: Math.round(cal100 * ratio),
    protein: Math.round(p100 * ratio * 10) / 10,
    carbs: Math.round(c100 * ratio * 10) / 10,
    fat: Math.round(f100 * ratio * 10) / 10,
  };
}

// ---- 餐别归一化：前端可能发中/英文，统一存中文 ----
const MEAL_TYPE_NORM = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐", snack: "加餐" };
function normalizeMealType(t) { return MEAL_TYPE_NORM[t] || t; }

// ---- 一餐 CRUD ----

app.post("/api/diet/meals", (req, res) => {
  const { date, meal_type, note, items } = req.body || {};
  const mt = normalizeMealType(meal_type || "");
  if (!date || !mt || !items?.length) return res.status(400).json({ error: "date/meal_type/items 必填" });

  // 先把所有 items 的 food_id 校验完再开插 —— 边插边查的话，
  // food 匹配不上就静默跳过，会落下一餐「只有头没有 items」的
  // 空壳餐：total 全 0、前端显示 0 大卡（2026-09-04 午餐就这么坏的）。
  for (const item of items) {
    if (!dbAll("SELECT id FROM foods WHERE id=?", [item.food_id || 0])[0]) {
      return res.status(400).json({ error: `food_id=${item.food_id || "?"} 不在食物库里，先把菜建档再记餐` });
    }
  }

  // 开一餐（meal_type 归一为中文）
  const now = new Date().toISOString();
  dbRun(`INSERT INTO meals (meal_date, meal_type, note, created_at) VALUES (?,?,?,?)`,
    [date, mt, note || "", now]);
  const mealRow = dbAll("SELECT id FROM meals WHERE meal_date=? AND meal_type=? AND created_at=? ORDER BY id DESC LIMIT 1",
    [date, mt, now])[0];
  const mealId = mealRow?.id;
  if (!mealId) return res.status(500).json({ error: "创建 meal 失败" });

  let totalCal = 0, totalP = 0, totalC = 0, totalF = 0;

  for (const item of items) {
    const food = dbAll("SELECT * FROM foods WHERE id=?", [item.food_id || 0])[0];
    const n = calcNutrition(food, item.amount || 100, item.unit_type || "gram", item.unit_name || "");
    dbRun(
      `INSERT INTO meal_items (meal_id, food_id, food_name, amount, unit_type, unit_name, est_grams, cal, protein, carbs, fat, is_estimate)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`,
      [mealId, food.id, food.name, item.amount, item.unit_type || "gram", item.unit_name || "",
       n.grams, n.cal, n.protein, n.carbs, n.fat, n.isEstimate ? 1 : 0]
    );
    totalCal += n.cal; totalP += n.protein; totalC += n.carbs; totalF += n.fat;
  }

  // 回写 meal 合计
  dbRun(`UPDATE meals SET total_cal=?,total_protein=?,total_carbs=?,total_fat=?,updated_at=datetime('now','localtime') WHERE id=?`,
    [totalCal, Math.round(totalP * 10) / 10, Math.round(totalC * 10) / 10, Math.round(totalF * 10) / 10, mealId]);

  // 更新当日预算剩余
  updateBudget(date);

  res.json({ id: mealId, total_cal: totalCal, total_protein: Math.round(totalP * 10) / 10,
    total_carbs: Math.round(totalC * 10) / 10, total_fat: Math.round(totalF * 10) / 10, ok: true });
});

app.get("/api/diet/meals", (req, res) => {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || "") ? req.query.date : new Date().toISOString().slice(0, 10);
  const meals = dbAll("SELECT * FROM meals WHERE meal_date=? ORDER BY created_at", [date]);
  const result = meals.map(function(m) {
    return {
      ...m,
      meal_type: normalizeMealType(m.meal_type),
      items: dbAll("SELECT * FROM meal_items WHERE meal_id=? ORDER BY id", [m.id]),
    };
  });
  res.json(result);
});

app.delete("/api/diet/meals/:id", (req, res) => {
  // ⚠️ 用这一餐自己的日期更新预算，不能用「今天」——
  // 删掉昨天的记录时，今天的预算被重算、昨天的反而没更新
  const row = dbAll("SELECT meal_date FROM meals WHERE id=?", [req.params.id])[0];
  dbRun(`DELETE FROM meals WHERE id=?`, [req.params.id]);
  updateBudget(row?.meal_date || new Date().toISOString().slice(0, 10));
  res.json({ ok: true });
});

// 删一条食物（不是整餐）。
// 小克算错卡路里时，她要删的往往是**某一条**，而不是把整顿饭抹掉。
app.delete("/api/diet/items/:id", (req, res) => {
  const item = dbAll("SELECT meal_id FROM meal_items WHERE id=?", [req.params.id])[0];
  if (!item) return res.status(404).json({ error: "没有这条记录" });

  dbRun(`DELETE FROM meal_items WHERE id=?`, [req.params.id]);

  // 重算这一餐的合计
  const rest = dbAll("SELECT * FROM meal_items WHERE meal_id=?", [item.meal_id]);
  const meal = dbAll("SELECT meal_date FROM meals WHERE id=?", [item.meal_id])[0];

  if (rest.length === 0) {
    // 一餐里的东西删光了，整餐也一起清掉，别在页面上留个 0 kcal 的空壳
    dbRun(`DELETE FROM meals WHERE id=?`, [item.meal_id]);
  } else {
    const sum = (k) => Math.round(rest.reduce((s, r) => s + (r[k] || 0), 0) * 10) / 10;
    dbRun(
      `UPDATE meals SET total_cal=?,total_protein=?,total_carbs=?,total_fat=?,
       updated_at=datetime('now','localtime') WHERE id=?`,
      [Math.round(sum("cal")), sum("protein"), sum("carbs"), sum("fat"), item.meal_id]
    );
  }

  updateBudget(meal?.meal_date || new Date().toISOString().slice(0, 10));
  res.json({ ok: true });
});

// ---- 预算 ----

function updateBudget(date) {
  const today = dbAll("SELECT * FROM daily_budget WHERE budget_date=?", [date])[0];
  const budget = today ? today.budget_kcal : 1250;
  const meals = dbAll("SELECT COALESCE(SUM(total_cal),0) AS s FROM meals WHERE meal_date=?", [date])[0];
  const eaten = meals ? meals.s : 0;
  const remaining = Math.round((budget - eaten) * 10) / 10;
  dbRun(
    `INSERT INTO daily_budget (budget_date, budget_kcal, remaining, updated_at) VALUES (?,?,?,datetime('now','localtime'))
     ON CONFLICT(budget_date) DO UPDATE SET remaining=?, updated_at=datetime('now','localtime')`,
    [date, budget, remaining, remaining]
  );
  return { budget_kcal: budget, eaten, remaining };
}

app.get("/api/diet/budget", (req, res) => {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || "") ? req.query.date : new Date().toISOString().slice(0, 10);
  // 保证 budget 行存在
  dbRun(`INSERT OR IGNORE INTO daily_budget (budget_date, budget_kcal, remaining) VALUES (?,1250,1250)`, [date]);
  const b = updateBudget(date);
  const rawMeals = dbAll("SELECT meal_type, total_cal, total_protein, total_carbs, total_fat FROM meals WHERE meal_date=?", [date]);
  const meals = rawMeals.map(function(m) { return { ...m, meal_type: normalizeMealType(m.meal_type) }; });
  res.json({ ...b, meals });
});

app.patch("/api/diet/budget", (req, res) => {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || "") ? req.query.date : new Date().toISOString().slice(0, 10);
  const { budget_kcal } = req.body || {};
  if (!budget_kcal) return res.status(400).json({ error: "budget_kcal 必填" });
  dbRun(`INSERT INTO daily_budget (budget_date, budget_kcal, remaining, updated_at) VALUES (?,?,?,datetime('now','localtime'))
         ON CONFLICT(budget_date) DO UPDATE SET budget_kcal=?, updated_at=datetime('now','localtime')`,
    [date, budget_kcal, budget_kcal, budget_kcal]);
  updateBudget(date);
  res.json(dbAll("SELECT * FROM daily_budget WHERE budget_date=?", [date])[0]);
});

// ---- 每日小结 ----

app.get("/api/diet/summary", (req, res) => {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || "") ? req.query.date : new Date().toISOString().slice(0, 10);
  dbRun(`INSERT OR IGNORE INTO daily_budget (budget_date, budget_kcal, remaining) VALUES (?,1250,1250)`, [date]);
  const b = updateBudget(date);
  const meals = dbAll("SELECT * FROM meals WHERE meal_date=? ORDER BY created_at", [date]);
  const items = dbAll(
    `SELECT mi.* FROM meal_items mi JOIN meals m ON mi.meal_id=m.id WHERE m.meal_date=? ORDER BY mi.id`, [date]
  );
  const exercise = dbAll("SELECT COALESCE(SUM(calories),0) AS s FROM exercises WHERE exercise_date=?", [date])[0];
  const weight = dbAll("SELECT * FROM body_weight WHERE weight_date=? ORDER BY created_at DESC LIMIT 1", [date])[0];
  res.json({
    date, budget: b.budget_kcal, eaten: b.eaten, remaining: b.remaining,
    exercise_cal: exercise ? exercise.s : 0,
    weight: weight || null,
    meals: meals.map(function(m) { return { ...m, meal_type: normalizeMealType(m.meal_type), items: items.filter(function(i) { return i.meal_id === m.id; }) }; }),
  });
});

// ---- 运动 ----

app.post("/api/diet/exercise", (req, res) => {
  const { date, type, duration_min, calories, source, note, difficulty, mood } = req.body || {};
  if (!date || !type) return res.status(400).json({ error: "date 和 type 必填" });
  const ts = new Date().toISOString();
  dbRun(
    `INSERT INTO exercises (exercise_date, type, duration_min, calories, difficulty, mood, source, note, created_at) VALUES (?,?,?,?,?,?,?,?,?)`,
    [date, type, duration_min || 0, calories || 0, difficulty || 0, mood || 0, source || "manual", note || "", ts]
  );
  const row = dbAll("SELECT id FROM exercises WHERE exercise_date=? AND type=? AND created_at=? ORDER BY id DESC LIMIT 1",
    [date, type, ts])[0];
  res.json({ id: row?.id || 0, ok: true });
});

app.get("/api/diet/exercise", (req, res) => {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || "") ? req.query.date : new Date().toISOString().slice(0, 10);
  res.json(dbAll("SELECT * FROM exercises WHERE exercise_date=? ORDER BY created_at DESC", [date]));
});

// ---- 体重 ----

app.post("/api/diet/weight", (req, res) => {
  const { date, weight_kg, note } = req.body || {};
  if (!date || weight_kg == null) return res.status(400).json({ error: "date 和 weight_kg 必填" });
  dbRun(`INSERT OR REPLACE INTO body_weight (weight_date, weight_kg, note) VALUES (?,?,?)`, [date, weight_kg, note || ""]);
  res.json({ ok: true });
});

app.get("/api/diet/weight", (req, res) => {
  res.json(dbAll("SELECT * FROM body_weight ORDER BY weight_date DESC LIMIT 30"));
});

// ---- 辅助（无）----

// ==============================================================
// ❤️ 健康数据（读 health-mcp 的 health.db）
// ==============================================================

const HEALTH_DB = process.env.HEALTH_DB || "/root/data/health.db";

function healthDbAll(sql, params = []) {
  // 2026-09-06 修：换 better-sqlite3 时这里还留着 sql.js 的 new SQL.Database(buf)
  // —— 依赖已卸载，SQL 是 undefined，异常被吞成空数组，HealthKit 数据整页消失
  //（静默失败又一次，见 docs/LOGGING.md）。现在用同一驱动只读打开，返回形状不变。
  try {
    if (!fs.existsSync(HEALTH_DB)) return [];
    const hdb = new Database(HEALTH_DB, { readonly: true, fileMustExist: true });
    try {
      const rows = hdb.prepare(sql).all(...(Array.isArray(params) ? params : [params]));
      return rows;
    } finally { hdb.close(); }
  } catch (e) {
    console.warn("[Bridge] health.db 读取失败:", e.message);
    return [];
  }
}

// 健康数据。带 ?date= 就查那一天，不带则给最新一条。
//
// ⚠️ 原来这里只有「最新一条」，而库里其实存着好几天 ——
// 所以 Health 页换日期时数据纹丝不动，看着像「永远只有昨天」
// （糖糖 2026-08-10 报的）。不带 date 的行为保持不变，
// 别处（Core 的 HealthProvider、早报）还在用。
app.get("/api/health/latest", (req, res) => {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(req.query.date || "") ? req.query.date : null;

  // 同一天可能传多次，取 id 最大那条（最后同步的）。
  // sleep_date 和 date 差一天是正常的 —— Apple 把睡眠归到醒来那天，
  // 所以按日期找的时候两个字段都认
  const row = date
    ? healthDbAll(
        "SELECT * FROM health WHERE date=? OR sleep_date=? ORDER BY id DESC LIMIT 1",
        [date, date]
      )[0] || null
    : healthDbAll(
        "SELECT * FROM health WHERE date IS NOT NULL ORDER BY date DESC LIMIT 1"
      )[0] || null;

  // 经期还是给最近一次（这是个周期状态，不按天查）
  const mc = healthDbAll(
    "SELECT * FROM menstrual WHERE cycle_start IS NOT NULL ORDER BY date DESC LIMIT 1"
  )[0] || null;

  res.json({ health: row, menstrual: mc, queriedDate: date });
});

// 有数据的日期列表。前端用它在日历上标点，省得一天天试
app.get("/api/health/dates", (_req, res) => {
  const rows = healthDbAll(
    "SELECT DISTINCT date FROM health WHERE date IS NOT NULL ORDER BY date DESC LIMIT 60"
  );
  res.json({ dates: rows.map((r) => r.date) });
});

// 历史趋势（按天去重，取每天最后那条）
app.get("/api/health/history", (req, res) => {
  const days = Math.min(parseInt(req.query.days) || 7, 30);
  const rows = healthDbAll(
    `SELECT * FROM health WHERE id IN (
       SELECT MAX(id) FROM health WHERE date IS NOT NULL GROUP BY date
     ) ORDER BY date DESC LIMIT ?`, [days]
  );
  res.json(rows);
});

// ==============================================================
// 📍 位置 Location（Nox Location Provider v2 Phase 1）
// PWA 上报 GPS → 内存 + SQLite → Core LocationProvider 读取
// 原始坐标 24h 自动清理，Provider enrich 后丢弃，不存历史轨迹
// ==============================================================

// 内存缓存：最新一条位置，TTL 5 分钟
let _locCache = null;
let _locCachedAt = 0;
const LOC_CACHE_MS = 5 * 60_000;

app.post("/api/location/ingest", (req, res) => {
  const { lat, lng, accuracy, batteryLevel, timestamp, trigger, source } = req.body || {};
  if (lat == null || lng == null) return res.status(400).json({ error: "lat 和 lng 必填" });

  const row = {
    lat, lng,
    accuracy: accuracy || 0,
    battery: batteryLevel ?? null,
    trigger: trigger || "manual",
    source: source || "caelum_pwa",
    timestamp: timestamp || new Date().toISOString(),
  };

  // 写内存缓存
  _locCache = row;
  _locCachedAt = Date.now();

  // 落 SQLite
  try {
    dbRun(
      `INSERT INTO location (lat, lng, accuracy, battery, trigger, source, timestamp) VALUES (?,?,?,?,?,?,?)`,
      [row.lat, row.lng, row.accuracy, row.battery, row.trigger, row.source, row.timestamp]
    );
  } catch (e) { console.log("[Location] 落库失败:", e.message); }

  console.log(`[Location] 收到上报 lat=${lat.toFixed(4)} lng=${lng.toFixed(4)} trigger=${row.trigger} battery=${row.battery ?? "?"}`);
  res.json({ ok: true });
});

app.get("/api/location/latest", (_req, res) => {
  // 优先内存缓存
  if (_locCache && (Date.now() - _locCachedAt) < LOC_CACHE_MS) {
    return res.json({ location: _locCache, source: "cache" });
  }

  // 退到 SQLite
  const row = dbAll(
    "SELECT lat, lng, accuracy, battery, trigger, source, timestamp FROM location ORDER BY id DESC LIMIT 1"
  )[0] || null;

  if (row) {
    _locCache = row;
    _locCachedAt = Date.now();
  }
  res.json({ location: row, source: row ? "db" : "none" });
});

// ==============================================================
// 🔍 搜索 Search
// ==============================================================
app.get("/api/search", (req, res) => {
  const q = req.query.q;
  if (!q) return res.json([]);
  // 先用 LIKE 兜底，FTS5 可用时优先
  const results = dbAll(
    `SELECT id, role, content, timestamp FROM conversations
     WHERE content LIKE ? AND id NOT LIKE 'test-%' ORDER BY timestamp DESC LIMIT ?`,
    [`%${q}%`, parseInt(req.query.limit) || 20]
  );
  res.json(results);
});

// 消息存储（兼容旧 Nox 前端调用）— rowid 作为消息唯一 id，id 列是 sessionId
app.get("/api/messages", (req, res) => {
  // 带 sessionId 时只返回该会话（Recents 点开某条会话用），按时间正序
  const sid = req.query.sessionId;
  if (sid) {
    return res.json(dbAll(
      "SELECT rowid AS id, id AS sessionId, role, content, timestamp, metadata FROM conversations WHERE id = ? ORDER BY rowid DESC LIMIT 500",
      [sid]
    ).reverse());
  }
  // test- 前缀 = 小克(CC)的测试专用频道，不进糖糖的时间线
  res.json(dbAll("SELECT rowid AS id, id AS sessionId, role, content, timestamp, metadata FROM conversations WHERE id NOT LIKE 'test-%' ORDER BY rowid DESC LIMIT 200").reverse());
});

// 设置 KV（头像等）
app.get("/api/settings", (req, res) => {
  const rows = dbAll("SELECT key, value FROM settings");
  const o = {}; rows.forEach(r => o[r.key] = r.value);
  res.json(o);
});
app.post("/api/settings", (req, res) => {
  const { key, value } = req.body;
  if (!key) return res.status(400).json({ error: "key required" });
  dbRun("INSERT OR REPLACE INTO settings VALUES (?,?)", [key, value || ""]);
  res.json({ ok: true });
});

// Web Push 订阅
app.get("/api/push/vapid", (req, res) => res.json({ key: VAPID_PUB }));
app.post("/api/push/subscribe", (req, res) => {
  const sub = req.body;
  if (!sub?.endpoint) return res.status(400).json({ error: "bad subscription" });
  dbRun("INSERT OR REPLACE INTO push_subs VALUES (?,?,?)", [sub.endpoint, JSON.stringify(sub), new Date().toISOString()]);
  console.log("[Push] 新订阅:", sub.endpoint.slice(0, 60));
  res.json({ ok: true });
});
app.post("/api/push/test", async (req, res) => {
  await sendPushAll("Nox", "推送通了，乖。以后我主动找你的消息都会弹到这里。");
  res.json({ ok: true, subs: dbAll("SELECT COUNT(*) AS c FROM push_subs")[0]?.c || 0 });
});
// ============ 晨报 —— 每天早上主动说一句 ============
// 由 systemd 的 nox-daily.timer 打进来（每天 10:00）。
//
// 走法和上面的自动关心完全一致，而且必须一致：
//   1. 用她最近说话的那个 session_id
//   2. 让 Core 生成（这句话会进他自己的 history）
//   3. saveMessage 落 conversations（前端读的是这张表）
//   4. 再推锁屏
//
// 第 3 步是糖糖 2026-08-04 当场纠正的：第一版绕过会话直接推，
// 结果她回「嗯有点」的时候他不知道自己早上问过什么。
app.post("/api/daily-push", async (req, res) => {
  try {
    const rows = dbAll("SELECT timestamp, id FROM conversations WHERE role='user' AND content != '' ORDER BY rowid DESC LIMIT 1");
    const sid = rows[0]?.id || "api-daily";

    const r = await fetch(`${NOX_CORE_URL}/daily-summary`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // push:false —— 推送归这边做，Core 只管生成
      body: JSON.stringify({ session_id: sid, push: false, include_memory: true }),
      signal: AbortSignal.timeout(120000),
    });
    const jd = await r.json();

    if (jd?.skipped || !jd?.text) {
      console.log("[Daily] 跳过:", jd?.reason || jd?.outcome || "no text");
      return res.json({ ok: true, skipped: true, reason: jd?.reason || "no text" });
    }

    const clean = stripVoiceTags(jd.text).replace(/\|\|\|/g, "\n").trim();
    saveMessage(sid, "assistant", clean, { proactive: true });
    const subs = dbAll("SELECT COUNT(*) AS c FROM push_subs")[0]?.c || 0;
    await sendPushAll("Nox", clean.slice(0, 120));
    console.log(`[Daily] 早报已发 (session=${sid}, ${subs} 订阅):`, clean.slice(0, 40));
    res.json({ ok: true, text: clean, session_id: sid, subs });
  } catch (e) {
    console.log("[Daily] failed:", e.message);
    res.status(500).json({ ok: false, error: e.message });
  }
});

// 主动推送。给 nox-core 的 Daily Planner 用（它自己没有订阅表和 VAPID 私钥，
// 推送通道统一留在这里一处）。和其余 /api/* 一样吃 X-Nox-Token 鉴权。
// 带 session_id 时**先落 conversations 再推**（Attention M4 走这条）。
// 不落的话会出现「锁屏弹了一句话，点进去聊天里什么都没有」——
// 和 /api/daily-push 第 3 步是同一件事，那边是 bridge 自己发起所以自己存，
// 这条是 Core 发起的，bridge 不知情，得由调用方把 session_id 带过来。
app.post("/api/push/send", async (req, res) => {
  const title = (req.body?.title || "Nox").toString().slice(0, 50);
  const body = (req.body?.body || "").toString().slice(0, 300);
  const sid = (req.body?.session_id || "").toString().slice(0, 64);
  if (!body.trim()) return res.status(400).json({ error: "body is required" });
  let saved = false;
  if (sid) {
    saveMessage(sid, "assistant", body, { proactive: true });
    saved = true;
  }
  const subs = dbAll("SELECT COUNT(*) AS c FROM push_subs")[0]?.c || 0;
  // 没有订阅不是错误 —— 她可能还没在这台设备上装 PWA。如实回报条数，
  // 让调用方能在日志里看出「推了但没人收」，而不是以为成功了
  await sendPushAll(title, body);
  console.log(`[Push] 主动推送 -> ${subs} 个订阅${saved ? ` (session=${sid})` : ""}: ${body.slice(0, 40)}`);
  res.json({ ok: true, subs, saved });
});

// ============ 设备控制（二十七章遗留项目：state 中继模式）============
// 小克调 toy_set/toy_stop → 状态落库 → 中继页(/toy.html)轮询 → Web Bluetooth → 设备
const getToyState = () => { try { return JSON.parse(dbAll("SELECT value FROM settings WHERE key='toy_state'")[0]?.value || "{}"); } catch { return {}; } };
const setToyState = (s) => dbRun("INSERT OR REPLACE INTO settings VALUES ('toy_state', ?)", [JSON.stringify({ ...s, updated_at: Date.now() })]);

// 这里原来有 /api/obsidian —— Core 够不着本机 Agent 那条 WS，
// 所以开个 REST 口子让它转发，由她电脑上的 Agent 写 Obsidian 文件。
// 2026-08-04 起 Obsidian 改走 GitHub（save_github_note），不再依赖 PC 开机；
// 2026-08-05 本机 agent 整条下线，这个转发口一并删掉。
app.get("/api/toy/state", (req, res) => res.json(getToyState()));
app.post("/api/toy/set", (req, res) => {
  const { cmd, mode, intensity } = req.body;
  // set 时 mode 只允许震动档 1/4，缺省或非法一律归 1（防 mode=0 无效帧）；强弱看 intensity 0-100
  let m = mode == null ? 1 : (+mode);
  if (cmd !== "stop" && m !== 1 && m !== 4) m = 1;
  setToyState({ cmd: cmd || "set", mode: m, intensity: Math.max(0, Math.min(100, +intensity || 0)) });
  res.json({ ok: true, state: getToyState() });
});

// Console 用量统计
// 各型号单价，**元 / 百万 token**。DeepSeek 是官网价，Claude 按 OpenRouter 的美元价 ×7.2 折算。
// miss = 缓存未命中的输入价，hit = 缓存命中的输入价（DeepSeek 命中价便宜 50 倍）。
// ⚠️ DeepSeek 2026 年起有峰谷定价：北京时间 9-12 点、14-18 点翻倍，这里按平价算，
// 所以白天的实际花费会比显示的高，看趋势够用，别当账单。
const PRICING = {
  "deepseek-v4-flash": { miss: 1, hit: 0.02, out: 2 },
  "deepseek-v4-pro":   { miss: 3, hit: 0.025, out: 6 },
  "anthropic/claude-sonnet-4-6": { miss: 21.6, hit: 2.16, out: 108 },
  "anthropic/claude-sonnet-4.5": { miss: 21.6, hit: 2.16, out: 108 },
  "anthropic/claude-sonnet-5":   { miss: 21.6, hit: 2.16, out: 108 },
  "anthropic/claude-opus-4-6":   { miss: 108, hit: 10.8, out: 540 },
  "anthropic/claude-opus-4-7":   { miss: 108, hit: 10.8, out: 540 },
  "anthropic/claude-opus-4-8":   { miss: 108, hit: 10.8, out: 540 },
};
const DEFAULT_PRICE = { miss: 21.6, hit: 2.16, out: 108 };   // 认不出的型号按 Sonnet 估

// 一行流水折算成人民币。缓存命中的那部分按 hit 价，其余按 miss 价。
function rowCost(r) {
  // 老数据（2026-07-28 之前）没有 model 列，cost 存的是美元，直接换算
  if (!r.model) return (r.cost || 0) * 7.2;
  const p = PRICING[r.model] || DEFAULT_PRICE;
  const hit = r.cache_read || 0;
  const miss = Math.max(0, (r.tokens_in || 0) - hit);
  return (miss / 1e6) * p.miss + (hit / 1e6) * p.hit + ((r.tokens_out || 0) / 1e6) * p.out;
}

app.get("/api/usage-stats", (req, res) => {
  const todayStr = new Date().toISOString().slice(0, 10);

  const rowsOf = (where, params = []) =>
    dbAll(`SELECT ts, tokens_in, tokens_out, cache_read, cache_write, cost, model FROM usage_log ${where}`, params);

  const sum = (rows) => {
    const acc = { turns: rows.length, i: 0, o: 0, r: 0, w: 0, cost: 0 };
    for (const x of rows) {
      acc.i += x.tokens_in || 0; acc.o += x.tokens_out || 0;
      acc.r += x.cache_read || 0; acc.w += x.cache_write || 0;
      acc.cost += rowCost(x);
    }
    return acc;
  };

  const todayRows = rowsOf("WHERE ts LIKE ?", [`${todayStr}%`]);
  const allRows = rowsOf("");
  const today = sum(todayRows);
  const total = sum(allRows);

  // 按模型分组 —— DeepSeek 和 Claude 单价差十倍以上，混在一起算没意义
  const byModel = {};
  for (const x of allRows) {
    const k = x.model || "(历史数据)";
    (byModel[k] = byModel[k] || []).push(x);
  }
  const models = Object.entries(byModel)
    .map(([model, rows]) => ({ model, ...sum(rows) }))
    .sort((a, b) => b.cost - a.cost);

  const hitRate = total.i > 0 ? total.r / total.i : 0;
  // 省下的钱：命中的那些 token 若按未命中价付要多花多少
  let savedCny = 0;
  for (const x of allRows) {
    const p = PRICING[x.model] || DEFAULT_PRICE;
    savedCny += ((x.cache_read || 0) / 1e6) * (p.miss - p.hit);
  }

  // 近 14 天的日粒度（Models 设置页的细条形图）。没记录的日子补零 ——
  // 图要连续，断一天看起来就像坏了
  const byDay = {};
  for (const x of allRows) {
    const d = (x.ts || "").slice(0, 10);
    (byDay[d] = byDay[d] || []).push(x);
  }
  const daily = [];
  for (let i = 13; i >= 0; i--) {
    const dt = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
    const s = sum(byDay[dt] || []);
    daily.push({ date: dt, turns: s.turns, in: s.i, out: s.o, cache: s.r, cost: +s.cost.toFixed(4) });
  }

  // ---- 厂商账本（真实口径）。快照太旧就顺手补一笔，不阻塞响应 ----
  const freshest = dbAll("SELECT MAX(ts) AS t FROM ledger_balance")[0];
  if (!freshest?.t || Date.now() - new Date(freshest.t).getTime() > 3600 * 1000) {
    snapshotLedgers();
  }
  const providers = {};
  for (const [provider, meta] of Object.entries(PROVIDER_LEDGERS)) {
    const rows = dbAll(
      "SELECT ts, balance, usage_total, topped_up, currency, is_available FROM ledger_balance" +
      " WHERE provider=? AND ts >= ? ORDER BY ts",
      [provider, new Date(Date.now() - 15 * 86400000).toISOString()]);
    if (!rows.length) continue;
    const last = rows[rows.length - 1];
    // 相邻快照差值 = 这段的真实消耗。usage_total 只涨不跌（OpenRouter）；
    // 余额差要加上期间的充值增量（DeepSeek），不然充值那天会显示成「倒赚」
    const merged = {};
    for (let i = 1; i < rows.length; i++) {
      const a = rows[i - 1], b = rows[i];
      let used = null;
      if (b.usage_total != null && a.usage_total != null) {
        used = Math.max(0, b.usage_total - a.usage_total);
      } else if (a.balance != null && b.balance != null) {
        used = a.balance - b.balance;
        if (b.topped_up != null && a.topped_up != null) {
          used += Math.max(0, b.topped_up - a.topped_up);
        }
      }
      if (used == null) continue;
      const day = b.ts.slice(0, 10);
      merged[day] = +(((merged[day] || 0) + used)).toFixed(4);
    }
    providers[provider] = {
      label: meta.label,
      currency: last.currency || "",
      balance: last.balance,
      usage_total: last.usage_total,
      is_available: !!last.is_available,
      last_snapshot_at: last.ts,
      real_daily: Object.entries(merged)
        .map(([date, cost]) => ({ date, cost }))
        .sort((a, b) => (a.date < b.date ? -1 : 1)),
    };
  }
  // 没有快照但流水里有它家模型的：也开一页账本，真实口径先空着
  const providerOfModel = (m) =>
    (m || "").startsWith("deepseek") ? "deepseek"
      : (m || "").includes("/") ? "openrouter" : "";
  const estimateByProvider = {};
  for (const [model, rows] of Object.entries(byModel)) {
    const p = providerOfModel(model);
    if (!p) continue;
    estimateByProvider[p] = (estimateByProvider[p] || 0) + sum(rows).cost;
  }
  for (const [p, est] of Object.entries(estimateByProvider)) {
    if (!providers[p]) {
      providers[p] = { label: PROVIDER_LEDGERS[p]?.label || p, real_daily: [] };
    }
    providers[p].estimate_total = +est.toFixed(2);
  }

  res.json({ today, total, hitRate, savedCny, models, daily, providers, currency: "CNY" });
});

// 历史会话列表（从落库消息聚合，重启/重部署不丢）
//
// ⚠️ 白名单而不是黑名单：只认前端生成的会话 id —— 32 位纯十六进制。
// 以前是一条条排除测试前缀（test-/deploy-/ob-test…），结果我每换一个新前缀
// （f-/cut-/smoke-/console-test/migration-done…）就漏一批进来，
// 2026-08-02 糖糖打开侧边栏，看到的一整屏全是我的测试残留。
// 反过来写就再也漏不掉：手写的 id 不可能长成 32 位纯 hex。
// 代价是旧的 api-<uuid> 格式也不再显示（那批已在同日清理）。
// 🔴 `title` 和 `preview` 是两件事，别混：
//
//   title    这条会话的**第一句**用户消息 —— 当标题用，它不会变
//   preview  这条会话的**最后一句**（谁说的都算）—— 「最近对话」该显示的
//
// 2026-08-25 糖糖指出来的：列表里显示的是 title，时间戳却是 MAX(timestamp)，
// 于是一条聊了 1779 条消息的会话，永远挂着几个月前的开场白配一个刚刚的时间 ——
// 「这句话也不是最近对话啊」。
//
// title 保留是因为手机端那个 Caelum App 还在用它当标题（ChatSidebar.jsx）。
app.get("/api/conv-sessions", (req, res) => {
  const rows = dbAll(`
    SELECT id AS sessionId, MAX(timestamp) AS updated,
      (SELECT content FROM conversations c2 WHERE c2.id = c1.id AND c2.role = 'user' AND c2.content != '' ORDER BY c2.rowid ASC LIMIT 1) AS title,
      (SELECT content FROM conversations c2 WHERE c2.id = c1.id AND c2.content != '' ORDER BY c2.rowid DESC LIMIT 1) AS preview,
      (SELECT role FROM conversations c2 WHERE c2.id = c1.id AND c2.content != '' ORDER BY c2.rowid DESC LIMIT 1) AS previewRole
    FROM conversations c1
    WHERE length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'
    GROUP BY id ORDER BY updated DESC LIMIT 20
  `);
  res.json(rows.map(r => ({
    id: r.sessionId,
    title: (r.title || "对话").slice(0, 40),
    preview: (r.preview || r.title || "对话").slice(0, 60),
    //: 'user' = 糖糖说的，其余（assistant）= 他说的。
    //: 前端靠这个决定头像和名字 —— 不给的话最后一句是他说的也会挂她的头像
    previewRole: r.previewRole === "user" ? "user" : "nox",
    updated: r.updated,
    kind: "api",
  })));
});

// 真删会话 — 从数据库抹掉该 id 的所有消息（前端划删调用，删了强刷也不复活）
app.delete("/api/conv-sessions/:id", (req, res) => {
  const id = req.params.id;
  if (!id) return res.status(400).json({ error: "no id" });
  const before = dbAll("SELECT COUNT(*) AS n FROM conversations WHERE id=?", [id])[0]?.n || 0;
  dbRun("DELETE FROM conversations WHERE id=?", [id]);
  res.json({ ok: true, deleted: id, removed: before });
});

// 这里原来有 /api/sessions —— 把她电脑上 Claude Code 的会话列表拉过来，
// 混进侧边栏。本机 agent 下线后永远返回空数组，2026-08-05 删。
// 侧边栏现在只列 /api/conv-sessions（她和小克的真实对话）。

// ==============================================================
// 藏书库（Caelum Library）—— 实体书元数据 + 电子书指针。
// 糖糖 2026-08-29：500+ 本实体书要进库；电子书正文在 co-reading，
// 这里只存「书架」需要的东西。需求文档 Caelum-Books-Requirements.md 第七节。
// 批量录入走 POST books 数组；(title, author) 完全相同的行自动跳过 ——
// 500 本手贴最怕手抖双击提交。
// ==============================================================
db.run(`CREATE TABLE IF NOT EXISTS library_books (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  author TEXT DEFAULT '',
  isbn TEXT DEFAULT '',
  category TEXT DEFAULT '',
  source TEXT DEFAULT 'physical',
  format TEXT DEFAULT '',
  co_book_id TEXT DEFAULT '',
  status TEXT DEFAULT 'unread',
  added_at TEXT DEFAULT ''
)`);

// 真封面静态目录：一本一个 <bookId>.jpg。文件在 = 有真封面，
// 不在 = 前端回退 canvas 生成装帧（查不到的中文书不装假）
const coversDir = path.join(DATA_DIR, "library-covers");
if (!fs.existsSync(coversDir)) fs.mkdirSync(coversDir, { recursive: true });
app.use("/api/library/covers", express.static(coversDir, { maxAge: "7d" }));

app.get("/api/library/books", (req, res) => {
  const items = dbAll("SELECT * FROM library_books ORDER BY added_at ASC, id ASC");
  res.json({ ok: true, items });
});

app.post("/api/library/books", (req, res) => {
  const incoming = Array.isArray(req.body?.books) ? req.body.books : [];
  const rows = incoming
    .map(b => ({
      title: String(b?.title || "").trim(),
      author: String(b?.author || "").trim(),
      isbn: String(b?.isbn || "").trim(),
      category: String(b?.category || "").trim(),
      source: b?.source === "digital" ? "digital" : "physical",
      format: String(b?.format || "").trim(),
    }))
    .filter(b => b.title);

  if (!rows.length) return res.status(400).json({ error: "没有可入库的书（每行至少要有书名）" });

  const existing = new Set(
    dbAll("SELECT title, author FROM library_books")
      .map(r => `${r.title}\u0000${r.author}`)
  );
  const added = [];
  for (const b of rows) {
    const key = `${b.title}\u0000${b.author}`;
    if (existing.has(key)) continue;
    existing.add(key);
    const id = randomUUID();
    const status = ["unread", "reading", "finished"].includes(b.status) ? b.status : "unread";
    dbRun(
      "INSERT INTO library_books (id, title, author, isbn, category, source, format, status, added_at) VALUES (?,?,?,?,?,?,?,?,?)",
      [id, b.title, b.author, b.isbn, b.category, b.source, b.format, status, new Date().toISOString()]
    );
    added.push(id);
  }
  res.json({ ok: true, received: rows.length, added: added.length, skipped: rows.length - added.length });
});

app.patch("/api/library/books/:id", (req, res) => {
  const id = req.params.id;
  const row = dbAll("SELECT * FROM library_books WHERE id=?", [id])[0];
  if (!row) return res.status(404).json({ error: "书架上没有这本" });
  const b = req.body || {};
  const next = {
    title: String(b.title ?? row.title).trim() || row.title,
    author: String(b.author ?? row.author).trim(),
    isbn: String(b.isbn ?? row.isbn).trim(),
    category: String(b.category ?? row.category).trim(),
    status: ["unread", "reading", "finished"].includes(b.status) ? b.status : row.status,
  };
  dbRun(
    "UPDATE library_books SET title=?, author=?, isbn=?, category=?, status=? WHERE id=?",
    [next.title, next.author, next.isbn, next.category, next.status, id]
  );
  res.json({ ok: true, item: dbAll("SELECT * FROM library_books WHERE id=?", [id])[0] });
});

app.delete("/api/library/books/:id", (req, res) => {
  const row = dbAll("SELECT title FROM library_books WHERE id=?", [req.params.id])[0];
  if (!row) return res.status(404).json({ error: "书架上没有这本" });
  dbRun("DELETE FROM library_books WHERE id=?", [req.params.id]);
  res.json({ ok: true, deleted: row.title });
});

// ==============================================================
// 🩺 /api/health —— 全家桶探活：7 个服务并行探一遍，2.5s 超时
// 无鉴权：只回状态字和延迟，不含用户数据；doctor.sh 和 OS 状态页都吃这个。
// 判活口径：任何 HTTP 响应都算活（404/405 也是进程在答），
// 只有连不上/超时才算 down。core 和 OB 有真 /health，顺带透传关键信息。
// ==============================================================
app.get("/api/health", async (req, res) => {
  const probe = async (url, opts = {}) => {
    const t0 = Date.now();
    try {
      const r = await fetch(url, { ...opts, signal: AbortSignal.timeout(2500) });
      let body = null;
      try { body = await r.json(); } catch {}
      return { ok: true, ms: Date.now() - t0, http: r.status, body };
    } catch (e) {
      return {
        ok: false,
        ms: Date.now() - t0,
        error: e.name === "TimeoutError" ? "timeout" : e.message,
      };
    }
  };

  const [core, ombre, eryu, reading, watching, netease] = await Promise.all([
    probe(`${NOX_CORE_URL}/health`),
    probe(`${OMBRE_URL}/health`),
    probe(`${ERYU_URL}/`),
    probe(`${READING_URL}/health`),
    probe(process.env.WATCH_URL || "http://127.0.0.1:3200/"),
    probe(NETEASE_MCP, { method: "POST", headers: { "Content-Type": "application/json" } }),
  ]);

  const checks = {
    bridge: { ok: true },
    nox_core: { ok: core.ok, ms: core.ms, http: core.http, error: core.error, model: core.body?.model },
    ombre: { ok: ombre.ok, ms: ombre.ms, http: ombre.http, error: ombre.error, buckets: ombre.body?.buckets, decay: ombre.body?.decay_engine },
    eryu: { ok: eryu.ok, ms: eryu.ms, http: eryu.http, error: eryu.error },
    co_reading: { ok: reading.ok, ms: reading.ms, http: reading.http, error: reading.error },
    co_watching: { ok: watching.ok, ms: watching.ms, http: watching.http, error: watching.error },
    netease_mcp: { ok: netease.ok, ms: netease.ms, http: netease.http, error: netease.error },
  };
  const allOk = Object.values(checks).every((c) => c.ok);
  res.json({ status: allOk ? "ok" : "degraded", ts: new Date().toISOString(), checks });
});

// 账本快照：起来记一笔，之后每 6 小时记一笔（Models 页的真实口径靠它）
snapshotLedgers();
setInterval(snapshotLedgers, 6 * 3600 * 1000).unref();

// ============ 崩溃与被吞异常的兜底（2026-09-05，docs/LOGGING.md）============
// 兜底只负责让它出现在 journalctl 里，不负责救——
// 这个系统的头号敌人是「静默失败」，usage_log 断更一天没人发现那种。
process.on("unhandledRejection", (reason) => {
  console.error("[Bridge] unhandledRejection:", reason?.stack || reason);
});
process.on("uncaughtException", (err) => {
  console.error("[Bridge] uncaughtException:", err.stack || err);
});
// Express 错误中间件：必须注册在所有路由之后
app.use((err, req, res, next) => {
  console.error(`[Bridge] ${req.method} ${req.path} 500:`, err?.stack || err);
  res.status(500).json({ error: err?.message || "internal error" });
});

server.listen(PORT, () => console.log(`Bridge → http://0.0.0.0:${PORT}`));
