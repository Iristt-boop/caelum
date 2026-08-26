/**
 * Caelum Bridge 冒烟测试。
 *
 * ## 为什么现在才有（2026-08-18）
 *
 * `server.js` 十万字符以上，同时管静态托管、鉴权、SSE 代理、待办/日记/相册/
 * 饮食的 CRUD、TTS/ASR、推送、第三方代理 —— **一个测试都没有**。
 * 而 nox-core 那边有 732 个。
 *
 * 同一天里三次「差点打到糖糖手机上」的问题，**全部出在这一侧**：
 *
 *   1. `/api/todo/list` 的 sections 区名想改成「今天/近期/随时」——
 *      手机 App 和桌面端都照 `s["进行中"]` 取值，改了那两块当场变空**而且不报错**
 *   2. 手机 Home 的待办组件只读「进行中/近期」，而没设时间的待办全在「随时」
 *   3. 循环待办被勾掉时设了 `done=1`，「每天背单词」勾一次就永远消失
 *
 * 三个都是人肉发现的。这个文件就是为了让第四个别再靠人肉。
 *
 * ## 范围：三条主路径 + 鉴权
 *
 * 待办 / 会话 / 推送。**不测**外部依赖（Core、TTS、GitHub）——
 * 那些跑不起来也不该让这套测试变红。
 */
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

let bridge;
const base = () => bridge.base;

before(async () => {
  bridge = await startBridge();
});
after(async () => {
  await bridge?.stop();
});

// ---------------------------------------------------------------- 鉴权

describe("鉴权", () => {
  test("不带 token 一律 403", async () => {
    const r = await api(base(), "/api/todo/list", { token: null });
    assert.equal(r.status, 403);
  });

  test("token 不对也是 403", async () => {
    // 同 helpers.js：token 只能 ASCII，HTTP 头是 ByteString
    const r = await api(base(), "/api/todo/list", { token: "wrong-token" });
    assert.equal(r.status, 403);
  });

  test("token 对了才放行", async () => {
    const r = await api(base(), "/api/todo/list");
    assert.equal(r.status, 200);
  });
});

// ---------------------------------------------------------------- 待办

