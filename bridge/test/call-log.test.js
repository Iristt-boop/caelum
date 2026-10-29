/**
 * 她拨出去的电话也记账（2026-10-10）。
 *
 * 她问「我播出去的没记录吗？那他怎么知道我在跟他打电话」——
 * 他当场知道（每句话带 voice 标记），但没有任何一行说「这是通话」，事后谁也说不出通了多久。
 * 这里守：登记、门槛（点进去又退出的不算）、上限、和「他打来的」同表不混、旧行为不变。
 *
 * ⚠️ 不断言中文正文（本机控制台 gb2312）。
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

async function withBridge(fn) {
  const bridge = await startBridge();
  try {
    await fn(bridge.base);
  } finally {
    await bridge.stop();
  }
}

const log = (base, body) => api(base, "/api/call/log", { method: "POST", body });
const calls = async (base) => (await api(base, "/api/call/status")).data.calls;

test("登记一通：status=ended、direction=out、时长原样、开始时间 = 结束 - 时长", async () => {
  await withBridge(async (base) => {
    const before = Date.now();
    const r = await log(base, { duration: 125 });
    assert.equal(r.status, 200);
    assert.equal(r.data.logged, true);
    const [c] = await calls(base);
    assert.equal(c.id, r.data.id);
    assert.equal(c.status, "ended");
    assert.equal(c.direction, "out");
    assert.equal(c.duration_s, 125);
    const start = Date.parse(c.created_at);
    assert.ok(Math.abs(start - (before - 125 * 1000)) < 5000, `开始时间应约等于现在 - 125s，实际 ${c.created_at}`);
  });
});

test("5 秒以下不记（点进去又退出的不算一通）；刚好 5 秒记", async () => {
  await withBridge(async (base) => {
    for (const d of [0, 1, 4]) {
      const r = await log(base, { duration: d });
      assert.equal(r.data.logged, false, `${d}s 不该记`);
    }
    assert.equal((await log(base, { duration: 5 })).data.logged, true);
    assert.equal((await calls(base)).length, 1);
  });
});

test("乱传的时长不炸、不记：缺字段 / 字符串 / 负数 / NaN", async () => {
  await withBridge(async (base) => {
    for (const body of [{}, { duration: "abc" }, { duration: -30 }, { duration: null }]) {
      const r = await log(base, body);
      assert.equal(r.status, 200);
      assert.equal(r.data.logged, false);
    }
    assert.equal((await calls(base)).length, 0);
  });
});

test("时长有上限：前端算出 14 小时也只记 6 小时", async () => {
  await withBridge(async (base) => {
    await log(base, { duration: 14 * 3600 });
    assert.equal((await calls(base))[0].duration_s, 6 * 3600);
  });
});

test("字符串数字也认（前端 JSON 里可能是 \"90\"）", async () => {
  await withBridge(async (base) => {
    assert.equal((await log(base, { duration: "90" })).data.logged, true);
    assert.equal((await calls(base))[0].duration_s, 90);
  });
});

test("和他打来的同一张表、互不混：他打来的 direction 是空，她拨的是 out", async () => {
  await withBridge(async (base) => {
    const inv = await api(base, "/api/call/invite", { method: "POST", body: { reason: "r", opener: "o" } });
    assert.equal(inv.status, 200);
    await log(base, { duration: 30 });
    const all = await calls(base);
    assert.equal(all.length, 2);
    const hers = all.find((c) => c.direction === "out");
    const his = all.find((c) => !c.direction);
    assert.equal(hers.status, "ended");
    assert.equal(his.status, "ringing");
  });
});

test("登记她拨的电话不会冒出一通「正在响」的来电", async () => {
  await withBridge(async (base) => {
    await log(base, { duration: 60 });
    const cur = await api(base, "/api/call/current");
    assert.equal(cur.data.call, null);
  });
});

test("不带令牌进不来（和别的 /api/call 一样）", async () => {
  await withBridge(async (base) => {
    const r = await fetch(`${base}/api/call/log`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ duration: 60 }),
    });
    assert.ok(r.status === 401 || r.status === 403, `应该被挡，实际 ${r.status}`);
  });
});

// ---- 10-29：她拨的那通收成一个通话气泡（「语音通话 3:24」，点开看全文）----
// 她：「我主动打过去的语音通话也这样做。通话完成后显示『语音通话 3:24』，点击显示通话的所有内容」
// 通话页现生成 id，每句 /api/chat 带 callId → 落库标 meta.inCall；挂断 /call/log 带同一个 id →
// 往那几句所在的会话落一条通话气泡。Core 指向死端口：她那句在转发前落库，他那句是「连不上」兜底，照样带标

const chat = async (base, body) => {
  const r = await fetch(`${base}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Nox-Token": TOKEN },
    body: JSON.stringify(body),
  });
  await r.text().catch(() => "");
};
const saved = async (base, sid) => {
  const r = await api(base, `/api/messages?sessionId=${encodeURIComponent(sid)}`);
  return (Array.isArray(r.data) ? r.data : []).map((m) => ({ ...m, meta: m.metadata ? JSON.parse(m.metadata) : {} }));
};
const CID = "call-out-1761700000000-abc123";

test("通话里的每句（她的和他的）都标 inCall；不是通话的不标", async () => {
  await withBridge(async (base) => {
    await chat(base, { message: "hi", mode: "core", voice: true, sessionId: "s-call", callId: CID });
    await chat(base, { message: "typed", mode: "core", sessionId: "s-call", callId: CID });
    const rows = await saved(base, "s-call");
    const voiced = rows.filter((m) => m.meta.inCall === CID);
    assert.deepEqual(voiced.map((m) => m.role).sort(), ["assistant", "user"], "通话那轮她和他的都该标");
    assert.equal(rows.find((m) => m.content === "typed").meta.inCall, undefined, "不带 voice 的不算通话");
  });
});

test("乱传的 callId 不认（不落标）", async () => {
  await withBridge(async (base) => {
    for (const callId of ["x", "call-1-<b>", 12345, "call-out-1761700000000-abc123; drop"]) {
      await chat(base, { message: "hi", mode: "core", voice: true, sessionId: "s-bad", callId });
    }
    assert.ok((await saved(base, "s-bad")).every((m) => m.meta.inCall === undefined));
  });
});

test("挂断带 id：用这个 id 记账，往通话那条会话落一条她这边的通话气泡（带时长）", async () => {
  await withBridge(async (base) => {
    await chat(base, { message: "other", mode: "core", sessionId: "s-other" });
    await chat(base, { message: "hi", mode: "core", voice: true, sessionId: "s-call", callId: CID });
    const r = await log(base, { duration: 204, id: CID });
    assert.equal(r.data.id, CID);
    const [c] = await calls(base);
    assert.equal(c.id, CID);
    const bubble = (await saved(base, "s-call")).filter((m) => m.meta.call);
    assert.equal(bubble.length, 1);
    assert.equal(bubble[0].role, "user");
    assert.deepEqual(bubble[0].meta.call, { id: CID, status: "ended", direction: "out", duration_s: 204 });
    assert.equal((await saved(base, "s-other")).filter((m) => m.meta.call).length, 0, "别的会话不该冒气泡");
  });
});

test("同一个 id 重发只记一次、只一个气泡", async () => {
  await withBridge(async (base) => {
    await chat(base, { message: "hi", mode: "core", voice: true, sessionId: "s-call", callId: CID });
    await log(base, { duration: 60, id: CID });
    const again = await log(base, { duration: 60, id: CID });
    assert.equal(again.data.logged, false);
    assert.equal((await calls(base)).length, 1);
    assert.equal((await saved(base, "s-call")).filter((m) => m.meta.call).length, 1);
  });
});

test("不带 id 的旧调用照旧只记账、不落气泡", async () => {
  await withBridge(async (base) => {
    await chat(base, { message: "hi", mode: "core", voice: true, sessionId: "s-call" });
    await log(base, { duration: 60 });
    assert.equal((await saved(base, "s-call")).filter((m) => m.meta.call).length, 0);
  });
});

