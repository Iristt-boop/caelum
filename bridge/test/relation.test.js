/**
 * 关系状态的确认卡和列表代理（10-06，《Caelum-关系状态-设计稿》）。
 *   ① nox-core 调 /api/relation/card → 那条会话里多一条 meta.relation 的消息，**不推通知**
 *   ② /api/nox/relation* 原样转给 nox-core（状态码也原样：409 不能变成 200）
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, describe, test } from "node:test";

import { api, startBridge } from "./helpers.js";

let core; let bridge; const seen = [];

before(async () => {
  const srv = http.createServer((req, res) => {
    seen.push(`${req.method} ${req.url}`);
    res.writeHead(req.url.endsWith("/reject") ? 409 : 200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(req.url.endsWith("/reject")
      ? { detail: "已经处理过了" } : { ok: true, items: [{ id: "r1", status: "pending" }] }));
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  core = { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
  bridge = await startBridge({ NOX_CORE_URL: core.url, NOX_GALLERY_BACKFILL_MS: "0" });
});
after(async () => { await bridge?.stop(); await core?.close(); });

describe("确认卡", () => {
  test("落成那条会话里的一条消息，带着 relation；不推通知", async () => {
    const item = { id: "r1", kind: "avoid", text: "她不喜欢我老问她吃没吃饭", quote: "别老问我吃没吃饭", status: "pending" };
    const r = await api(bridge.base, "/api/relation/card", { method: "POST", body: { session_id: "test-rel-1", item } });
    assert.equal(r.status, 200);
    const rows = (await api(bridge.base, "/api/messages?sessionId=test-rel-1")).data;
    assert.equal(rows.length, 1);
    const meta = JSON.parse(rows[0].metadata);
    assert.deepEqual(meta.relation, { id: "r1", kind: "avoid", text: item.text, quote: item.quote });
    assert.equal(meta.proactive, undefined, "不是他主动找她说话");
  });

  test("缺东西就 400，不落一张空卡", async () => {
    const r = await api(bridge.base, "/api/relation/card", { method: "POST", body: { session_id: "test-rel-2", item: { id: "x" } } });
    assert.equal(r.status, 400);
  });
});

describe("列表代理", () => {
  test("列表 / 现查 / Keep 原样转；409 原样回", async () => {
    assert.equal((await api(bridge.base, "/api/nox/relation")).data.items[0].id, "r1");
    await api(bridge.base, "/api/nox/relation/r1");
    assert.equal((await api(bridge.base, "/api/nox/relation/r1/confirm", { method: "POST" })).status, 200);
    assert.equal((await api(bridge.base, "/api/nox/relation/r1/reject", { method: "POST" })).status, 409);
    assert.equal((await api(bridge.base, "/api/nox/relation/r1/fly", { method: "POST" })).status, 400);
    assert.deepEqual(seen, [
      "GET /api/nox/relation", "GET /api/nox/relation/r1",
      "POST /api/nox/relation/r1/confirm", "POST /api/nox/relation/r1/reject",
    ], "fly 不该转出去");
  });
});
