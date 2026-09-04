/**
 * SPA 兜底路由：**静态资源找不到要 404，不许回 index.html**。
 *
 * ## 这条测试守的是一次真事故（2026-09-04）
 *
 * 糖糖：「app 现在渲染有问题啦，发出去的消息和他回复我的都不显示了」。
 *
 * 根因不在 Chat 那些代码里，在这条兜底路由：
 *
 *   她开着 app（已加载 index-旧.js）→ 点进 Chat
 *   → 浏览器取 Chat-旧哈希.js → 那个文件在部署时被换掉了
 *   → 兜底路由把 index.html 当结果返回，**状态码 200**
 *   → 浏览器以为拿到了 JS，解析 `<!doctype html>` → 语法错误
 *   → Chat 那个模块加载失败 → **整页不渲染**
 *
 * 最坑的是它**不报「文件没了」**，报的是一个指不到真因的语法错误 ——
 * 所以查了很久才查到这儿。回 404 的话浏览器会说「chunk 加载失败」。
 *
 * ⚠️ 这是**每次部署前端都会踩**的雷，只要她当时开着 app。
 * 不是那天才有的偶发问题，只是那天我一下午换了四次产物才撞出来。
 */
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";

import { startBridge } from "./helpers.js";

let bridge;
const base = () => bridge.base;

before(async () => {
  bridge = await startBridge();
});
after(async () => {
  await bridge?.stop();
});

/** 直接取，不走 api() —— 这几条要看的是原始状态码和 content-type。 */
async function raw(path) {
  const r = await fetch(`${base()}${path}`);
  return { status: r.status, type: r.headers.get("content-type") || "" };
}

describe("SPA 兜底不许吞掉静态资源", () => {
  test("不存在的 js 分包 → 404，而不是 200 的 HTML", async () => {
    const r = await raw("/assets/Chat-DOESNOTEXIST.js");
    assert.equal(r.status, 404, "回 200 的话浏览器会把 HTML 当 JS 解析");
    assert.ok(!r.type.includes("text/html"), `不该是 HTML，实际 ${r.type}`);
  });

  test("各种静态后缀都一样", async () => {
    //: `/assets/` 之外还有 icons、manifest 这些，同样不该被兜底成 HTML
    for (const p of [
      "/assets/style-NOPE.css",
      "/assets/app-NOPE.js.map",
      "/icons/nope.png",
      "/fonts/nope.woff2",
      "/manifest-nope.webmanifest",
    ]) {
      const r = await raw(p);
      assert.equal(r.status, 404, `${p} 该 404，实际 ${r.status}`);
    }
  });

  test("🔴 真正的页面路由仍然要拿到 index.html", async () => {
    //: 这才是兜底路由存在的理由 —— 修上面那条不能把它一起修坏。
    //  页面路由没有后缀，这就是判据。
    //: ⚠️ 别拿 `/health` 举例 —— 那是服务器的健康检查端点，
    //  兜底路由本来就明确排除它（见 server.js 那行 next()）
    for (const p of ["/chat", "/diary", "/gallery", "/"]) {
      const r = await raw(p);
      assert.equal(r.status, 200, `${p} 该拿到页面，实际 ${r.status}`);
      assert.ok(r.type.includes("text/html"), `${p} 该是 HTML，实际 ${r.type}`);
    }
  });

  test("带点的页面路由不该被误判成静态资源", async () => {
    //: 万一哪天有 `/books/三体.第一部` 这种路由，后缀不在白名单里，
    //  它该照常拿到页面。判据是**已知的静态后缀**，不是"含不含点"
    const r = await raw("/books/三体.第一部");
    assert.equal(r.status, 200);
    assert.ok(r.type.includes("text/html"));
  });

  test("/api 找不到时不受影响", async () => {
    //: 那条 next() 的分支不能被这次改动碰到
    const r = await raw("/api/nope-not-a-real-route");
    assert.ok(!r.type.includes("text/html"), "API 404 不该回 HTML");
  });
});
