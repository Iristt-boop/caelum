/**
 * 电影元信息（2026-09-04）—— 给票根填那几栏。
 *
 * ## 🔴 不打网络
 *
 * 全部注入 fetch。两个理由：
 *   1. 她的 Windows 在国内，**访问维基是 403**（实测）——
 *      真打网络的测试在她机器上永远红
 *   2. 维基条目会被人改，摘要一改断言就飘
 *
 * 下面每份假响应都是**从线上真实抓回来的形状**（2026-09-04 在 VPS 上跑的），
 * 不是我拼的。
 *
 * ## 守的核心
 *
 * **查不到不是错误。** 冷门片、片名打错、维基没收录都很正常，
 * 这时候要返回她填的那点东西、海报留空，**票根照样生成** ——
 * 那张票根记的是「你们看过这场」，元信息只是锦上添花。
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { lookupMovie, longEnough, _parseExtract as parse } from "../lib/movie-meta.js";

/** 造一个假 fetch：按 URL 关键词返回预设响应。 */
function fakeFetch(routes) {
  return async (url) => {
    for (const [key, val] of Object.entries(routes)) {
      if (url.includes(encodeURIComponent(key)) || url.includes(key)) {
        if (val === 404) return { ok: false, status: 404 };
        return { ok: true, status: 200, json: async () => val };
      }
    }
    return { ok: false, status: 404 };
  };
}

//: 线上真实响应的形状（龙猫）
const TOTORO = {
  type: "standard",
  titles: { display: '<span lang="zh"><span class="mw-page-title-main">龙猫</span></span>' },
  extract: "《龙猫》 是一部由吉卜力工作室与德间书店製作，宫崎骏執導，"
    + "於1988年4月16日首映的日本奇幻動畫電影。電影劇情以195…",
  thumbnail: { source: "https://upload.wikimedia.org/wikipedia/zh/0/02/My_Neighbor_Totoro.jpg" },
  content_urls: { desktop: { page: "https://zh.wikipedia.org/wiki/龙猫" } },
};

describe("摘要解析", () => {
  test("龙猫：年份/上映日/导演/类型/国家全捞出来", () => {
    const m = parse(TOTORO.extract);
    assert.equal(m.year, "1988");
    assert.equal(m.released, "1988-04-16");
    assert.equal(m.director, "宫崎骏");
    assert.equal(m.country, "日本");
    assert.ok(m.genres.includes("奇幻"));
  });

  test("🔴 类型繁简归一，不许出现「动畫」", () => {
    //: 第一版漏了「畫」，龙猫那张票根上就是半繁半简的「动畫」
    const m = parse(TOTORO.extract);
    for (const g of m.genres) {
      assert.ok(!/[劇動畫懸愛險戰爭紀錄樂傳記]/.test(g), `还有繁体：${g}`);
    }
    assert.ok(m.genres.includes("动画"));
  });

  test("🔴 导演不带「所」——那是助词不是名字", () => {
    //: 第一版票根上印的是「羅伯·雷納所」
    const m = parse("《怦然心动》是2010年美国爱情喜剧电影，由罗伯·雷纳所执导。");
    assert.equal(m.director, "罗伯·雷纳");
  });

  test("捞不到就是空，不编", () => {
    const m = parse("这是一段完全没有信息的文字。");
    assert.equal(m.year, "");
    assert.equal(m.director, "");
    assert.deepEqual(m.genres, []);
  });

  test("空摘要不炸", () => {
    for (const v of ["", null, undefined]) {
      const m = parse(v);
      assert.equal(m.year, "");
    }
  });

  test("类型最多三个", () => {
    const m = parse("剧情 科幻 动画 奇幻 悬疑 恐怖 爱情 动作 冒险");
    assert.ok(m.genres.length <= 3);
  });
});

