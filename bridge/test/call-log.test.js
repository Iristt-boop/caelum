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

import { api, startBridge } from "./helpers.js";

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
