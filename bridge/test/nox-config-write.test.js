/**
 * 配置层 P2b：bridge 逐路由代理 Core 的写接口（填 key / 清 key / 迁移包导出、预览、导入）。
 *
 * 要钉住的：
 *   · 五条路由都在（漏一条前端拿到的是 bridge 的 404，看起来像 Core 挂了）
 *   · 方法和请求体原样到 Core
 *   · 🔴 Core 的错误状态码原样带回（400 口令不对 / 404 / 429），不能翻成 200
 *   · 🔴 请求体里是 key 和口令：**bridge 日志里一个字节都不能有**，错误路径也一样
 *   · 不带 token 拿不到
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, test } from "node:test";

import { api, startBridge } from "./helpers.js";

const KEY = "KEY-MARK-BRIDGE-777";
const PASS = "PASS-MARK-BRIDGE-888";
const seen = [];
let core;
let bridge;

before(async () => {
  const srv = http.createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => { raw += c; });
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, body: raw ? JSON.parse(raw) : null });
      const json = (code, o) => { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(o)); };
      if (req.url === "/api/nox/config/providers/nope/key") return json(404, { detail: "没有 nope 这一家" });
      if (req.url.endsWith("/key")) return json(200, { ok: true, provider: "deepseek", key_configured: true, key_source: "front" });
      if (req.url === "/api/nox/config/export") return json(200, { ok: true, filename: "x.caelumcfg", file_b64: "QUJD" });
      if (req.url === "/api/nox/config/import/preview") return json(400, { detail: "口令不对，或者文件被改过" });
      if (req.url === "/api/nox/config/import") return json(429, { detail: "点得太快了，过几分钟再试" });
      json(404, { detail: "nope" });
    });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  core = { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
  bridge = await startBridge({ NOX_CORE_URL: core.url });
});

after(async () => { await bridge?.stop(); await core?.close(); });

test("填 key：PUT 和请求体原样到 Core，响应里没有 key", async () => {
  const r = await api(bridge.base, "/api/nox/config/providers/deepseek/key", { method: "PUT", body: { key: KEY } });
  assert.equal(r.status, 200);
  assert.equal(r.data.key_configured, true);
  assert.ok(!JSON.stringify(r.data).includes(KEY));
  const hit = seen.find((x) => x.url === "/api/nox/config/providers/deepseek/key" && x.method === "PUT");
  assert.deepEqual(hit.body, { key: KEY });
});

test("清 key：DELETE 到 Core", async () => {
  const r = await api(bridge.base, "/api/nox/config/providers/deepseek/key", { method: "DELETE" });
  assert.equal(r.status, 200);
  assert.ok(seen.some((x) => x.method === "DELETE" && x.url.endsWith("/deepseek/key")));
});

test("🔴 Core 的错误码原样带回，ok:false 带原因", async () => {
  const nf = await api(bridge.base, "/api/nox/config/providers/nope/key", { method: "PUT", body: { key: KEY } });
  assert.equal(nf.status, 404);
  assert.equal(nf.data.ok, false);
  const bad = await api(bridge.base, "/api/nox/config/import/preview", { method: "POST", body: { file_b64: "QUJD", passphrase: PASS } });
  assert.equal(bad.status, 400);
  assert.match(bad.data.error, /口令不对/);
  const fast = await api(bridge.base, "/api/nox/config/import", { method: "POST", body: { file_b64: "QUJD", passphrase: PASS } });
  assert.equal(fast.status, 429);
});

test("导出：把文件内容带回前端", async () => {
  const r = await api(bridge.base, "/api/nox/config/export", { method: "POST", body: { passphrase: PASS, passphrase2: PASS } });
  assert.equal(r.data.file_b64, "QUJD");
});

test("🔴 key 和口令不进 bridge 日志（含出错的路径）", async () => {
  const text = bridge.logs.join("");
  assert.ok(seen.length >= 6, "前面几条没打到 Core，这条扫描就没意义");
  assert.ok(!text.includes(KEY), "bridge 日志里出现了 key");
  assert.ok(!text.includes(PASS), "bridge 日志里出现了口令");
});

test("不带 token 写不了", async () => {
  const r = await fetch(`${bridge.base}/api/nox/config/providers/deepseek/key`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: KEY }),
  });
  assert.equal(r.status, 403);
});