describe("查一部电影", () => {
  test("直接命中", async () => {
    const m = await lookupMovie("龙猫", fakeFetch({ 龙猫: TOTORO }));
    assert.equal(m.title, "龙猫");
    assert.equal(m.year, "1988");
    assert.ok(m.poster.includes("Totoro"));
  });

  test("🔴 原名不许带 HTML 标签", async () => {
    //: `titles.display` 是带 `<span>` 的，直接用会把标签印在票根上
    const m = await lookupMovie("龙猫", fakeFetch({ 龙猫: TOTORO }));
    assert.ok(!m.original.includes("<"), `原名里有标签：${m.original}`);
  });

  test("🔴 原名不带消歧义后缀", async () => {
    //: `怦然心动 (电影)` 那个括号不是「原名」，印在票根上很怪
    const flipped = {
      type: "standard",
      titles: { display: "怦然心动 (电影)" },
      extract: "《怦然心动》是2010年美国电影。",
    };
    const m = await lookupMovie("怦然心动", fakeFetch({ 怦然心动: flipped }));
    assert.ok(!m.original.includes("("), `还带着后缀：${m.original}`);
    assert.ok(!m.original.includes("（"));
  });

  test("直查不中时用搜索找正确条目名", async () => {
    //: 实测：「怦然心动」直查 404，搜索能找到「怦然心动 (电影)」
    let hitSearch = false;
    const f = async (url) => {
      if (url.includes("list=search")) {
        hitSearch = true;
        return { ok: true, json: async () => ({
          query: { search: [
            { title: "怦然心动的人生整理魔法" },   //: 沾边的，不该被选中
            { title: "怦然心动 (电影)" },
          ] },
        }) };
      }
      if (url.includes(encodeURIComponent("怦然心动 (电影)"))) {
        return { ok: true, json: async () => ({
          type: "standard", titles: { display: "怦然心动 (电影)" },
          extract: "《怦然心动》是2010年美国电影。",
        }) };
      }
      return { ok: false, status: 404 };
    };
    const m = await lookupMovie("怦然心动", f);
    assert.ok(hitSearch, "该走搜索回退");
    assert.equal(m.year, "2010");
  });

  test("🔴 沾边的不算 —— 宁可查不到也不印错的", async () => {
    //: 第一版写的是 `startsWith(name)`，而
    //: 「怦然心动的人生整理魔法」也是以「怦然心动」开头的 ——
    //: 那条规则根本不区分，搜片名会拿到一本讲整理的书。
    //: **这条测试当场抓到了它。**
    const f = async (url) => {
      if (url.includes("list=search")) {
        return { ok: true, json: async () => ({
          query: { search: [{ title: "怦然心动的人生整理魔法" }] },
        }) };
      }
      if (url.includes(encodeURIComponent("怦然心动的人生整理魔法"))) {
        return { ok: true, json: async () => ({
          type: "standard", extract: "这是一本讲整理的书，2011年出版。",
        }) };
      }
      return { ok: false, status: 404 };
    };
    const m = await lookupMovie("怦然心动", f);
    assert.equal(m.year, "", "不该把那本书的年份印上去");
    assert.equal(m.title, "怦然心动");
  });

  test("`片名 (消歧义)` 这种形状要认", async () => {
    const f = async (url) => {
      if (url.includes("list=search")) {
        return { ok: true, json: async () => ({
          query: { search: [
            { title: "怦然心动的人生整理魔法" },
            { title: "怦然心动 (电影)" },
          ] },
        }) };
      }
      if (url.includes(encodeURIComponent("怦然心动 (电影)"))) {
        return { ok: true, json: async () => ({
          type: "standard", extract: "《怦然心动》是2010年美国电影。",
        }) };
      }
      return { ok: false, status: 404 };
    };
    assert.equal((await lookupMovie("怦然心动", f)).year, "2010");
  });
});

describe("🔴 查不到也要能出票", () => {
  test("维基 404 → 返回她填的片名，海报留空", async () => {
    const m = await lookupMovie("这部片根本不存在xyz", fakeFetch({}));
    assert.equal(m.title, "这部片根本不存在xyz");
    assert.equal(m.poster, "");
    assert.equal(m.year, "");
  });

  test("网络挂了 → 不抛，照样返回", async () => {
    const boom = async () => { throw new Error("ETIMEDOUT"); };
    const m = await lookupMovie("星际穿越", boom);
    assert.equal(m.title, "星际穿越");
    assert.equal(m.poster, "");
  });

  test("返回的不是 JSON → 不抛", async () => {
    const bad = async () => ({ ok: true, json: async () => { throw new Error("bad json"); } });
    const m = await lookupMovie("星际穿越", bad);
    assert.equal(m.title, "星际穿越");
  });

  test("消歧义页不算命中", async () => {
    const dis = { type: "disambiguation", extract: "可以指：1994年电影…" };
    const f = async (url) => (url.includes("list=search")
      ? { ok: true, json: async () => ({ query: { search: [] } }) }
      : { ok: true, json: async () => dis });
    const m = await lookupMovie("阿甘", f);
    assert.equal(m.year, "");
  });

  test("空片名不发请求", async () => {
    let called = false;
    const f = async () => { called = true; return { ok: false }; };
    for (const v of ["", "   ", null]) {
      const m = await lookupMovie(v, f);
      assert.equal(m.title, String(v || "").trim());
    }
    assert.equal(called, false);
  });
});

describe("🔴 看多久才算一场", () => {
  //: 接口那头的测试把门槛调成了 0（造不出长场次），
  //: 所以这条规则**只能在这儿守**。漏了的话，她点开两分钟就关，
  //: 也会被弹窗问「这是电影还是电视剧」——那是纯打扰。
  const t = (mins) => {
    const end = new Date("2026-09-04T20:00:00Z");
    return [new Date(end.getTime() - mins * 60000).toISOString(), end.toISOString()];
  };

  test("看够 10 分钟才问", () => {
    assert.equal(longEnough(...t(10)), true);
    assert.equal(longEnough(...t(120)), true);
  });

  test("不到 10 分钟不问", () => {
    assert.equal(longEnough(...t(9)), false);
    assert.equal(longEnough(...t(0)), false);
  });

  test("时间戳坏了当没看够 —— 不猜", () => {
    assert.equal(longEnough(null, null), false);
    assert.equal(longEnough("不是时间", "2026-09-04T20:00:00Z"), false);
    assert.equal(longEnough(undefined, undefined), false);
  });

  test("门槛可调", () => {
    assert.equal(longEnough(...t(5), 3), true);
    assert.equal(longEnough(...t(5), 30), false);
  });
});
