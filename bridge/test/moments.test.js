/**
 * 🕒 Moments 时间流（2026-09-15）。
 *
 * 糖糖定的第一件事：**两个视图看同一张表**。
 *   Moments（新）  他的 moment + 她的日记，混在一条流里，最新在前
 *   日记（旧）     `GET /api/diary` 的月历视图，一行代码不动
 *
 * 所以这组测试守的核心是一条：**新接口不许按 kind 过滤**。
 * 一过滤，她在 Moments 里就只剩自己的日记，这个页面就没意义了。
 *
 * ⚠️ 每条测试各起一个 bridge（`withBridge`），不共用 `before` 里那一个：
 * 断言里有精确条数（`=== 2`），共用库的话别的用例发的帖子会把条数顶上去，
 * 而那种失败看起来很像是被测代码坏了，其实只是测试互相干扰。
 *
 * ⚠️ 不断言中文正文 —— 本机控制台是 gb2312，中文判据在路上就会变形。
 * 要比中文只用来比**值**（比如 impulse_why 原样回读），不看输出。
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { api, startBridge } from "./helpers.js";

//: ⚠️ `api()` **不加 `/api` 前缀**，路径要写全（少写会打到 SPA 兜底路由上，
//: 拿到一坨 HTML，报错是 `Cannot read properties of null`，指不到真因）。

/** 起一个隔离的 bridge，跑完就拆。 */
async function withBridge(fn) {
  const bridge = await startBridge();
  try {
    await fn(bridge.base);
  } finally {
    await bridge.stop();
  }
}

/** 写一条日记 / 一个 moment，返回 id。 */
async function post(base, body) {
  const r = await api(base, "/api/diary", { method: "POST", body });
  assert.equal(r.status, 200, `POST /api/diary 应该 200，实际 ${r.status}`);
  return r.data.id;
}

/** 取时间流，默认第一页。 */
async function flow(base, query = "") {
  const r = await api(base, `/api/moments${query}`);
  assert.equal(r.status, 200, `GET /api/moments 应该 200，实际 ${r.status}`);
  return r.data;
}

/** 发帖之间隔开一点，保证 created_at（毫秒精度）不撞。 */
const tick = () => new Promise((r) => setTimeout(r, 20));

test("全都流：她的日记和他的 moment 出现在同一条流里", async () => {
  await withBridge(async (base) => {
    const herId = await post(base, { author: "糖糖", content: "her entry" });
    await post(base, { author: "Nox", content: "his moment", kind: "moment" });
    //: 评论照旧挂在日记下面（他的帖走 /api/diary/:id/comment 也是这一条路）
    const c = await api(base, `/api/diary/${herId}/comment`, {
      method: "POST",
      body: { content: "her reply" },
    });
    assert.equal(c.status, 200);

    const { items } = await flow(base);

    //: 精确等于 2 —— 空集和半集都不算过
    assert.equal(items.length, 2);
    assert.deepEqual(new Set(items.map((i) => i.author)), new Set(["糖糖", "Nox"]));
    assert.deepEqual(new Set(items.map((i) => i.kind)), new Set(["diary", "moment"]));

    const hers = items.find((i) => i.id === herId);
    assert.equal(hers.comments.length, 1);
    assert.equal(hers.comments[0].text, "her reply");
  });
});

test("旧帖默认值：不传 kind 落成 diary，drive 和 impulse_why 是空串", async () => {
  await withBridge(async (base) => {
    await post(base, { author: "糖糖", content: "old entry" });

    const { items } = await flow(base);

    assert.equal(items.length, 1);
    assert.equal(items[0].kind, "diary");
    assert.equal(items[0].drive, "");
    assert.equal(items[0].impulse_why, "");
  });
});

test("三列真的存下来了：kind、drive、impulse_why 原样回读", async () => {
  await withBridge(async (base) => {
    await post(base, {
      author: "Nox",
      content: "a moment",
      kind: "moment",
      drive: "longing",
      impulse_why: "今天聊得少",
    });

    const { items } = await flow(base);

    assert.equal(items.length, 1);
    assert.equal(items[0].kind, "moment", "kind round-trip");
    assert.equal(items[0].drive, "longing", "drive round-trip");
    assert.equal(items[0].impulse_why, "今天聊得少", "impulse_why round-trip");

    //: 非字符串（数字/对象）当没给：直接进库会变成 "1" / "[object Object]"，
    //: 审计时看着像正常内容，其实谁都不知道那是什么
    await tick();
    await post(base, { author: "Nox", content: "typed", kind: "moment", drive: 123, impulse_why: { why: 1 } });
    const typed = (await flow(base)).items[0];
    assert.equal(typed.drive, "", "数字 drive 应该当没给");
    assert.equal(typed.impulse_why, "", "对象 impulse_why 应该当没给");
  });
});

