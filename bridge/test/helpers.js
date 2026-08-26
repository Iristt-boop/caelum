/**
 * 起一个**完全隔离**的 bridge 实例。
 *
 * ⚠️ 隔离是这套测试的第一要务：
 *   DB_PATH    → 临时目录（绝不碰 data/nox-bridge.db）
 *   HEALTH_DB  → 指向不存在的路径（healthDbAll 会返回 []）
 *   NOX_CORE_URL → 指向一个死端口（测试不打真的 Core）
 *   PORT       → 0 之外的随机高位端口
 *
 * 糖糖的规矩：不许在她的正式环境留测试数据。这里连库都是新的，
 * 跑完整个目录删掉。
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(HERE, "..", "server.js");

//: ⚠️ **只能用 ASCII** —— HTTP 头是 ByteString（latin1），
//  带一个中文字就是 `Cannot convert argument to a ByteString`
export const TOKEN = "test-token-not-a-real-one";

/** 随机高位端口。碰撞概率低，撞了就重跑 */
function pickPort() {
  return 20000 + Math.floor(Math.random() * 20000);
}

export async function startBridge() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bridge-test-"));
  const port = pickPort();

  const child = spawn(process.execPath, [SERVER], {
    env: {
      ...process.env,
      PORT: String(port),
      NOX_TOKEN: TOKEN,
      DB_PATH: path.join(dir, "test.db"),
      HEALTH_DB: path.join(dir, "health-不存在.db"),
      // 死端口：任何转发给 Core 的请求都会连不上，而不是打到真的 Core 上
      NOX_CORE_URL: "http://127.0.0.1:9",
      // 共影心跳的过期窗口压到 1 秒，好让测试验得动"她走开了"那条分支。
      // 线上是 90 秒（server.js 的 WATCH_STALE_MS）
      WATCH_STALE_MS: "1000",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  const logs = [];
  child.stdout.on("data", (b) => logs.push(String(b)));
  child.stderr.on("data", (b) => logs.push(String(b)));

  const base = `http://127.0.0.1:${port}`;
  await waitUp(base, child, logs);

  return {
    base,
    dir,
    logs,
    async stop() {
      child.kill();
      await new Promise((r) => child.once("exit", r));
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
}

async function waitUp(base, child, logs, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`bridge 启动就退了（code=${child.exitCode}）：\n${logs.join("")}`);
    }
    try {
      // 随便打一个需要鉴权的端点：403 就说明服务起来了
      const r = await fetch(`${base}/api/todo/list`);
      if (r.status === 403 || r.ok) return;
    } catch {
      /* 还没起来 */
    }
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error(`bridge 20 秒没起来：\n${logs.join("")}`);
}

/** 带 token 的请求。不传 token 用来测鉴权 */
export async function api(base, path, { method = "GET", body, token = TOKEN } = {}) {
  const headers = {};
  if (token) headers["X-Nox-Token"] = token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(`${base}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await r.json();
  } catch {
    /* 有的端点不回 JSON */
  }
  return { status: r.status, data };
}
