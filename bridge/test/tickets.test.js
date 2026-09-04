/**
 * 🎟️ 电影票根（2026-09-04）。
 *
 * 糖糖：「看完电影后共同看过的电影会生成一张票根」。
 *
 * ## 守的几条
 *
 * 1. **一场只能有一张** —— 她可能点两次生成，或者网络重试。
 *    不设主键的话同一场会出现两张一模一样的票。
 * 2. **只有电影出票** —— 她定的。其余类型只登记 kind，
 *    但也要登记，否则每次打开都会再弹一次问她。
 * 3. **看两分钟就关的不弹** —— 那不算一场，弹窗纯属打扰。
 * 4. 🔴 **拉不到元信息也要出票** —— 那张票根记的是「你们看过这场」，
 *    片名年份只是锦上添花。为了一次网络失败不给她票根是本末倒置。
 *
 * ⚠️ 这里**不打真网络**：测试跑在她的 Windows 上，而国内访问维基是 403。
 * 所以电影那条走「查不到」的路径，元信息为空 —— 正好也是最该守的那条。
 */
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";

import { api, startBridge } from "./helpers.js";

//: ⚠️ `api()` **不加 `/api` 前缀**，路径要写全。
//: 少写的话 fetch 会打到 SPA 兜底路由上，拿到一坨 HTML，
//: 于是 `r.data` 是 null，报错是「Cannot read properties of null」——
//: 那个报错指不到真因（2026-09-04 在这儿卡了一次）

let bridge;
const base = () => bridge.base;

before(async () => {
  bridge = await startBridge();
});
after(async () => {
  await bridge?.stop();
});

/** 造一场「看完了」的观影记录。 */
async function watched(id, title, minutes = 120) {
  const end = new Date();
  const start = new Date(end.getTime() - minutes * 60000);
  //: 先开一场
  await api(base(), "/api/watch/state", {
    method: "POST",
    body: { session_id: id, title, mode: "local", state: "playing" },
  });
  //: 再结束它
  await api(base(), "/api/watch/state", {
    method: "POST",
    body: { session_id: id, title, mode: "local", state: "ended" },
  });
  return { start, end };
}

describe("待处理的场次", () => {
  test("看完的场会出现在 pending 里", async () => {
    await watched("t-pending-1", "某部电影");
    const r = await api(base(), "/api/tickets/pending");
    assert.equal(r.status, 200);
    assert.ok(r.data.items.some((x) => x.id === "t-pending-1"));
  });

  test("出过票的不再出现", async () => {
    await watched("t-done-1", "看过的片");
    await api(base(), "/api/tickets", {
      method: "POST",
      body: { session_id: "t-done-1", kind: "movie", title: "看过的片" },
    });
    const r = await api(base(), "/api/tickets/pending");
    assert.ok(!r.data.items.some((x) => x.id === "t-done-1"));
  });

  test("🔴 选了「不是电影」的也不再出现", async () => {
    //: 只登记 kind、不出票，但必须记住"问过了" ——
    //: 不记的话每次打开都会再弹一次问她同一场
    await watched("t-tv-1", "某剧集");
    await api(base(), "/api/tickets", {
      method: "POST", body: { session_id: "t-tv-1", kind: "tv" },
    });
    const r = await api(base(), "/api/tickets/pending");
    assert.ok(!r.data.items.some((x) => x.id === "t-tv-1"));
  });
});

describe("生成票根", () => {
  test("电影：存下片名、影评、观看时长", async () => {
    await watched("t-movie-1", "绵羊侦探团", 95);
    const r = await api(base(), "/api/tickets", {
      method: "POST",
      body: {
        session_id: "t-movie-1", kind: "movie",
        title: "绵羊侦探团", review: "一群羊破案，比想象中好看。",
      },
    });
    assert.equal(r.status, 200);
    const t = r.data.ticket;
    assert.equal(t.title, "绵羊侦探团");
    assert.equal(t.review, "一群羊破案，比想象中好看。");
    assert.equal(t.kind, "movie");
    assert.ok(t.watched_minutes >= 0);
    assert.ok(t.watched_from && t.watched_to);
  });

  test("🔴 拉不到元信息照样出票", async () => {
    //: 测试环境没网（她机器上访问维基是 403），所以这条走的就是失败路径。
    //: **这正是最该守的一条** —— 为了一次网络失败不给她票根是本末倒置。
    await watched("t-movie-2", "一部查不到的片");
    const r = await api(base(), "/api/tickets", {
      method: "POST",
      body: { session_id: "t-movie-2", kind: "movie", title: "一部查不到的片", review: "还行" },
    });
    assert.equal(r.status, 200);
    assert.equal(r.data.ticket.title, "一部查不到的片");
    assert.equal(r.data.ticket.review, "还行");
  });

  test("🔴 同一场点两次只有一张票", async () => {
    await watched("t-dup-1", "重复的片");
    for (const rev of ["第一次写的", "第二次改的"]) {
      await api(base(), "/api/tickets", {
        method: "POST",
        body: { session_id: "t-dup-1", kind: "movie", title: "重复的片", review: rev },
      });
    }
    const r = await api(base(), "/api/tickets");
    const mine = r.data.items.filter((x) => x.session_id === "t-dup-1");
    assert.equal(mine.length, 1, "同一场出了两张票");
    //: 后写的覆盖前面的 —— 她改影评时该以最新的为准
    assert.equal(mine[0].review, "第二次改的");
  });

  test("没填片名就用共影记的那个", async () => {
    await watched("t-notitle-1", "共影记下的名字");
    const r = await api(base(), "/api/tickets", {
      method: "POST", body: { session_id: "t-notitle-1", kind: "movie" },
    });
    assert.equal(r.data.ticket.title, "共影记下的名字");
  });
});

describe("拒绝该拒绝的", () => {
  test("没有 session_id → 400", async () => {
    const r = await api(base(), "/api/tickets", { method: "POST", body: { kind: "movie" } });
    assert.equal(r.status, 400);
  });

  test("kind 不认识 → 400", async () => {
    await watched("t-badkind-1", "片");
    const r = await api(base(), "/api/tickets", {
      method: "POST", body: { session_id: "t-badkind-1", kind: "电影" },
    });
    assert.equal(r.status, 400);
  });

  test("不存在的场次 → 404", async () => {
    const r = await api(base(), "/api/tickets", {
      method: "POST", body: { session_id: "根本没这场", kind: "movie" },
    });
    assert.equal(r.status, 404);
  });
});

describe("票根列表", () => {
  test("🔴 只回电影 —— 别的类型没有票根可看", async () => {
    await watched("t-list-tv", "某综艺");
    await api(base(), "/api/tickets", {
      method: "POST", body: { session_id: "t-list-tv", kind: "variety" },
    });
    const r = await api(base(), "/api/tickets");
    assert.ok(r.data.items.every((x) => x.kind === "movie"));
    assert.ok(!r.data.items.some((x) => x.session_id === "t-list-tv"));
  });

  test("要鉴权", async () => {
    //: ⚠️ token 只能是 ASCII —— 中文塞进 HTTP 头会在 fetch 那一层就抛
    const r = await api(base(), "/api/tickets", { token: "wrong-token" });
    assert.ok(r.status === 401 || r.status === 403, `实际 ${r.status}`);
  });
});