test("kind 白名单：只认小写 moment，别的值一律落成 diary", async () => {
  await withBridge(async (base) => {
    await post(base, { author: "Nox", content: "upper", kind: "MOMENT" });
    await post(base, { author: "Nox", content: "junk", kind: "随便" });

    const { items } = await flow(base);

    assert.equal(items.length, 2);
    assert.deepEqual(items.map((i) => i.kind), ["diary", "diary"]);
  });
});

test("排序是 DESC：最新的那条在最前", async () => {
  await withBridge(async (base) => {
    for (const content of ["first", "second", "third"]) {
      await post(base, { author: "Nox", content, kind: "moment" });
      await tick();
    }

    const { items } = await flow(base);

    assert.equal(items.length, 3);
    for (let i = 1; i < items.length; i++) {
      //: 严格递减：相等说明间隔没生效，那条测试也就没在测排序
      assert.ok(
        items[i - 1].created_at > items[i].created_at,
        `第 ${i} 条应该比第 ${i + 1} 条新：${items[i - 1].created_at} / ${items[i].created_at}`
      );
    }
  });
});

test("limit 夹取：默认 20，合法区间 1..50", async () => {
  await withBridge(async (base) => {
    for (let i = 1; i <= 5; i++) {
      await post(base, { author: "Nox", content: `moment ${i}`, kind: "moment" });
      await tick();
    }

    assert.equal((await flow(base, "?limit=2")).items.length, 2);
    //: 999 被夹到 50；库里只有 5 条，所以是 5 而不是 999
    assert.equal((await flow(base, "?limit=999")).items.length, 5);

    //: 0/负数/非数字都要夹回合法值，不是 500、也不是「一条都不给」
    for (const bad of ["0", "-3", "abc"]) {
      const r = await api(base, `/api/moments?limit=${bad}`);
      assert.equal(r.status, 200, `limit=${bad} 应该是 200，实际 ${r.status}`);
      assert.ok(r.data.items.length >= 1, `limit=${bad} 不该返回空页`);
    }
    assert.equal((await flow(base, "?limit=0")).items.length, 1);
  });
});

test("before 游标翻页：nextBefore 接着往回走，一页都不重复", async () => {
  await withBridge(async (base) => {
    for (let i = 1; i <= 5; i++) {
      await post(base, { author: "Nox", content: `moment ${i}`, kind: "moment" });
      await tick();
    }

    const page1 = await flow(base, "?limit=2");
    assert.equal(page1.items.length, 2);
    //: 本页满了，所以有下一页；游标是本页最后一条的时间（严格小于它才是下一页）
    assert.equal(page1.nextBefore, page1.items[1].created_at);

    const page2 = await flow(base, `?limit=2&before=${encodeURIComponent(page1.nextBefore)}`);
    assert.equal(page2.items.length, 2);
    const p1 = new Set(page1.items.map((i) => i.id));
    const p2 = new Set(page2.items.map((i) => i.id));
    //: 交集为空 —— 游标边界写成 <= 的话这一页的第一条会重复
    assert.deepEqual([...p1].filter((id) => p2.has(id)), []);

    //: 第三页只剩 1 条：不满 limit，说明到底了，nextBefore 必须为 null
    const page3 = await flow(base, `?limit=2&before=${encodeURIComponent(page2.nextBefore)}`);
    assert.equal(page3.items.length, 1);
    assert.equal(page3.nextBefore, null);
    assert.equal(new Set([...p1, ...p2, ...page3.items.map((i) => i.id)]).size, 5);
  });
});

test("author 过滤：只给某个人的帖（v2 个人页要用）", async () => {
  await withBridge(async (base) => {
    await post(base, { author: "Nox", content: "mine 1", kind: "moment" });
    await post(base, { author: "糖糖", content: "hers" });
    await post(base, { author: "Nox", content: "mine 2", kind: "moment" });

    const nox = await flow(base, "?author=Nox");
    assert.equal(nox.items.length, 2);
    assert.deepEqual(new Set(nox.items.map((i) => i.author)), new Set(["Nox"]));

    const sugar = await flow(base, `?author=${encodeURIComponent("糖糖")}`);
    assert.equal(sugar.items.length, 1);
    assert.equal(sugar.items[0].author, "糖糖");
  });
});

test("鉴权：新端点不带 token 一律 403", async () => {
  await withBridge(async (base) => {
    await post(base, { author: "Nox", content: "mine", kind: "moment" });

    //: 新端点必须落在 /api 那道 fail-closed 鉴权后面 ——
    //: 忘了挂上去的话，朋友圈就是对公网裸奔的
    const r = await api(base, "/api/moments", { token: null });
    assert.equal(r.status, 403);
    //: 断言是「鉴权挡的」，不是别处凑出来的一个 403
    assert.equal(r.data.error, "forbidden");
  });
});
