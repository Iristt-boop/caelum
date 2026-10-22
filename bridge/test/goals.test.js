/**
 * 目标层（2026-10-22，设计稿第三节）：立目标 → 拆出来的事落进 Todo（带 goal_id）→
 * 划掉一条 = 目标记一笔进展；删目标问连不连带 Todo。
 */
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";

import { api, startBridge } from "./helpers.js";

let bridge;
before(async () => { bridge = await startBridge({ NOX_GALLERY_BACKFILL_MS: "0" }); });
after(async () => { await bridge?.stop(); });

const post = (path, body) => api(bridge.base, path, { method: "POST", body });

async function makeGoal(extra = {}) {
  const r = await post("/api/goals", { title: "减脂", category: "健康", origin: "我要减肥",
                                       plan: ["定目标体重", { step: "每周三次力量", status: "doing" }], ...extra });
  assert.equal(r.status, 200);
  return r.data.id;
}

describe("立目标", () => {
  test("计划规整成 {step,status}；分类不认识就归其他", async () => {
    const id = await makeGoal({ category: "瞎写" });
    const g = (await api(bridge.base, `/api/goals/${id}`)).data.item;
    assert.equal(g.category, "其他");
    assert.deepEqual(g.plan, [{ step: "定目标体重", status: "todo" }, { step: "每周三次力量", status: "doing" }]);
    assert.equal(g.origin, "我要减肥");
  });

  test("没标题 400", async () => {
    assert.equal((await post("/api/goals", { title: " " })).status, 400);
  });
});

describe("和 Todo 结合", () => {
  test("Todo 带 goal_id 落进清单；目标卡上看得到它和本周进展", async () => {
    const id = await makeGoal();
    const t = await post("/api/today", { text: "力量训练", repeat: "weekly_count", times: 3, goal_id: id });
    assert.equal(t.status, 200);
    const items = (await api(bridge.base, "/api/todo/list")).data.items;
    assert.equal(items.find(x => x.id === t.data.id).goalId, id);
    const g = (await api(bridge.base, `/api/goals/${id}`)).data.item;
    assert.equal(g.todos.length, 1);
    assert.deepEqual(g.week, { done: 0, planned: 3 });
  });

  test("目标不存在就不落 Todo", async () => {
    const r = await post("/api/today", { text: "挂在空气上", goal_id: "nope" });
    assert.equal(r.status, 400);
  });

  test("划掉一条 → 目标记一笔「做完了」，同一天不重复记", async () => {
    const id = await makeGoal();
    const t = await post("/api/today", { text: "浇水test", repeat: "daily", goal_id: id });
    await api(bridge.base, `/api/today/${t.data.id}`, { method: "PATCH", body: { done: true } });
    await api(bridge.base, `/api/today/${t.data.id}`, { method: "PATCH", body: { done: false } });
    await post("/api/todo/complete", { keyword: "浇水test" });
    const g = (await api(bridge.base, `/api/goals/${id}`)).data.item;
    assert.deepEqual(g.log.filter(l => l.kind === "done").map(l => l.text), ["浇水test：做完了"]);
    assert.equal(g.week.done, 1);
  });

  test("两条划掉的路（页面勾选 / 他的 complete_todo）各自都记", async () => {
    const id = await makeGoal();
    const a = await post("/api/today", { text: "页面勾test", repeat: "daily", goal_id: id });
    await post("/api/today", { text: "他来划test", repeat: "daily", goal_id: id });
    await api(bridge.base, `/api/today/${a.data.id}`, { method: "PATCH", body: { done: true } });
    await post("/api/todo/complete", { keyword: "他来划test" });
    const g = (await api(bridge.base, `/api/goals/${id}`)).data.item;
    assert.deepEqual(g.log.map(l => l.text).sort(), ["他来划test：做完了", "页面勾test：做完了"]);
  });

  test("她自己的 Todo 划掉不碰任何目标", async () => {
    const t = await post("/api/today", { text: "自己的事test", repeat: "daily" });
    const r = await api(bridge.base, `/api/today/${t.data.id}`, { method: "PATCH", body: { done: true } });
    assert.equal(r.status, 200);
  });
});

describe("改 / 记 / 删", () => {
  test("改计划、暂停都记进展；deleted 只能走 DELETE", async () => {
    const id = await makeGoal();
    assert.equal((await api(bridge.base, `/api/goals/${id}`, { method: "PATCH", body: { plan: ["只剩一步"] } })).status, 200);
    assert.equal((await api(bridge.base, `/api/goals/${id}`, { method: "PATCH", body: { status: "paused" } })).status, 200);
    assert.equal((await api(bridge.base, `/api/goals/${id}`, { method: "PATCH", body: { status: "deleted" } })).status, 400);
    await post(`/api/goals/${id}/log`, { text: "体重 52.1" });
    const g = (await api(bridge.base, `/api/goals/${id}`)).data.item;
    assert.equal(g.status, "paused");
    assert.deepEqual(g.log.map(l => l.text).sort(), ["体重 52.1", "先停一停", "改了计划"].sort());
    // 暂停的不在默认列表，在 all 里
    assert.ok(!(await api(bridge.base, "/api/goals")).data.items.some(x => x.id === id));
    assert.ok((await api(bridge.base, "/api/goals?status=all")).data.items.some(x => x.id === id));
  });

  test("删目标不带 with_todos：Todo 留下变成她自己的", async () => {
    const id = await makeGoal();
    const t = await post("/api/today", { text: "留下来test", repeat: "daily", goal_id: id });
    const r = await api(bridge.base, `/api/goals/${id}`, { method: "DELETE" });
    assert.deepEqual(r.data, { ok: true, todos: 1, todosDeleted: false });
    const left = (await api(bridge.base, "/api/todo/list")).data.items.find(x => x.id === t.data.id);
    assert.equal(left.goalId, "");
    assert.equal((await api(bridge.base, `/api/goals/${id}`)).status, 404);
  });

  test("删目标 with_todos=1：连带删", async () => {
    const id = await makeGoal();
    const t = await post("/api/today", { text: "一起走test", repeat: "daily", goal_id: id });
    await api(bridge.base, `/api/goals/${id}?with_todos=1`, { method: "DELETE" });
    assert.ok(!(await api(bridge.base, "/api/todo/list")).data.items.some(x => x.id === t.data.id));
  });
});
