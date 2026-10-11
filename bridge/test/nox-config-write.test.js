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
      if (req.url === "/api/nox/config/test") {
        // 真测慢：拖 21 秒再回，超过别的写接口的 20 秒 —— 这条路由必须用更长的超时
        return setTimeout(() => json(200, { ok: true, results: [{ kind: "chat", ok: true }] }), 21000);
      }
      if (req.url === "/api/nox/config/models") return json(200, { ok: true, models: [{ model_name: "glm-5.3-flash", status: "正常" }] });
      if (req.url === "/api/nox/config/providers" && req.method === "POST") {
        return json(400, { detail: { error: "真测没通过，没有保存 —— 正文是空的", results: [{ kind: "chat", ok: false }] } });
      }
      if (req.url === "/api/nox/config/providers/gemini" && req.method === "PATCH") return json(200, { ok: true, tested: false });
      if (req.url === "/api/nox/config/models/3" && req.method === "DELETE") return json(200, { ok: true, model_id: 3 });
      if (req.url === "/api/nox/config/providers/gemini/models" && req.method === "POST") return json(200, { ok: true, models: ["x"] });
      if (req.url === "/api/nox/config/fetch-models") return json(200, { ok: true, models: ["a", "b"] });
      if (req.url === "/api/nox/config/presets") return json(200, { ok: true, presets: [{ preset: "zhipu" }] });
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

test("真测：慢也等得到（超时比别的写接口长）；型号清单能读", { timeout: 60000 }, async () => {
  const r = await api(bridge.base, "/api/nox/config/test", { method: "POST", body: { provider_id: "zhipu" } });
  assert.equal(r.status, 200);
  assert.equal(r.data.results[0].ok, true);
  assert.deepEqual(seen.find((x) => x.url === "/api/nox/config/test").body, { provider_id: "zhipu" });
  const m = await api(bridge.base, "/api/nox/config/models");
  assert.equal(m.data.models[0].status, "正常");
});

test("P2d：真测没过的原因带回前端；改 / 删 / 加型号 / 拉型号 / 预设都通", async () => {
  const bad = await api(bridge.base, "/api/nox/config/providers", { method: "POST", body: { id: "gemini", key: KEY } });
  assert.equal(bad.status, 400);
  assert.equal(bad.data.ok, false);
  assert.match(bad.data.error, /没有保存/, "error 要是一句话，不是 [object Object]");
  assert.deepEqual(bad.data.results, [{ kind: "chat", ok: false }]);
  assert.equal((await api(bridge.base, "/api/nox/config/providers/gemini", { method: "PATCH", body: { label: "x" } })).data.tested, false);
  assert.equal((await api(bridge.base, "/api/nox/config/models/3", { method: "DELETE" })).data.model_id, 3);
  assert.deepEqual((await api(bridge.base, "/api/nox/config/providers/gemini/models", { method: "POST", body: { models: ["x"] } })).data.models, ["x"]);
  assert.deepEqual((await api(bridge.base, "/api/nox/config/fetch-models", { method: "POST", body: { provider_id: "zhipu" } })).data.models, ["a", "b"]);
  assert.equal((await api(bridge.base, "/api/nox/config/presets")).data.presets[0].preset, "zhipu");
  assert.ok(!bridge.logs.join("").includes(KEY));
});
