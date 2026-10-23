/**
 * 她看我屏幕（Nox 的电脑 v1.5，10-23）的代理：胶囊状态 / 实时画面 / 喊停 / 截图，原样转给 nox-core。
 * 喊停必须是 POST 真到 Core；没有的截图回 404（不是 502 —— 小卡那页就显示没图，不当成断线）。
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, test } from "node:test";

import { api, startBridge } from "./helpers.js";

let core; let bridge; const seen = [];

before(async () => {
  const srv = http.createServer((req, res) => {
    seen.push({ m: req.method, u: req.url });
    const missing = req.url.startsWith("/api/nox/desk/shot/nope");
    res.writeHead(missing ? 404 : 200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(missing ? { detail: "没有这张" }
      : req.url.endsWith("/status") ? { ok: true, active: true, doing: "在查 VPS 的价格", pages: 2 }
        : { ok: true, image: "data:image/jpeg;base64,QUJD" }));
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  core = { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
  bridge = await startBridge({ NOX_CORE_URL: core.url, NOX_GALLERY_BACKFILL_MS: "0" });
});
after(async () => { await bridge?.stop(); await core?.close(); });

test("胶囊状态：原样转", async () => {
  const r = await api(bridge.base, "/api/nox/desk/status");
  assert.equal(r.status, 200);
  assert.equal(r.data.doing, "在查 VPS 的价格");
});

test("实时画面 / 截图：图在 JSON 里", async () => {
  assert.equal((await api(bridge.base, "/api/nox/desk/frame")).data.image, "data:image/jpeg;base64,QUJD");
  const s = await api(bridge.base, "/api/nox/desk/shot/1800000000-abcdef12");
  assert.equal(s.data.image, "data:image/jpeg;base64,QUJD");
  assert.deepEqual(seen.at(-1), { m: "GET", u: "/api/nox/desk/shot/1800000000-abcdef12" });
});

test("喊停：POST 真到 Core", async () => {
  const r = await api(bridge.base, "/api/nox/desk/stop", { method: "POST", body: {} });
  assert.equal(r.status, 200);
  assert.deepEqual(seen.at(-1), { m: "POST", u: "/api/nox/desk/stop" });
});

test("没有的截图回 404，不压成 502", async () => {
  assert.equal((await api(bridge.base, "/api/nox/desk/shot/nope")).status, 404);
});

test("要鉴权：不带 token 进不来", async () => {
  const r = await fetch(`${bridge.base}/api/nox/desk/frame`);
  assert.equal(r.status, 403);
});