describe("待办", () => {
  test("清单的区名必须是「进行中 / 近期 / 随时」", async () => {
    // ⚠️ **这条是回归测试，不是形式主义。**
    // 手机 App 的 TodoWidget 和桌面端今日计划都是照 s["进行中"] 取值的，
    // 换个更贴切的名字（比如「今天」），她那两块会当场变空而且不报错。
    const r = await api(base(), "/api/todo/list");
    assert.equal(r.status, 200);
    assert.equal(r.data.ok, true);
    for (const key of ["进行中", "近期", "随时"]) {
      assert.ok(Array.isArray(r.data.sections[key]), `少了区：${key}`);
    }
  });

  test("没设时间的待办进「随时」，不占提醒名额", async () => {
    await api(base(), "/api/today", { method: "POST", body: { text: "买传感器" } });
    const r = await api(base(), "/api/todo/list");
    assert.ok(r.data.sections["随时"].some((x) => x.includes("买传感器")));

    const due = await api(base(), "/api/todo/due");
    assert.equal(due.data.items.length, 0, "没有时刻的不该被追");
  });

  test("每天循环：进「进行中」，描述带「每天」", async () => {
    await api(base(), "/api/today", {
      method: "POST",
      body: { text: "背单词", repeat: "daily", at: "18:00" },
    });
    const r = await api(base(), "/api/todo/list");
    const hit = r.data.items.find((i) => i.text === "背单词");
    assert.equal(hit.bucket, "进行中");
    assert.equal(hit.repeat, "daily");
    assert.ok(hit.when.includes("每天"), `描述不对：${hit.when}`);
  });

  test("每周 N 次：描述里带「这周已 N 次」", async () => {
    await api(base(), "/api/today", {
      method: "POST",
      body: { text: "健身", repeat: "weekly_count", times: 3, at: "20:00" },
    });
    const r = await api(base(), "/api/todo/list");
    const hit = r.data.items.find((i) => i.text === "健身");
    assert.equal(hit.repeat, "weekly_count");
    assert.equal(hit.times, 3);
    assert.equal(hit.doneThisWeek, 0);
    assert.ok(hit.when.includes("每周 3 次"), `描述不对：${hit.when}`);
  });

  test("循环任务勾掉只记一笔，**不会永久消失**", async () => {
    // 2026-08-18 修的：原来设 done=1，「每天背单词」勾一次第二天他就不提了
    const done = await api(base(), "/api/todo/complete", {
      method: "POST",
      body: { keyword: "背单词" },
    });
    assert.equal(done.data.ok, true);
    assert.equal(done.data.closed, false, "循环任务不该被永久划掉");

    const r = await api(base(), "/api/todo/list");
    assert.ok(
      r.data.items.some((i) => i.text === "背单词"),
      "勾完就不见了 —— 明天他不会再提醒她",
    );
  });

  test("每周 N 次勾一次，配额 +1", async () => {
    const r1 = await api(base(), "/api/todo/complete", {
      method: "POST", body: { keyword: "健身" },
    });
    assert.equal(r1.data.doneThisWeek, 1);
    const r2 = await api(base(), "/api/todo/list");
    assert.equal(r2.data.items.find((i) => i.text === "健身").doneThisWeek, 1);
  });

  test("一次性任务勾掉就是划掉", async () => {
    await api(base(), "/api/today", {
      method: "POST", body: { text: "预约复查", repeat: "once", at: "09:00" },
    });
    const done = await api(base(), "/api/todo/complete", {
      method: "POST", body: { keyword: "预约复查" },
    });
    assert.equal(done.data.closed, true);
    const r = await api(base(), "/api/todo/list");
    assert.ok(!r.data.items.some((i) => i.text === "预约复查"));
  });

  test("找不到的关键词要如实说，不能假装划掉了", async () => {
    const r = await api(base(), "/api/todo/complete", {
      method: "POST", body: { keyword: "根本没有这条" },
    });
    assert.equal(r.data.ok, false);
    assert.ok(r.data.error.includes("没找到"));
  });

  test("/api/todo/due 只回**时刻已过**的", async () => {
    await api(base(), "/api/today", {
      method: "POST", body: { text: "凌晨那条", repeat: "daily", at: "00:01" },
    });
    await api(base(), "/api/today", {
      method: "POST", body: { text: "深夜那条", repeat: "daily", at: "23:59" },
    });
    const due = await api(base(), "/api/todo/due");
    const names = due.data.items.map((i) => i.text);
    assert.ok(names.includes("凌晨那条"), "00:01 早该到点了");
    assert.ok(!names.includes("深夜那条"), "23:59 还没到，不该追");
  });

  test("追过之后标 fired，同一天不再重复追", async () => {
    const due = await api(base(), "/api/todo/due");
    const one = due.data.items[0];
    assert.ok(one, "应该有到期的条目");

    await api(base(), "/api/todo/fired", { method: "POST", body: { id: one.id } });
    const again = await api(base(), "/api/todo/due");
    assert.ok(
      !again.data.items.some((i) => i.id === one.id),
      "标了 fired 还在回 —— 今天会被追第二轮",
    );
  });

  test("PATCH 取消勾选，条目回到清单", async () => {
    const add = await api(base(), "/api/today", {
      method: "POST", body: { text: "临时一条", repeat: "once", at: "08:00" },
    });
    await api(base(), `/api/today/${add.data.id}`, { method: "PATCH", body: { done: true } });
    let r = await api(base(), "/api/todo/list");
    assert.ok(!r.data.items.some((i) => i.text === "临时一条"));

    await api(base(), `/api/today/${add.data.id}`, { method: "PATCH", body: { done: false } });
    r = await api(base(), "/api/todo/list");
    assert.ok(r.data.items.some((i) => i.text === "临时一条"), "取消勾选没回来");
  });

  test("空正文不许建", async () => {
    const r = await api(base(), "/api/today", { method: "POST", body: { text: "" } });
    assert.equal(r.status, 400);
  });
});

// ---------------------------------------------------------------- 会话

