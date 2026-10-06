/**
 * 「思考」开关的转发（2026-10-06）。验三件事：拨得动、读得回、Core 存不上时不许翻成成功。
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, describe, test } from "node:test";

import { api, startBridge } from "./helpers.js";

let bridge;
let core;
let on = false;
let failNext = false;

before(async () => {
  core = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      if (!req.url.startsWith("/api/nox/thinking")) { res.statusCode = 404; return res.end(); }
      if (req.method === "POST") {
        if (failNext) {
          failNext = false;
          res.writeHead(503, { "Content-Type": "application/json" });
          return res.end(JSON.stringify({ detail: "思考开关没存上" }));
        }
        on = !!JSON.parse(body || "{}").on;
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true, on }));
    });
  });
  await new Promise((r) => core.listen(0, "127.0.0.1", r));
  bridge = await startBridge({ NOX_CORE_URL: `http://127.0.0.1:${core.address().port}` });
});
after(async () => { await bridge?.stop(); await new Promise((r) => core.close(r)); });

describe("思考开关", () => {
  test("拨开、读回来是开；拨关、读回来是关", async () => {
    assert.equal((await api(bridge.base, "/api/nox/thinking", { method: "POST", body: { on: true } })).data.on, true);
    assert.equal((await api(bridge.base, "/api/nox/thinking")).data.on, true);
    assert.equal((await api(bridge.base, "/api/nox/thinking", { method: "POST", body: { on: false } })).data.on, false);
    assert.equal((await api(bridge.base, "/api/nox/thinking")).data.on, false);
  });

  test("🔴 Core 存不上（503）原样告诉前端，不翻成 200", async () => {
    failNext = true;
    const r = await api(bridge.base, "/api/nox/thinking", { method: "POST", body: { on: true } });
    assert.equal(r.status, 503);
  });
});
