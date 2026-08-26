"""
健康数据接收服务
接收 iPhone 快捷指令上传的 Apple Health 数据，存入 SQLite。

启动:
    uvicorn server:app --host 127.0.0.1 --port 8102

⚠️ **端口不能用 8100** —— 那是 nox-core 占着的。原来这里写的就是 8100，
照着部署会和 Core 撞车。8101 是 health_mcp，所以这里用 8102。

⚠️ 绑 127.0.0.1 而不是 0.0.0.0：这个端点要公网可达（快捷指令要 POST 进来），
但那一段交给 Caddy 做 TLS 和反代，进程本身只听本机。

快捷指令 POST 到:  https://noxtang.com/health/sync
Header:  X-Auth-Token: <HEALTH_SYNC_TOKEN>
"""

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("health-sync")

# ============ 配置 ============
# 快捷指令里填的 token，改了这里快捷指令也要改。
#
# **没配就拒绝启动**，不给默认值 —— 这个端点在公网上，
# 留一个 "change-me-to-a-random-string" 当兜底，等于没有锁。
# ha-mcp 也是这么做的（HA_TOKEN 缺失直接 raise）。
AUTH_TOKEN = os.getenv("HEALTH_SYNC_TOKEN", "")
if not AUTH_TOKEN or AUTH_TOKEN == "change-me-to-a-random-string":
    raise RuntimeError("HEALTH_SYNC_TOKEN 未设置（或还是默认值），拒绝启动")

# 和 bridge 的库放一起（/root/data），备份脚本一次能兜住
DB_PATH = os.getenv("HEALTH_DB", "/root/data/health.db")

app = FastAPI(title="Health Sync API")