describe("会话", () => {
  test("空库时 /api/conv-sessions 回空数组，不是报错", async () => {
    const r = await api(base(), "/api/conv-sessions");
    assert.equal(r.status, 200);
    assert.ok(Array.isArray(r.data));
  });

  test("/api/messages 带 sessionId 回数组", async () => {
    const r = await api(base(), "/api/messages?sessionId=" + "a".repeat(32));
    assert.equal(r.status, 200);
    assert.ok(Array.isArray(r.data));
  });

  test("会话列表只认 32 位纯十六进制的 id", async () => {
    // 白名单而不是黑名单（2026-08-02 的教训：一条条排除测试前缀总会漏）
    await api(base(), "/api/push/send", {
      method: "POST",
      body: { body: "手写 id 的消息", session_id: "test-手写的-不该出现" },
    });
    const good = "b".repeat(32);
    await api(base(), "/api/push/send", {
      method: "POST", body: { body: "正经会话的消息", session_id: good },
    });

    const r = await api(base(), "/api/conv-sessions");
    const ids = r.data.map((s) => s.id);
    assert.ok(ids.includes(good), "32 位 hex 的会话该出现");
    assert.ok(
      !ids.some((i) => i.startsWith("test-")),
      "手写 id 混进了她的会话列表",
    );
  });

  test("🔴 preview 是最后一句，不是第一句", async () => {
    // 2026-08-25 糖糖指出来的：「最近对话」显示的是会话的**开场白**，
    // 时间戳却是最后活动时间。一条聊了 1779 条消息的会话，
    // 永远挂着几个月前那句话配一个刚刚的时间 ——「这句话也不是最近对话啊」。
    const sid = "c".repeat(32);
    for (const body of ["第一句", "中间那句", "最后一句"]) {
      await api(base(), "/api/push/send", { method: "POST", body: { body, session_id: sid } });
    }

    const r = await api(base(), "/api/conv-sessions");
    const row = r.data.find((s) => s.id === sid);
    assert.ok(row, "这条会话该在列表里");
    assert.equal(row.preview, "最后一句");
    // title 保留原来的语义 —— 手机端那个 Caelum App 还拿它当标题
    assert.ok(row.title !== "最后一句", "title 不该跟着变成最后一句");
  });

  test("preview 是谁说的要报出来", async () => {
    // 不报的话，最后一句是他说的也会挂糖糖的头像
    const sid = "d".repeat(32);
    // push/send 落库的是 assistant
    await api(base(), "/api/push/send", {
      method: "POST", body: { body: "他主动说的", session_id: sid },
    });
    const r = await api(base(), "/api/conv-sessions");
    assert.equal(r.data.find((s) => s.id === sid)?.previewRole, "nox");
  });
});

// ---------------------------------------------------------------- 推送

describe("主动推送", () => {
  test("带 session_id 时**先落库再推**", async () => {
    // 糖糖 2026-08-04 当场纠正的：不落库的话，
    // 锁屏弹一句话、点进去聊天里什么都没有
    const sid = "c".repeat(32);
    const r = await api(base(), "/api/push/send", {
      method: "POST", body: { body: "我想你了", session_id: sid },
    });
    assert.equal(r.status, 200);
    assert.equal(r.data.saved, true, "没落库 —— 她点进去会看到空的");

    const msgs = await api(base(), `/api/messages?sessionId=${sid}`);
    const hit = msgs.data.find((m) => m.content === "我想你了");
    assert.ok(hit, "conversations 里没有这条");
    assert.equal(hit.role, "assistant");
    assert.ok(
      String(hit.metadata || "").includes("proactive"),
      "没打 proactive 标记 —— 桌面端认不出这是他主动说的",
    );
  });

  test("没有订阅不算错误，如实回报条数", async () => {
    const r = await api(base(), "/api/push/send", {
      method: "POST", body: { body: "没人订阅的一句话" },
    });
    assert.equal(r.status, 200);
    assert.equal(r.data.subs, 0, "应该如实说 0 个订阅，而不是假装成功");
  });

  test("空 body 要拒绝", async () => {
    const r = await api(base(), "/api/push/send", { method: "POST", body: { body: "  " } });
    assert.equal(r.status, 400);
  });
});

