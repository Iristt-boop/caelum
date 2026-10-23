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
    // nox-core 算「这周落后了没」要的形状（attention/goal_nudge.py）
    const list = (await api(bridge.base, "/api/goals")).data;
    assert.ok(list.weekday >= 1 && list.weekday <= 7 && /^\d{4}-\d{2}-\d{2}$/.test(list.date));
    const tv = list.items.find(x => x.id === id).todos[0];
    assert.equal(tv.times, 3);
    assert.equal(tv.repeat, "weekly_count");
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

describe("他自己跟进的（owner=nox，10-23 她：最近的事是提醒他的，不是提醒她的）", () => {
  test("立的时候分得清是谁的；他的默认隔 2 天，夹在 1~30", async () => {
    const mine = (await post("/api/goals", { title: "跟进test", owner: "nox", origin: "姨妈推迟了" })).data.id;
    const odd = (await post("/api/goals", { title: "跟进2test", owner: "nox", every_days: 99 })).data.id;
    const hers = await makeGoal();
    const all = (await api(bridge.base, "/api/goals")).data.items;
    const by = Object.fromEntries(all.map((g) => [g.id, g]));
    assert.equal(by[mine].owner, "nox");
    assert.equal(by[mine].everyDays, 2);
    assert.equal(by[odd].everyDays, 30);
    assert.equal(by[hers].owner, "her");
    assert.equal(by[hers].everyDays, 0);
  });

  test("他的跟进不落进她的 Todo", async () => {
    const mine = (await post("/api/goals", { title: "跟进3test", owner: "nox" })).data.id;
    const r = await post("/api/today", { text: "问问姨妈test", goal_id: mine });
    assert.equal(r.status, 400);
    assert.ok(!(await api(bridge.base, "/api/todo/list")).data.items.some((x) => x.text === "问问姨妈test"));
  });
});

describe("到点他去做的事（routine，10-23）", () => {
  test("立的时候带上；时刻 / 星期规整；不合格 400 不悄悄存成没有", async () => {
    const id = (await post("/api/goals", { title: "每天新闻test", routine: { at: "09:00", weekdays: "7,1,1,9", task: "搜今天的 AI 新闻挑一两条讲给她" } })).data.id;
    const g = (await api(bridge.base, `/api/goals/${id}`)).data.item;
    assert.deepEqual(g.routine, { at: "09:00", weekdays: "1,7", task: "搜今天的 AI 新闻挑一两条讲给她" });
    assert.equal((await post("/api/goals", { title: "坏的test", routine: { at: "25:00", task: "x做事" } })).status, 400);
  });

  test("改 / 清掉", async () => {
    const id = (await post("/api/goals", { title: "改routine test" })).data.id;
    assert.equal((await api(bridge.base, `/api/goals/${id}`)).data.item.routine, null);
    await api(bridge.base, `/api/goals/${id}`, { method: "PATCH", body: { routine: { at: "21:30", task: "看看这周新片" } } });
    assert.deepEqual((await api(bridge.base, `/api/goals/${id}`)).data.item.routine, { at: "21:30", weekdays: "1,2,3,4,5,6,7", task: "看看这周新片" });
    assert.equal((await api(bridge.base, `/api/goals/${id}`, { method: "PATCH", body: { routine: { at: "9点" } } })).status, 400);
    await api(bridge.base, `/api/goals/${id}`, { method: "PATCH", body: { routine: null } });
    assert.equal((await api(bridge.base, `/api/goals/${id}`)).data.item.routine, null);
  });
});