# ============ 数据库 ============
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,               -- 数据所属日期 YYYY-MM-DD
            steps INTEGER,                    -- 步数
            active_energy REAL,               -- 活动能量 kcal
            distance_km REAL,                 -- 步行+跑步距离 km
            flights_climbed INTEGER,          -- 爬楼层数
            avg_heart_rate REAL,              -- 平均心率
            min_heart_rate REAL,
            max_heart_rate REAL,
            resting_heart_rate REAL,          -- 静息心率
            hrv_ms REAL,                      -- 心率变异性 ms
            sleep_duration_min REAL,          -- 总睡眠 分钟
            deep_sleep_min REAL,              -- 深度睡眠 分钟
            rem_sleep_min REAL,               -- REM 睡眠 分钟
            core_sleep_min REAL,              -- 核心睡眠(浅睡) 分钟
            awake_min REAL,                   -- 睡眠中清醒 分钟
            blood_oxygen_pct REAL,            -- 血氧 %
            -- ⚠️ 墓碑（2026-08-19）：weight_kg / body_fat_pct **一直是空的**。
            -- 实测最近 14 天 0 行有值 —— HealthKit 那边根本没往这两个字段送。
            -- **记体重的真源是 bridge 的 `body_weight` 表**
            -- （Core 的 `log_weight` 工具写，App 的运动页读）。
            -- 列留着不删：删它要同步改下面 `_ACTIVITY` 的插入列表，
            -- 为两列空数据去动一个正在跑的同步服务，风险大于收益。
            -- **别再拿这两列当体重的数据源。**
            weight_kg REAL,
            body_fat_pct REAL,
            sleep_date TEXT,                  -- 睡眠归属日，见下
            source TEXT DEFAULT 'shortcuts',  -- 数据来源
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_health_date ON health(date)")

    # 老库补列。一条记录里其实装着两个日期语义：
    #   date       —— 活动数据（步数/心率/能量）所属的自然日，= 昨天
    #   sleep_date —— 这一觉的归属日。**Apple 按醒来那天算**，所以是今天
    # 不分开的话，早上问「昨晚睡得怎么样」拿到的会是前天晚上那一觉。
    cols = {r[1] for r in conn.execute("PRAGMA table_info(health)")}
    if "sleep_date" not in cols:
        conn.execute("ALTER TABLE health ADD COLUMN sleep_date TEXT")
        print("[init_db] 已给老表补上 sleep_date 列")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS menstrual (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            flow_level TEXT DEFAULT '',
            day_number INTEGER,
            cycle_start TEXT,
            note TEXT DEFAULT '',
            source TEXT DEFAULT 'shortcuts',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_menstrual_date ON menstrual(date)")
    conn.commit()
    conn.close()


init_db()


# ============ 数据模型 ============
class MenstrualPayload(BaseModel):
    """经期数据。至少一条，支持批量 POST。"""
    model_config = ConfigDict(extra="allow")
    date: str
    flow_level: str = ""          # 少量/中等/大量/微量
    note: str = ""


class HealthPayload(BaseModel):
    model_config = ConfigDict(extra="allow")  # 允许接收 sleepSamples 等前端自定义字段

    date: str                                # 数据日期
    steps: Optional[int] = None
    active_energy: Optional[float] = None
    distance_km: Optional[float] = None
    flights_climbed: Optional[int] = None
    avg_heart_rate: Optional[float] = None
    min_heart_rate: Optional[float] = None
    max_heart_rate: Optional[float] = None
    resting_heart_rate: Optional[float] = None
    hrv_ms: Optional[float] = None
    sleep_duration_min: Optional[float] = None
    deep_sleep_min: Optional[float] = None
    rem_sleep_min: Optional[float] = None
    core_sleep_min: Optional[float] = None
    awake_min: Optional[float] = None
    blood_oxygen_pct: Optional[float] = None
    weight_kg: Optional[float] = None
    body_fat_pct: Optional[float] = None
    sleep_samples: Optional[list] = None     # 原始睡眠样本，后端解析
    sleep_date: Optional[str] = None         # 睡眠归属日；不传则从样本的结束时间算


# 快捷指令常用 camelCase 字段 → 后端 snake_case 字段 的兼容映射
_CAMEL_CASE_ALIASES = {
    "activeEnergy": "active_energy",
    "distance": "distance_km",
    "avgHeartRate": "avg_heart_rate",
    "minHeartRate": "min_heart_rate",
    "maxHeartRate": "max_heart_rate",
    "restingHeartRate": "resting_heart_rate",
    "hrv": "hrv_ms",
    "totalSleep": "sleep_duration_min",
    "deepSleep": "deep_sleep_min",
    "remSleep": "rem_sleep_min",
    "lightSleep": "core_sleep_min",
    "awakeSleep": "awake_min",
}


# 睡眠分期名称映射（兼容中文/英文系统）
_SLEEP_STAGE_MAP = {
    # 深睡
    "深睡": "deep", "deep": "deep", "deep_sleep": "deep", "deepsleep": "deep",
    # REM
    "快速眼动": "rem", "rem": "rem", "rem_sleep": "rem", "remsleep": "rem",
    # 浅睡/核心
    "核心": "core", "core": "core", "浅睡": "core", "light": "core",
    "light_sleep": "core", "lightsleep": "core",
    # 清醒
    "清醒": "awake", "awake": "awake", "awake_sleep": "awake", "awakesleep": "awake",
}


def _parse_ios_date(value) -> Optional[datetime]:
    """兼容 iOS 快捷指令常见的几种日期字符串格式"""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # ISO 8601: 2026-07-29T23:00:00+08:00
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    # 其余常见写法。
    #
    # ⚠️ **不带秒的必须单列**：iOS 中文系统实测吐的是「2026年8月1日 00:04」，
    # 而原来只有带秒的 "%Y年%m月%d日 %H:%M:%S"，匹配不上 —— 于是每一条睡眠样本
    # 都被静默跳过，睡眠数据整段丢失，而且不报任何错。
    for fmt in (
        "%Y年%m月%d日 %H:%M",      # ← 实测就是这个
        "%Y年%m月%d日 %H:%M:%S",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
        "%Y年%m月%d日%H:%M",       # 日期和时间之间没空格的写法
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _sleep_date_from_samples(samples: list) -> Optional[str]:
    """这一觉算哪天的 —— 取最后一个样本的**结束**日期。

    Apple Health 把睡眠归到醒来那天：8-2 夜里 00:31 睡、8-3 早上醒，
    健康 App 里显示的是「8月3日 7小时12分」。

    所以不能拿活动数据的 date（昨天）来说睡眠，那会差一天 ——
    早上问「昨晚睡得怎么样」，答的是前天晚上。
    """
    latest = None
    for s in samples:
        if not isinstance(s, dict):
            continue
        end = _parse_ios_date(s.get("endDate"))
        if end and (latest is None or end > latest):
            latest = end
    return latest.strftime("%Y-%m-%d") if latest else None


def _last_sleep_session(samples: list, gap_hours: float = 3.0) -> list:
    """从一堆睡眠样本里切出**最后一觉**。

    为什么要在后端切：iOS 快捷指令的「开始日期 介于 A 和 B」筛选不可靠 ——
    2026-08-03 实测，窗口明明设的是 8-2 18:00 → 8-3 12:00，
    返回的样本却是 8-2 01:56 → 08:35（前一天那觉）。
    与其追它为什么不生效，不如让它多传一点，切分逻辑握在自己手里。

    切法：样本按开始时间排序，相邻两段间隔超过 gap_hours 就当成两觉，
    只保留最后那一组。3 小时是因为夜里醒一会儿很常见（她昨晚醒了 17 分钟），
    但不会连着醒三小时还算同一觉。
    """
    spans = []
    for s in samples:
        if not isinstance(s, dict):
            continue
        st = _parse_ios_date(s.get("startDate"))
        en = _parse_ios_date(s.get("endDate"))
        if st and en:
            spans.append((st, en, s))
    if not spans:
        return []

    spans.sort(key=lambda x: x[0])
    group = [spans[0]]
    for prev, cur in zip(spans, spans[1:]):
        # 用「上一段的结束」到「这一段的开始」算间隔
        if (cur[0] - prev[1]).total_seconds() / 3600.0 > gap_hours:
            group = [cur]          # 断开了，重新起一觉
        else:
            group.append(cur)
    return [s for _, _, s in group]


def _parse_sleep_samples(samples: list) -> dict:
    """
    解析 iOS 原始睡眠样本列表。
    每个样本格式: {"value": "深睡", "startDate": "...", "endDate": "..."}
    返回汇总字段，供数据库存储。
    """
    buckets = {"deep": 0.0, "rem": 0.0, "core": 0.0, "awake": 0.0}
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        value = str(sample.get("value", "")).lower().strip().replace(" ", "_")
        stage = None
        for key, mapped in _SLEEP_STAGE_MAP.items():
            if key.lower() in value:
                stage = mapped
                break
        if not stage:
            continue
        start = _parse_ios_date(sample.get("startDate"))
        end = _parse_ios_date(sample.get("endDate"))
        if not start or not end:
            continue
        minutes = (end - start).total_seconds() / 60.0
        if minutes > 0:
            buckets[stage] += minutes

    total = buckets["deep"] + buckets["rem"] + buckets["core"]
    return {
        "sleep_duration_min": total,
        "deep_sleep_min": buckets["deep"],
        "rem_sleep_min": buckets["rem"],
        "core_sleep_min": buckets["core"],
        "awake_min": buckets["awake"],
    }


def _normalize_payload(raw: dict) -> dict:
    """
    同时兼容两种前端写法：
    1. snake_case（原始字段名）
    2. camelCase（快捷指令参考图里的字段名）
    3. 原始睡眠样本列表（后端自动解析汇总）
    距离/血氧单位也会在这里容错。
    """
    data = dict(raw)

    # camelCase → snake_case
    for camel, snake in _CAMEL_CASE_ALIASES.items():
        if camel in data and data[camel] is not None and data.get(snake) is None:
            data[snake] = data[camel]

    # 原始睡眠样本解析（如果前端没传汇总字段的话）
    sleep_samples = data.get("sleep_samples") or data.get("sleepSamples")
    if sleep_samples and isinstance(sleep_samples, list) and len(sleep_samples) > 0:
        # 把样本的真实时间范围记下来 —— 这是判断「快捷指令的查询窗口对不对」
        # 的唯一证据。只存汇总的话，拿到的是哪一觉根本看不出来，
        # 只能靠对比健康 App 的数字猜（2026-08-03 就为这个来回试了几轮）。
        def _span(items):
            ss = [(_parse_ios_date(s.get("startDate")), _parse_ios_date(s.get("endDate")))
                  for s in items if isinstance(s, dict)]
            ss = [(a, b) for a, b in ss if a and b]
            if not ss:
                return None
            return min(a for a, _ in ss), max(b for _, b in ss)

        # 只留最后一觉 —— 传来的可能横跨两个晚上（iOS 的日期筛选靠不住）
        last_night = _last_sleep_session(sleep_samples)
        whole, one = _span(sleep_samples), _span(last_night)
        if whole and one:
            logger.info(
                "睡眠样本 %d 条覆盖 %s → %s；取最后一觉 %d 条：%s → %s",
                len(sleep_samples), whole[0].isoformat(timespec="minutes"),
                whole[1].isoformat(timespec="minutes"), len(last_night),
                one[0].isoformat(timespec="minutes"), one[1].isoformat(timespec="minutes"),
            )
        if last_night:
            sleep_samples = last_night

        parsed = _parse_sleep_samples(sleep_samples)
        for key, val in parsed.items():
            if data.get(key) is None:
                data[key] = val
        # 睡眠归属日从样本算，不用快捷指令再传一个字段
        if not data.get("sleep_date"):
            data["sleep_date"] = _sleep_date_from_samples(sleep_samples)

    # 距离单位容错：>100 基本不可能是公里（除非你是超人），按米处理
    if data.get("distance_km") is not None and data["distance_km"] > 100:
        data["distance_km"] = data["distance_km"] / 1000.0

    # 血氧单位容错：快捷指令里可能是 0.98，数据库期望 98
    if data.get("blood_oxygen_pct") is not None and data["blood_oxygen_pct"] < 1:
        data["blood_oxygen_pct"] = data["blood_oxygen_pct"] * 100.0

    return data


# 快捷指令拼 JSON 时，某项当天没数据就会留下一个空洞：
#     "distance": ,
# 那是**非法 JSON**，FastAPI 会整包 422 掉 —— 十几项好数据陪着一起丢。
# 实测 2026-08-01 那天就是这样（她没有距离数据）。
#
# 这里在解析前把 `"key": ` 后面直接跟 , 或 } 的补成 null。
# 只补空洞，不做别的修补 —— 宽容要有边界，真正的畸形还是该报错。
_EMPTY_VALUE = re.compile(r'("(?:[^"\\]|\\.)*"\s*:\s*)(?=[,}\]])')
# 删掉一个字段后，前一行的尾随逗号很容易忘记删 —— 2026-08-03 就是这么炸的
# （删了 sleepSamples，"hrv" 那行的逗号还在）。
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _normalize_shortcut_date(s: str) -> str:
    """2026年7月19日 12:00 → 2026-07-19"""
    import re
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


def _parse_shortcut_menstrual(body: str):
    """解析快捷指令发出的经期数据。

    实测格式（非标准 JSON）：
        {
        [
          {
            "date": "2026年7月19日 12:00",
            "flow": "量少量少..."   ← 多天的 flow 被拼成一个字符串！
          },
          ...
        ]
        }

    策略：
      1. 去掉外层的 { 和 } 包装，只留中间 [ ... ]
      2. 用正则提取每个 { "date": "...", "flow": "..." }
      3. flow 字段里可能需要去重（"量少量少" → "量少"）
    """
    import re

    # 去掉外层 { } 包装（非标准 JSON 格式）
    s = body.strip()
    # 去掉开头可能有的 {
    if s.startswith("{"):
        depth = 0
        for i, ch in enumerate(s):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    s = s[:i]  # 去掉结尾的 }
                    break
        s = s[s.index("[") if "[" in s else 0:]

    # 现在应该只剩 [ ... ] 了，试试正常解析
    s = s.strip()
    if not s.startswith("["):
        # 最后的兜底：用 re 直接从原始里抠 date 和 flow
        return _extract_menstrual_re(body)

    # 修复 flow 值里可能的多余空格
    s = re.sub(r'"量少\s+量少"', '"量少"', s)
    s = re.sub(r'"量中\s+量中"', '"中等"', s)
    s = re.sub(r'"大量\s+大量"', '"大量"', s)

    try:
        items = json.loads(s)
        if isinstance(items, list):
            return items
    except json.JSONDecodeError:
        pass

    return _extract_menstrual_re(body)


def _extract_menstrual_re(body: str):
    """兜底：用正则直接抠 date 和 flow"""
    import re
    results = []
    # 匹配每个对象: "date": "...", "flow": "..."
    pattern = re.compile(
        r'"date"\s*:\s*"([^"]+)".*?"flow"\s*:\s*"([^"]*)"',
        re.DOTALL,
    )
    for m in pattern.finditer(body):
        date_str = m.group(1)
        flow_str = m.group(2).strip()
        # 去重 flow（"量少量少" → "量少"）
        # 每个中文流量词最多出现一次
        for term in ("大量", "中等", "量少", "微量"):
            count = flow_str.count(term)
            if count > 1:
                flow_str = flow_str.replace(term, "", count - 1)
        results.append({"date": date_str, "flow": flow_str})
    return results if results else None


def _lenient_loads(text: str) -> dict:
    fixed = _EMPTY_VALUE.sub(r"\1null", text)
    if fixed != text:
        n = len(_EMPTY_VALUE.findall(text))
        # 记一笔，别静默 —— 空洞多说明快捷指令那边有字段一直取不到数
        logger.warning("请求里有 %d 个空值字段，已补成 null", n)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        # **只在真解析不动时**才动尾随逗号。
        # 无条件替换会误伤字符串里的 ",}"，那种破坏是静默的，比报错糟得多。
        retry = _TRAILING_COMMA.sub(r"\1", fixed)
        if retry == fixed:
            raise
        result = json.loads(retry)      # 还失败就照常往外抛
        logger.warning("请求里有多余的尾随逗号，已容错；建议在快捷指令里删掉")
        return result


# ============ 接口 ============
@app.post("/health/sync")
async def sync_health(request: Request, x_auth_token: str = Header(default="")):
    """快捷指令每天上传昨日完整健康数据"""
    if x_auth_token != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")

    body = (await request.body()).decode("utf-8", "replace")
    try:
        raw = _lenient_loads(body)
    except json.JSONDecodeError as e:
        logger.warning("JSON 解析失败: %s | 前 200 字: %s", e, body[:200])
        raise HTTPException(status_code=400, detail=f"invalid json: {e}") from e
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="body 必须是一个 JSON 对象")
    if not raw.get("date"):
        raise HTTPException(status_code=400, detail="缺少 date 字段")

    # 过一遍模型做类型转换，未定义的字段（sleepSamples 等）也保留
    payload = HealthPayload(**raw)
    merged = payload.model_dump()
    if payload.model_extra:
        merged.update(payload.model_extra)
    p = _normalize_payload(merged)
    # 只写这次真的带了值的字段。
    #
    # ⚠️ 一天一条记录，**同一天再传就更新，不新插一行**。
    # 原因是两个快捷指令的顺序：她起床时先跑睡眠（写进 date=昨天 那条），
    # 9 点再跑活动。如果活动是 INSERT，同一天就会出现两行 ——
    # 一行只有睡眠、一行只有活动，而读取按 MAX(id) 取，
    # 拿到的是后插的那行，**睡眠字段空的**。她早上问睡得怎么样，
    # 他会说没数据，而且全程不报错。
    #
    # 睡眠端点那边同理，只更新睡眠字段。两边各写各的，互不覆盖。
    _ACTIVITY = ("steps", "active_energy", "distance_km", "flights_climbed",
                 "avg_heart_rate", "min_heart_rate", "max_heart_rate",
                 "resting_heart_rate", "hrv_ms", "blood_oxygen_pct",
                 "weight_kg", "body_fat_pct")
    _SLEEP = ("sleep_duration_min", "deep_sleep_min", "rem_sleep_min",
              "core_sleep_min", "awake_min", "sleep_date")
    present = {k: p.get(k) for k in _ACTIVITY + _SLEEP if p.get(k) is not None}

    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT MAX(id) FROM health WHERE date = ?", (p["date"],)).fetchone()
        target = row[0] if row else None
        if target and present:
            sets = ", ".join(f"{k}=?" for k in present)
            conn.execute(f"UPDATE health SET {sets} WHERE id=?",
                         (*present.values(), target))
            action = "updated"
        elif target:
            action = "noop"          # 这次啥都没带，别把已有的抹成空
        else:
            cols = ["date"] + list(present)
            ph = ", ".join("?" * len(cols))
            conn.execute(f"INSERT INTO health ({', '.join(cols)}) VALUES ({ph})",
                         (p["date"], *present.values()))
            action = "inserted"
        conn.commit()
    finally:
        conn.close()

    logger.info("活动数据 %s：date=%s，写入 %d 个字段", action, p["date"], len(present))
    return {"status": "ok", "action": action, "date": p["date"],
            "fields_written": len(present), "received_at": datetime.now().isoformat()}


@app.post("/health/sleep")
async def sync_sleep(request: Request, x_auth_token: str = Header(default="")):
    """只收睡眠样本，专给「睡眠」那个快捷指令用。

    为什么单独开一个端点：睡眠和活动的**日期语义不一样** ——
    活动按自然日（8-2 一整天），睡眠按醒来那天（Apple 把 8-2 夜里那觉归到 8-3）。
    塞进同一个快捷指令要算两套时间窗口，而 iOS 的日期筛选还不可靠
    （2026-08-03 实测：窗口设 8-2 18:00→8-3 12:00，返回的却是 8-2 01:56 的样本）。

    所以这边只要一件事：**把最近的睡眠样本原样发过来**，日期全由后端算。
    请求体只需要：
        {"sleepSamples": [{"value": "...", "startDate": "...", "endDate": "..."}, ...]}

    后端做三件事：
      1. 切出最后一觉（相邻样本间隔 > 3 小时算两觉）
      2. 归属日 = 最后一个样本的结束日期（Apple 的算法）
      3. 写进 `date = 归属日 - 1 天` 那条记录 —— 也就是「那天白天的活动 +
         那天晚上睡的觉」配成一条，语义完整。没有那条就新建一条。
    """
    if x_auth_token != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")

    body = (await request.body()).decode("utf-8", "replace")
    try:
        raw = _lenient_loads(body)
    except json.JSONDecodeError as e:
        logger.warning("睡眠 JSON 解析失败: %s | 前 200 字: %s", e, body[:200])
        raise HTTPException(status_code=400, detail=f"invalid json: {e}") from e

    samples = raw.get("sleepSamples") or raw.get("sleep_samples") or []
    if not isinstance(samples, list) or not samples:
        raise HTTPException(status_code=400, detail="没有 sleepSamples")

    last = _last_sleep_session(samples)
    if not last:
        raise HTTPException(status_code=400, detail="样本里没有能解析出时间的条目")

    sleep_date = _sleep_date_from_samples(last)
    if not sleep_date:
        raise HTTPException(status_code=400, detail="算不出睡眠归属日")

    p = _parse_sleep_samples(last)
    # 活动日 = 归属日的前一天：8-2 白天的活动，配 8-2 夜里睡、8-3 早上醒的那觉
    activity_date = (datetime.strptime(sleep_date, "%Y-%m-%d")
                     - timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(
        "睡眠专用端点：收到 %d 条 → 最后一觉 %d 条，归属 %s，写入 date=%s，总睡眠 %.0f 分",
        len(samples), len(last), sleep_date, activity_date, p["sleep_duration_min"],
    )

    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT MAX(id) FROM health WHERE date = ?", (activity_date,)
        ).fetchone()
        target = row[0] if row else None
        fields = (p["sleep_duration_min"], p["deep_sleep_min"], p["rem_sleep_min"],
                  p["core_sleep_min"], p["awake_min"], sleep_date)
        if target:
            conn.execute("""
                UPDATE health SET sleep_duration_min=?, deep_sleep_min=?, rem_sleep_min=?,
                       core_sleep_min=?, awake_min=?, sleep_date=? WHERE id=?
            """, (*fields, target))
            action = "updated"
        else:
            # 活动数据还没传（比如睡眠快捷指令先跑），先占一行，等活动数据来了会另起一条
            conn.execute("""
                INSERT INTO health (date, sleep_duration_min, deep_sleep_min,
                                    rem_sleep_min, core_sleep_min, awake_min, sleep_date)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (activity_date, *fields))
            action = "inserted"
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "ok", "action": action,
        "sleep_date": sleep_date, "activity_date": activity_date,
        "samples_received": len(samples), "samples_used": len(last),
        "sleep_minutes": round(p["sleep_duration_min"]),
        "deep": round(p["deep_sleep_min"]), "awake": round(p["awake_min"]),
    }


@app.post("/health/menstrual")
async def sync_menstrual(request: Request, x_auth_token: str = Header(default="")):
    """接收经期数据。快捷指令「查找健康样本」筛经血量，POST 过来。

    请求体：日期数组
    [
      {"date": "2026-08-01", "flow_level": "少量"},
      {"date": "2026-08-02", "flow_level": "中等"},
      ...
    ]

    后端自动算：
      - day_number = 该日期 - 最早带血日 + 1
      - cycle_start = 该周期的开始日

    同一天传两次会更新（UNIQUE(date)），不会重复。
    """
    if x_auth_token != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")

    raw_bytes = await request.body()
    body = raw_bytes.decode("utf-8", "replace")
    logger.warning("经期 body(%d字) 前1200: %s", len(body), body[:1200] if body else "(空)")

    raw = _parse_shortcut_menstrual(body)
    if raw is None:
        raise HTTPException(status_code=400, detail="invalid json - 无法解析快捷指令格式")
        raise HTTPException(status_code=400, detail=f"invalid json: {e}") from e

    items = raw if isinstance(raw, list) else [raw]
    if not items:
        raise HTTPException(status_code=400, detail="没有经期数据")

    # 找这批数据里最早的带血日 = 周期起点
    dates_with_flow = []
    clean = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # 兼容 "flow" 和 "flow_level" 两种字段名
        date_val = (item.get("date") or "").strip()
        flow_val = (item.get("flow") or item.get("flow_level") or "").strip()
        note_val = (item.get("note") or "").strip()
        if not date_val:
            continue
        # 尝试解析日期
        from datetime import datetime as _dt2
        parsed = _parse_ios_date(date_val)
        if parsed:
            date_val = parsed.strftime("%Y-%m-%d")
        # 日期格式转换（2026年7月19日 → 2026-07-19）
        date_val = _normalize_shortcut_date(date_val)
        # 映射 flow 值到标准名
        flow_map = {"量少": "少量", "少量": "少量",
                     "量中": "中等", "中等": "中等",
                     "大量": "大量", "量多": "大量",
                     "微量": "微量", "无": ""}
        flow_val = flow_map.get(flow_val, flow_val)
        dates_with_flow.append((date_val, flow_val))
        clean.append((date_val, flow_val, note_val))
        # 只有带经血量的才参与周期计算（微量不算周期的第一天，
        # 但数据还是存下来）
        dates_with_flow.append((date_val, flow_val))
        clean.append((date_val, flow_val, note_val))

    if not clean:
        raise HTTPException(status_code=400, detail="没有有效日期")

    # 按间隔切分周期：相邻带血日间隔 >5 天 = 新周期开始
    from datetime import datetime as _dt
    def days_between(a, b):
        return (_dt.strptime(a, "%Y-%m-%d") - _dt.strptime(b, "%Y-%m-%d")).days

    sorted_dates = sorted(set(d for d, _ in dates_with_flow))
    cycles = []  # [(cycle_start, [date, ...]), ...]
    current_start = None
    current_dates = []

    for i, d in enumerate(sorted_dates):
        if current_start is None:
            current_start = d
            current_dates = [d]
        elif days_between(d, sorted_dates[i-1]) > 5:
            # 间隔超过 5 天 → 新周期
            cycles.append((current_start, current_dates))
            current_start = d
            current_dates = [d]
        else:
            current_dates.append(d)
    if current_start:
        cycles.append((current_start, current_dates))

    # 给每个日期分配 cycle_start 和 day_number
    date_cycle = {}
    for cs, cds in cycles:
        for j, d in enumerate(cds, 1):
            date_cycle[d] = (cs, j)

    logger.info("经期周期切分: %d 个周期 %s",
                len(cycles),
                [(cs, f"{cds[0]}~{cds[-1]}", len(cds)) for cs, cds in cycles])

    conn = sqlite3.connect(DB_PATH)
    inserted = 0
    try:
        for d, flow, note in clean:
            cs, dn = date_cycle.get(d, ("", 0))
            conn.execute("""
                INSERT OR REPLACE INTO menstrual (date, flow_level, day_number, cycle_start, note)
                VALUES (?,?,?,?,?)
            """, (d, flow, dn, cs, note))
            inserted += 1
        conn.commit()
    finally:
        conn.close()

    cycle_info = [(cs, f"{cds[0]}~{cds[-1]}", len(cds)) for cs, cds in cycles]
    logger.info("经期数据：收到 %d 条，写入 %d 条，%d 个周期 %s",
                len(items), inserted, len(cycles), cycle_info)
    return {"status": "ok", "received": len(items), "inserted": inserted,
            "cycles": len(cycles)}


@app.get("/health/ping")
async def ping():
    """快捷指令测试连通性用"""
    return {"status": "ok", "time": datetime.now().isoformat()}
