// 跑法：node --test test/
// nox-watchdog 的判定和状态机（core.js）。Cloudflare 的东西（cron、KV、发信 binding）用假的顶上。
import assert from "node:assert/strict";
import test from "node:test";

import { buildRaw, check, tick } from "../src/core.js";

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const healthy = () => json({ status: "ok", checks: { bridge: { ok: true }, nox_core: { ok: true } } });
const coreDown = () => json({ status: "degraded", checks: { bridge: { ok: true }, nox_core: { ok: false, error: "timeout" } } });
const tunnelDown = () => new Response("<h1>Error 1033</h1> Argo Tunnel error", { status: 530 });

function harness(responses) {
  const kv = new Map();
  const sent = [];
  let t = Date.UTC(2026, 9, 12, 2, 10);          // 10:10 北京时间
  let failSend = false;
  const env = {
    CHECK_URL: "https://noxtang.com/api/health",
    MAIL_FROM: "watchdog@noxtang.com",
    MAIL_TO: "her@example.com",
    SEND_EMAIL: {},
    STATE: { get: async (k) => kv.get(k) ?? null, put: async (k, v) => { kv.set(k, v); } },
  };
  const deps = {
    now: () => t,
    fetch: async () => {
      const r = responses.shift();
      if (r instanceof Error) throw r;
      return r();
    },
    send: async (_env, from, to, raw) => {
      if (failSend) throw new Error("mail down");
      sent.push(raw);
    },
  };
  const subjects = () => sent.map((raw) => {
    const m = raw.match(/Subject: =\?UTF-8\?B\?([^?]+)\?=/);
    return Buffer.from(m[1], "base64").toString("utf8");
  });
  return {
    env, deps, sent, subjects, kv,
    step: async (mins = 1) => { const r = await tick(env, deps); t += mins * 60000; return r; },
    state: () => JSON.parse(kv.get("state")),
    setFailSend: (v) => { failSend = v; },
  };
}

test("🔴 首页 200 不算数：bridge 在、我的后端（nox-core）死了 → 「Caelum 挂了」", async () => {
  const h = harness([coreDown, coreDown]);
  const r = await h.step();
  assert.equal(r.up, false);
  assert.equal(r.kind, "service");
  assert.match(r.detail, /nox-core/);
  assert.equal(h.sent.length, 0, "一次失败不发（防抖）");
  await h.step();
  assert.deepEqual(h.subjects(), ["【看门狗】家里网络在,但 Caelum 挂了"]);
});

test("健康：不发邮件；首页那种 HTML 200 也算活（读不到 JSON 不瞎判）", async () => {
  const h = harness([healthy, () => new Response("<html>ok</html>", { status: 200, headers: { "content-type": "text/html" } })]);
  assert.equal((await h.step()).up, true);
  assert.equal((await h.step()).up, true);
  assert.equal(h.sent.length, 0);
});

test("隧道断（530 / 1033）连续两次 → 「从外面连不上家里了」；恢复 → 从第一次失败算时长", async () => {
  const h = harness([tunnelDown, tunnelDown, tunnelDown, tunnelDown, healthy]);
  for (let i = 0; i < 4; i++) await h.step(1);
  assert.deepEqual(h.subjects(), ["【看门狗】从外面连不上家里了"], "断着期间只发一封");
  await h.step();
  assert.equal(h.subjects()[1], "【看门狗】回来了,断了 4 分钟");
});

test("超时 / 网络错误算「连不上家里」；502 算「服务挂了」", async () => {
  const to = Object.assign(new Error("t"), { name: "TimeoutError" });
  assert.equal((await check("x", async () => { throw to; })).kind, "unreachable");
  assert.equal((await check("x", async () => new Response("bad", { status: 502 }))).kind, "service");
});

test("故障性质变了（服务挂 → 整个连不上）补发一封", async () => {
  const h = harness([coreDown, coreDown, tunnelDown, tunnelDown]);
  for (let i = 0; i < 4; i++) await h.step();
  assert.deepEqual(h.subjects(), ["【看门狗】家里网络在,但 Caelum 挂了", "【看门狗】情况变了:从外面连不上家里了"]);
});

test("🔴 发信失败不许把这一分钟的状态弄丢：进补发队列，下一分钟发出去", async () => {
  const h = harness([tunnelDown, tunnelDown, tunnelDown, tunnelDown]);
  h.setFailSend(true);
  await h.step();
  await h.step();                                   // 该告警了，但发信挂了 —— 不能抛
  const s = h.state();
  assert.equal(s.failCount, 2, "状态没存上 = 失败次数永远攒不起来");
  assert.equal(s.down, true);
  assert.equal(s.mailQueue.length, 1);
  await h.step();                                   // 补发也失败（比如发信那边还没好）—— 队列里不能丢
  assert.equal(h.state().mailQueue.length, 1, "补发失败就把邮件扔了 = 她永远收不到这封告警");
  h.setFailSend(false);
  await h.step();
  assert.deepEqual(h.subjects(), ["【看门狗】从外面连不上家里了"]);
  assert.equal(h.state().mailQueue.length, 0);
});

test("邮件格式：头和正文之间是 CRLF，主题是 UTF-8 base64", () => {
  const raw = buildRaw("a@noxtang.com", "b@qq.com", "回来了", "正文");
  assert.ok(raw.includes("\r\n\r\n"), "头和正文之间要有一个空行（CRLF CRLF）");
  assert.ok(!/[^\r]\n/.test(raw), "不能有单独的 LF");
  assert.match(raw, /Subject: =\?UTF-8\?B\?/);
});
