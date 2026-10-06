/**
 * Temporal P4：她说「哪天再做」→ 那天之前不追（2026-10-06）。
 *
 * 验的是「从 nox-core 那一侧看到的」：推迟之后 `/api/todo/due` 里就没有它了，
 * `/api/todo/list` 带着 deferredUntil；而且这个接口**只能往后推、碰不到完成**。
 */
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";

import { api, startBridge } from "./helpers.js";

let bridge;

const cn = (offsetDays = 0) => {
  const d = new Date(Date.now() + offsetDays * 86400000 + 8 * 3600000);
  return d.toISOString().slice(0, 10);
};

before(async () => { bridge = await startBridge(); });
after(async () => { await bridge?.stop(); });

async function addDaily(text) {
  // at=00:00 + 每天：任何时候测都已经「到点」
  const r = await api(bridge.base, "/api/today", { method: "POST", body: { text, repeat: "daily", at: "00:00" } });
  assert.ok(r.data?.id, `建不出待办：${JSON.stringify(r.data)}`);
  return r.data.id;
}

describe("待办推迟", () => {
  test("🔴 推到明天：今天到点也不追了；list 里看得见推到哪天", async () => {
    const id = await addDaily("臀腿训练");
    const due = async () => (await api(bridge.base, "/api/todo/due")).data.items.map((t) => t.id);
    assert.ok((await due()).includes(id), "前提不成立：本来就不在到点列表里");
    const r = await api(bridge.base, "/api/todo/defer", { method: "POST", body: { id, until: cn(1) } });
    assert.equal(r.status, 200);
    assert.ok(!(await due()).includes(id), "推迟了还在追");
    const item = (await api(bridge.base, "/api/todo/list")).data.items.find((t) => t.id === id);
    assert.equal(item.deferredUntil, cn(1));
  });

  test("只能往后推：今天、昨天、乱写都拒", async () => {
    const id = await addDaily("背英语单词");
    for (const until of [cn(0), cn(-1), "周五", ""]) {
      const r = await api(bridge.base, "/api/todo/defer", { method: "POST", body: { id, until } });
      assert.equal(r.status, 400, `until=${until} 被收了`);
    }
  });

  test("碰不到完成：推迟之后它还是没做完", async () => {
    const id = await addDaily("补充鱼油和维D");
    await api(bridge.base, "/api/todo/defer", { method: "POST", body: { id, until: cn(3) } });
    const item = (await api(bridge.base, "/api/todo/list")).data.items.find((t) => t.id === id);
    assert.ok(item, "推迟之后从未完成清单里消失了 —— 被当成完成了？");
  });

  test("没有这条 → 404", async () => {
    const r = await api(bridge.base, "/api/todo/defer", { method: "POST", body: { id: "nope", until: cn(1) } });
    assert.equal(r.status, 404);
  });
});
