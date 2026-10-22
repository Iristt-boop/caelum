/**
 * 「关于你」档案的代理（10-22）：原样转给 nox-core，409（她能看懂的原因）原样回，不压成 502。
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, test } from "node:test";

import { api, startBridge } from "./helpers.js";

let core; let bridge; const seen = [];

before(async () => {
  const srv = http.createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => { raw += c; });
    req.on("end", () => {
      seen.push({ m: req.method, u: req.url, body: raw ? JSON.parse(raw) : null });
      const dup = raw.includes("重复的");
      res.writeHead(dup ? 409 : 200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(dup ? { detail: "这一行已经在了" }
        : req.method === "GET" ? { ok: true, sections: [{ name: "喜欢", lines: ["晚霞"] }], history: [] }
          : { ok: true, op: "add", new: "拼豆" }));
    });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  core = { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
  bridge = await startBridge({ NOX_CORE_URL: core.url, NOX_GALLERY_BACKFILL_MS: "0" });
});
after(async () => { await bridge?.stop(); await core?.close(); });

test("读：原样转", async () => {
  const r = await api(bridge.base, "/api/nox/profile");
  assert.equal(r.status, 200);
  assert.deepEqual(r.data.sections, [{ name: "喜欢", lines: ["晚霞"] }]);
});

test("改：POST 到 /edit、带上她的那一行；409 原样回", async () => {
  const ok = await api(bridge.base, "/api/nox/profile/edit", { method: "POST", body: { op: "add", section: "喜欢", text: "拼豆" } });
  assert.equal(ok.status, 200);
  assert.deepEqual(seen.at(-1), { m: "POST", u: "/api/nox/profile/edit", body: { op: "add", section: "喜欢", text: "拼豆" } });
  const dup = await api(bridge.base, "/api/nox/profile/edit", { method: "POST", body: { op: "add", section: "喜欢", text: "重复的" } });
  assert.equal(dup.status, 409);
  assert.equal(dup.data.error, "这一行已经在了");
});

test("没 token 不给读", async () => {
  const r = await api(bridge.base, "/api/nox/profile", { token: "" });
  assert.notEqual(r.status, 200);
});
