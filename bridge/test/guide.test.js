/**
 * 手册给她看（Guide A3，2026-10-18）：bridge 两条只读代理。
 * 从使用方断言：读的是前端收到的响应。404（没这一篇）/ 503（Core 没载入）原样带回 —— 前端要分得清「读不到」和「没有」。
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, describe, test } from "node:test";

import { api, startBridge } from "./helpers.js";

let core;
let bridge;
let loaded = true;

before(async () => {
  const srv = http.createServer((req, res) => {
    const json = (code, o) => { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(o)); };
    const m = req.url.match(/^\/api\/nox\/guide(?:\/([^/?]+))?$/);
    if (!m) return json(404, { detail: "nope" });
    if (!loaded) return json(503, { detail: "手册没载入" });
    if (!m[1]) return json(200, { ok: true, items: [{ id: "memory", title: "记忆怎么运作", ask: "她问记不记得时翻" }] });
    if (m[1] === "memory") return json(200, { ok: true, id: "memory", title: "记忆怎么运作", ask: "", body: "你有三处记着东西的地方" });
    return json(404, { detail: "没有这一篇" });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  core = { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
  bridge = await startBridge({ NOX_CORE_URL: core.url });
});

after(async () => { await bridge?.stop(); await core?.close(); });

describe("手册代理", () => {
  test("目录和正文原样转", async () => {
    const list = await api(bridge.base, "/api/nox/guide");
    assert.equal(list.status, 200);
    assert.deepEqual(list.data.items.map((i) => i.id), ["memory"]);
    const one = await api(bridge.base, "/api/nox/guide/memory");
    assert.equal(one.data.body, "你有三处记着东西的地方");
  });

  test("没有这一篇 → 404", async () => {
    const r = await api(bridge.base, "/api/nox/guide/nope");
    assert.equal(r.status, 404);
    assert.equal(r.data.ok, false);
  });

  test("🔴 Core 没载入手册 → 503，不是空目录", async () => {
    loaded = false;
    try {
      const r = await api(bridge.base, "/api/nox/guide");
      assert.equal(r.status, 503);
      assert.match(r.data.error, /没载入/);
    } finally { loaded = true; }
  });

  test("要登录", async () => {
    const r = await api(bridge.base, "/api/nox/guide", { token: null });
    assert.equal(r.status, 403);
  });
});
