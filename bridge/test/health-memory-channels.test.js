/**
 * OB 的外部通道（embedding / rerank）断了，/api/health 必须红（rerank 上线方案第 2 条）。
 *
 * ## 为什么要有这条
 *
 * 2026-09-22 百炼欠费断了三小时：OB 进程活着、/health 200，
 * 这里原来的 `ombre` 一项全绿 —— 他只是想不起来了，而没有任何人知道。
 * OB 现在自己记台账（`channel_health.py`），在 /health 里给 `channels_failing`；
 * bridge 只负责把结论端出来，caelum-watch 摘 `alert` 推她手机。
 *
 * 形状同 health-background.test.js：起一个只回 /health 的假 OB。
 */
import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { api, startBridge } from "./helpers.js";

async function fakeOmbre(healthBody) {
  const server = http.createServer((req, res) => {
    if (req.url === "/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(healthBody));
      return;
    }
    res.writeHead(404).end();
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const { port } = server.address();
  return {
    url: `http://127.0.0.1:${port}`,
    close: () => new Promise((r) => server.close(r)),
  };
}

const HEALTHY = {
  status: "ok",
  buckets: 263,
  decay_engine: "running",
  rerank_mode: "shadow",
  channels: {
    embedding: { calls_3h: 9, fails_3h: 0, failing: false },
    rerank: { calls_3h: 4, fails_3h: 0, failing: false },
  },
  channels_failing: [],
};

test("通道都好时 memory_channels 是绿的，并带上 rerank 模式", async () => {
  const ob = await fakeOmbre(HEALTHY);
  const bridge = await startBridge({ OMBRE_URL: ob.url });
  try {
    const { data } = await api(bridge.base, "/api/health");
    assert.equal(data.checks.ombre.ok, true, "假 OB 应该连得上");
    assert.equal(data.checks.memory_channels.ok, true);
    assert.equal(data.checks.memory_channels.rerank_mode, "shadow");
  } finally {
    await bridge.stop();
    await ob.close();
  }
});

test("🔴 OB 活着但百炼断了 —— 必须红，点名是哪条通道", async () => {
  const ob = await fakeOmbre({
    ...HEALTHY,
    channels: {
      embedding: { calls_3h: 5, fails_3h: 5, failing: true, last_error: "Arrearage" },
      rerank: { calls_3h: 4, fails_3h: 4, failing: true, last_error: "HTTP 400" },
    },
    channels_failing: ["embedding", "rerank"],
  });
  const bridge = await startBridge({ OMBRE_URL: ob.url });
  try {
    const { data } = await api(bridge.base, "/api/health");
    assert.equal(data.checks.ombre.ok, true, "连得上，所以老的 ombre 检查不会响 —— 这正是要补的洞");
    const mc = data.checks.memory_channels;
    assert.equal(mc.ok, false, "🔴 百炼断了却报绿 = 回到 09-22 那三小时");
    assert.deepEqual(mc.failing, ["embedding", "rerank"]);
    assert.match(mc.alert, /embedding/);
    assert.match(mc.alert, /rerank/);
    assert.equal(mc.channels.embedding.last_error, "Arrearage", "细节要透传，查的时候用得上");
  } finally {
    await bridge.stop();
    await ob.close();
  }
});

test("🔴 alert 文案不许带会变的数字（看门狗拿它算指纹做冷却）", async () => {
  const mk = (fails) => ({
    ...HEALTHY,
    channels: { ...HEALTHY.channels, rerank: { calls_3h: fails, fails_3h: fails, failing: true } },
    channels_failing: ["rerank"],
  });
  const alerts = [];
  for (const n of [3, 7]) {
    const ob = await fakeOmbre(mk(n));
    const bridge = await startBridge({ OMBRE_URL: ob.url });
    try {
      const { data } = await api(bridge.base, "/api/health");
      alerts.push(data.checks.memory_channels.alert);
    } finally {
      await bridge.stop();
      await ob.close();
    }
  }
  assert.equal(alerts[0], alerts[1], "失败次数变了 alert 就变 → 冷却被绕过、每 5 分钟吵她一次");
});

test("老版本 OB 没有这个字段时不许崩、不许误报", async () => {
  // bridge 可能先于 OB 发布
  const ob = await fakeOmbre({ status: "ok", buckets: 263, decay_engine: "running" });
  const bridge = await startBridge({ OMBRE_URL: ob.url });
  try {
    const { status, data } = await api(bridge.base, "/api/health");
    assert.equal(status, 200);
    assert.equal(data.checks.memory_channels.ok, true);
  } finally {
    await bridge.stop();
    await ob.close();
  }
});
