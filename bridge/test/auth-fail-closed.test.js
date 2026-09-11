/**
 * 鉴权 fail-closed（2026-09-11）。
 *
 * ## 为什么有这条
 *
 * `ensureApiAuth` 原来最后有 `if (!AUTH_TOKEN) return next();` —— 缺 token 就
 * **全放行**。这条路径一旦被走到，`/api/*` 全部无鉴权可达：聊天记录、健康数据、
 * 待办、记忆，还有那条真的会下单的 `/api/nox/orders/{id}/confirm`。
 * 而且它不打日志、服务看起来一切正常。
 *
 * 这个项目历史上已经发生过一次「钥匙在公网裸奔」（PROJECT.md 第十二节前的
 * 「凭证：NOX_TOKEN 曾在公网裸奔」），所以现在选择 fail-closed：
 * **缺关键凭据就起不来。**
 *
 * ⚠️ 这条测试**不启动完整 bridge**（`helpers.js` 的 startBridge 一定会传 token），
 * 而是直接 spawn 一个没配 NOX_TOKEN 的进程，断言它自己退出。
 * 退出发生在任何 DB/文件操作之前，所以不会污染工作目录。
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(HERE, "..", "server.js");

/** 起一个**没有** NOX_TOKEN 的 bridge，等它自己死。 */
function startWithoutToken() {
  return new Promise((resolve) => {
    const env = { ...process.env };
    // Windows 的 env 键大小写不敏感，delete 一下就干净了
    delete env.NOX_TOKEN;
    env.PORT = "0";

    const child = spawn(process.execPath, [SERVER], {
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    const chunks = [];
    child.stdout.on("data", (b) => chunks.push(String(b)));
    child.stderr.on("data", (b) => chunks.push(String(b)));

    // 上限 15 秒：正常应该几十毫秒就退出。
    // 如果真的挂住了（= 它开始监听了），这里会 timeout 并 kill，
    // 断言 timedOut 为 false 就会红 —— 那正是我们要抓的回归。
    const timer = setTimeout(() => {
      child.kill();
      resolve({ code: null, out: chunks.join(""), timedOut: true });
    }, 15000);

    child.on("exit", (code) => {
      clearTimeout(timer);
      resolve({ code, out: chunks.join(""), timedOut: false });
    });
  });
}

test("缺 NOX_TOKEN 时 bridge 拒绝启动，而不是无鉴权运行", async () => {
  const { code, out, timedOut } = await startWithoutToken();

  assert.equal(
    timedOut,
    false,
    `没配 NOX_TOKEN 却还活着（= 已经开始监听无鉴权的 /api/*）。输出：\n${out}`,
  );
  assert.equal(code, 1, `应该以退出码 1 结束，实际 code=${code}。输出：\n${out}`);
});

test("鉴权失败时不会退回 fail-open（结构性检查）", async () => {
  const fs = await import("node:fs");
  const src = fs.readFileSync(SERVER, "utf8");

  // ⚠️ 只检查**代码行**，不检查注释。
  // 第一版直接对全文做正则，结果第一炮就打在自己身上：注释里引用旧代码
  // 也会命中。字符串 grep 不区分注释和代码，正是审计报告里点名过的毛病
  // （check-boundaries.sh 那套），所以这里至少把它做对：
  //   1. 丢掉以 // 或 * 或 /* 开头的行（注释）
  //   2. 剩下的行拼起来、压缩空白，这样跨行的 `if (...)\n return next()` 也能抓
  //
  // 局限（诚实标注）：行尾注释（`code(); // 说明`）不剥，块注释里不以 * 开头的
  // 中间行不剥。都只会导致**误报**，不会漏报 —— 对一条守门测试来说方向是对的。
  const code = src
    .split("\n")
    .filter((line) => {
      const t = line.trim();
      return t && !t.startsWith("//") && !t.startsWith("*") && !t.startsWith("/*");
    })
    .join(" ")
    .replace(/\s+/g, " ");

  assert.equal(
    /if\s*\(\s*!\s*AUTH_TOKEN\s*\)\s*return\s+next\s*\(/.test(code),
    false,
    "server.js 的代码里又出现了 `if (!AUTH_TOKEN) return next()` —— fail-open 回来了",
  );

  // 同一件事的另一半：启动守卫必须在。
  assert.equal(
    /if\s*\(\s*!\s*AUTH_TOKEN\s*\)\s*\{/.test(code),
    true,
    "server.js 里找不到「缺 NOX_TOKEN 就退出」的启动守卫",
  );
});
