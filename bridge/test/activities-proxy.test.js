/**
 * 他自己的时间（V5，10-06）的代理：参数收口、状态码原样（503 不能变成空列表）。
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, test } from "node:test";

import { api, startBridge } from "./helpers.js";

let core; let bridge; const seen = []; let status = 200;

before(async () => {
  const srv = http.createServer((req, res) => {
    seen.push(req.url);
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(status === 200 ? { ok: true, items: [{ what: "回了个帖" }] } : { detail: "活动日志没起来" }));
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  core = { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
  bridge = await startBridge({ NOX_CORE_URL: core.url, NOX_GALLERY_BACKFILL_MS: "0" });
});
after(async () => { await bridge?.stop(); await core?.close(); });

test("转给 nox-core，参数收口", async () => {
  const r = await api(bridge.base, "/api/nox/activities?days=7&date=2026-10-07");
  assert.equal(r.data.items[0].what, "回了个帖");
  await api(bridge.base, "/api/nox/activities?days=999&date=../etc");
  assert.deepEqual(seen, ["/api/nox/activities?days=7&date=2026-10-07", "/api/nox/activities?days=31"]);
});

test("503 原样回，不是空列表", async () => {
  status = 503;
  const r = await api(bridge.base, "/api/nox/activities");
  assert.equal(r.status, 503);
  status = 200;
});
