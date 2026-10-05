/**
 * 配置层 P1：bridge 逐路由代理 Core 的 `/api/nox/config/{providers,slots}`。
 *
 * 要钉住的不是「转发了」，是**「读不到」和「没有」不能被抹平**：
 *   · Core 的配置库没起来会回 503 {detail}。bridge 若把它当成普通 JSON 原样吐给前端
 *     （没有 ok:false），前端就会把「读不到」画成「还没有任何线路」——和 Home 旧版
 *     接口挂了拿假条目顶上是同一类错（DESIGN.md 五节那条硬规矩）。
 *   · Core 连不上同理。
 */
import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { api, startBridge } from "./helpers.js";

const PROVIDERS = {
  ok: true,
  providers: [{ id: "deepseek", label: "DeepSeek", protocol: "openai_compat",
                base_url: "https://api.deepseek.com/v1", cache_style: "auto_prefix",
                source: "seed", key_envs: ["DEEPSEEK_API_KEY"], key_configured: true, models: 3 }],
  shadow: { runs: 2, bad_runs: 0, diffs: 0 },
};
const SLOTS = { ok: true, slots: [{ slot: "chat.primary", ok: true,
                                    provider: { id: "deepseek", label: "DeepSeek" },
                                    model: { name: "deepseek-flash" }, key_configured: true }] };

/** 假 Core：按 routes 表回话；status 可控，用来造 503 */
async function fakeCore(routes) {
  const server = http.createServer((req, res) => {
    const hit = routes[req.url];
    if (!hit) { res.writeHead(404).end(); return; }
    res.writeHead(hit.status || 200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(hit.body));
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return { url: `http://127.0.0.1:${server.address().port}`,
           close: () => new Promise((r) => server.close(r)) };
}

test("两条路由把 Core 的视图原样带给前端", async () => {
  const core = await fakeCore({
    "/api/nox/config/providers": { body: PROVIDERS },
    "/api/nox/config/slots": { body: SLOTS },
  });
  const bridge = await startBridge({ NOX_CORE_URL: core.url });
  try {
    const p = await api(bridge.base, "/api/nox/config/providers");
    assert.equal(p.status, 200);
    assert.deepEqual(p.data, PROVIDERS);
    const s = await api(bridge.base, "/api/nox/config/slots");
    assert.deepEqual(s.data, SLOTS);
  } finally {
    await bridge.stop();
    await core.close();
  }
});

test("🔴 Core 的配置库没起来（503）—— 必须是 ok:false 带原因，不能是空列表", async () => {
  const detail = "配置库没起来 —— NOX_CONFIG_SHADOW 关着，或启动时失败了，看日志里的「配置影子」";
  const core = await fakeCore({
    "/api/nox/config/providers": { status: 503, body: { detail } },
    "/api/nox/config/slots": { status: 503, body: { detail } },
  });
  const bridge = await startBridge({ NOX_CORE_URL: core.url });
  try {
    for (const path of ["/api/nox/config/providers", "/api/nox/config/slots"]) {
      const { data } = await api(bridge.base, path);
      assert.equal(data.ok, false, `${path}：读不到却报 ok = 前端会把它画成「没有」`);
      assert.match(data.error, /配置库没起来/, "要把原因带给前端");
      assert.equal(data.providers, undefined);
      assert.equal(data.slots, undefined);
    }
  } finally {
    await bridge.stop();
    await core.close();
  }
});

test("Core 自己回 ok:false 也原样当成读不到", async () => {
  const core = await fakeCore({
    "/api/nox/config/slots": { body: { ok: false, error: "炸了" } },
  });
  const bridge = await startBridge({ NOX_CORE_URL: core.url });
  try {
    const { data } = await api(bridge.base, "/api/nox/config/slots");
    assert.deepEqual(data, { ok: false, error: "炸了" });
  } finally {
    await bridge.stop();
    await core.close();
  }
});

test("Core 连不上 —— ok:false", async () => {
  const bridge = await startBridge();           // 默认死端口
  try {
    for (const path of ["/api/nox/config/providers", "/api/nox/config/slots"]) {
      const { status, data } = await api(bridge.base, path);
      assert.equal(status, 200);
      assert.equal(data.ok, false);
      assert.ok(data.error);
    }
  } finally {
    await bridge.stop();
  }
});

test("Core 回了非 JSON（比如反代的错误页）也不崩", async () => {
  const server = http.createServer((req, res) => {
    res.writeHead(502, { "Content-Type": "text/html" });
    res.end("<html>bad gateway</html>");
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const bridge = await startBridge({ NOX_CORE_URL: `http://127.0.0.1:${server.address().port}` });
  try {
    const { data } = await api(bridge.base, "/api/nox/config/providers");
    assert.equal(data.ok, false);
    assert.match(data.error, /502/);
  } finally {
    await bridge.stop();
    await new Promise((r) => server.close(r));
  }
});

test("没带登录 token 不许读（配置信息不是公开的）", async () => {
  const core = await fakeCore({ "/api/nox/config/providers": { body: PROVIDERS } });
  const bridge = await startBridge({ NOX_CORE_URL: core.url });
  try {
    for (const path of ["/api/nox/config/providers", "/api/nox/config/slots"]) {
      const { status, data } = await api(bridge.base, path, { token: "" });
      assert.equal(status, 403, `${path}：无 token 必须被挡`);
      assert.notEqual(data?.ok, true);
    }
  } finally {
    await bridge.stop();
    await core.close();
  }
});
