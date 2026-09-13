/**
 * 「连得上」和「还在干活」是两回事（审计 1.4）。
 *
 * ## 为什么要有这条
 *
 * `/api/health` 原来只 probe 各服务的端口。而 nox-core 有三条后台循环
 * （attention 心跳 / Care 快循环 / 话题池），每一条死掉的症状都**不是报错**：
 *
 *     attention 心跳  死了 → 他从此不再主动找她
 *     Care 快循环     死了 → 位置跃迁、随机惦记全没了
 *     话题池 Scout    死了 → 他再也不带新东西来聊
 *
 * 三条都用 `except Exception: 下一轮继续` 兜着 —— 兜是对的，但那也意味着
 * 「连着失败一千次」和「一切正常」在 `/api/health` 里长得一模一样。
 *
 * nox-core 自己记了台账（`obs/heartbeat.py`），bridge 只负责把结论端出来。
 * 这条测试钉的就是"端出来了"。
 *
 * ## 为什么要起一个假的 nox-core
 *
 * helpers 默认把 `NOX_CORE_URL` 指向死端口 —— 那只能验"连不上"。
 * 「连上了，但它说自己的后台停了」是完全不同的一条分支，
 * 不造个假上游根本走不到。
 */
import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { api, startBridge } from "./helpers.js";

/** 起一个只回 /health 的假 nox-core，返回 {url, close} */
async function fakeCore(healthBody) {
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
  ok: true,
  model: "glm-5.3",
  background: {
    attention_tick: { stale: false, count: 12 },
    care_tick: { stale: false, count: 300 },
    topic_scout: { stale: false, count: 2 },
  },
  background_stale: [],
};

test("后台都在跑时，nox_background 是绿的", async () => {
  const core = await fakeCore(HEALTHY);
  const bridge = await startBridge({ NOX_CORE_URL: core.url });
  try {
    const { data } = await api(bridge.base, "/api/health");
    assert.equal(data.checks.nox_core.ok, true, "假上游应该连得上");
    assert.equal(data.checks.nox_background.ok, true);
    assert.equal(data.checks.nox_background.jobs, 3, "三条循环都该被数到");
    // ⚠️ **这里不断言 `data.status`。** 沙箱里 ombre / eryu / 共读 / 共听
    //    全都连不上，总体状态怎么都是 degraded —— 断言它等于 "ok" 会永远失败，
    //    断言它等于 "degraded" 又永远成立，**两种写法都不测任何东西**。
    //    真正的判据是上面那一项，它只受这次改动影响。
  } finally {
    await bridge.stop();
    await core.close();
  }
});

test("🔴 端口通着但后台停了 —— 必须红，而且要点名", async () => {
  const core = await fakeCore({
    ...HEALTHY,
    background: {
      ...HEALTHY.background,
      attention_tick: { stale: true, count: 0 },
    },
    background_stale: ["attention_tick"],
  });
  const bridge = await startBridge({ NOX_CORE_URL: core.url });
  try {
    const { data } = await api(bridge.base, "/api/health");
    // 连通性是好的 —— 这正是这条测试的全部意义
    assert.equal(data.checks.nox_core.ok, true, "连得上，所以老的检查不会响");
    assert.equal(data.checks.nox_background.ok, false, "🔴 后台停了却报绿 = 这套东西白做");
    assert.deepEqual(data.checks.nox_background.stale, ["attention_tick"]);
    assert.match(data.checks.nox_background.error, /attention_tick/,
      "要点名是哪一条，不能只说「不健康」——看的人得知道下一步干嘛");
    // 同上：沙箱里总体状态恒为 degraded，断言它没有意义。
    // 看门狗摘的是 `checks` 里 ok=false 的键名，也就是上面这几条。
  } finally {
    await bridge.stop();
    await core.close();
  }
});

test("nox_background 和 nox_core 是两项，不许合并", async () => {
  // 「他挂了」和「他还在但不干活了」下一步要做的完全不同，
  // 合成一个红点就把这个区别丢了。
  const bridge = await startBridge();   // 默认死端口 = Core 连不上
  try {
    const { data } = await api(bridge.base, "/api/health");
    assert.equal(data.checks.nox_core.ok, false);
    assert.ok("nox_background" in data.checks, "Core 连不上时这一项也要在");
  } finally {
    await bridge.stop();
  }
});

test("上游没有这个字段时不许崩（老版本 Core / 部署顺序颠倒）", async () => {
  // bridge 可能先于 nox-core 发布。那时 Core 还没有 background_stale，
  // 这里不能因为读不到就 500 —— 探活自己挂掉是最坏的一种坏。
  const core = await fakeCore({ ok: true, model: "glm-5.3" });
  const bridge = await startBridge({ NOX_CORE_URL: core.url });
  try {
    const { status, data } = await api(bridge.base, "/api/health");
    assert.equal(status, 200);
    assert.equal(data.checks.nox_background.ok, true, "没有字段 = 没有已知的停摆，不该报红");
    assert.equal(data.checks.nox_background.jobs, 0);
  } finally {
    await bridge.stop();
    await core.close();
  }
});