// ---------------------------------------------------------------- 共影

describe("共影：她在不在看片", () => {
  test("没心跳的时候 watching 是 false", async () => {
    const r = await api(base(), "/api/watch/state");
    assert.equal(r.status, 200);
    assert.equal(r.data.watching, false);
    assert.equal(r.data.session, null);
  });

  test("心跳之后 watching 是 true，带着片名和进度", async () => {
    await api(base(), "/api/watch/state", {
      method: "POST",
      body: { session_id: "w1", title: "测试片", mode: "stream", duration_s: 600, position_ms: 12000 },
    });
    const r = await api(base(), "/api/watch/state");
    assert.equal(r.data.watching, true);
    assert.equal(r.data.session.title, "测试片");
    assert.equal(r.data.session.position_ms, 12000);
  });

  test("后续心跳不许覆盖 started_at", async () => {
    // started_at 是这一场的开头。被心跳一路推着走的话，
    // 「一起看了多久」永远算出来是 0
    const first = await api(base(), "/api/watch/state");
    const startedAt = first.data.session.started_at;
    await new Promise((r) => setTimeout(r, 30));
    await api(base(), "/api/watch/state", {
      method: "POST",
      body: { session_id: "w1", title: "测试片", position_ms: 45000 },
    });
    const again = await api(base(), "/api/watch/state");
    assert.equal(again.data.session.started_at, startedAt, "started_at 被心跳改掉了");
    assert.equal(again.data.session.position_ms, 45000, "进度该跟着走");
  });

  test("心跳停了就当她走开了，但要说得出是哪一场", async () => {
    // 关浏览器不会发 ended。过期之后 watching 必须是 false ——
    // 否则 Care 会以为她还在看片，从此再也不开口
    await new Promise((r) => setTimeout(r, 1200)); // 测试里 WATCH_STALE_MS=1000
    const r = await api(base(), "/api/watch/state");
    assert.equal(r.data.watching, false);
    assert.equal(r.data.stale_session?.id, "w1", "「走开了」和「从来没看过」不是一回事");
  });

  test("发了 ended 就真的结束，不再是 stale", async () => {
    await api(base(), "/api/watch/state", {
      method: "POST",
      body: { session_id: "w1", title: "测试片", state: "ended", position_ms: 60000 },
    });
    const r = await api(base(), "/api/watch/state");
    assert.equal(r.data.watching, false);
    assert.equal(r.data.stale_session, null, "结束了就不该再挂在「没结束」那条上");
  });

  test("看过的进历史", async () => {
    const r = await api(base(), "/api/watch/history");
    assert.equal(r.status, 200);
    const row = r.data.items.find((x) => x.id === "w1");
    assert.ok(row, "这一场该在历史里");
    assert.equal(row.title, "测试片");
    assert.ok(row.ended_at, "结束时间该记下来");
  });

  test("没有 session_id 就是 400，不许静默建一条空记录", async () => {
    const r = await api(base(), "/api/watch/state", { method: "POST", body: { title: "没有 id" } });
    assert.equal(r.status, 400);
  });
});

// ---------------------------------------------------------------- 外部依赖挂了

describe("外部依赖挂掉时不许白屏", () => {
  test("Core 连不上，/api/nox/state 回 ok:false 而不是 5xx", async () => {
    // 工作台只是展示，不能因为 Core 抽风让整页白掉
    const r = await api(base(), "/api/nox/state");
    assert.equal(r.status, 200);
    assert.equal(r.data.ok, false);
    assert.ok(r.data.error, "该说明为什么读不到");
  });

  test("Core 连不上，/api/nox/day 回空时间线而不是 5xx", async () => {
    // 「他的一天」是展示页，Core 抽风不该让她看到一页报错
    const r = await api(base(), "/api/nox/day");
    assert.equal(r.status, 200);
    assert.equal(r.data.ok, false);
    assert.deepEqual(r.data.events, []);
  });

  test("健康库不存在时回 null，不是崩", async () => {
    const r = await api(base(), "/api/health/latest");
    assert.equal(r.status, 200);
    assert.equal(r.data.health, null);
  });
});
