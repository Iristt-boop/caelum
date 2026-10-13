/**
 * 声音链（配置层 P4，2026-10-13）：Call / Live 两条，按她在 Models 页定的顺序合成。
 *
 * 从使用方断言：看 /api/tts 真正用的是哪一家（X-TTS-Engine），不看配置里写了什么。
 *   · 🔴 她改了 Call 链（v3 打头）→ 打电话那条（phone / desktop / 不传）都走 v3
 *   · 试听（only）只用那一家、不降级
 *   · Live 豆包流式失败后（after: doubao）从后面接着试，不再重试豆包
 *   · 🔴 Core 读不到 → 按默认顺序（永远不会因为配置读不到而没声音）
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, describe, test } from "node:test";

import { pickChain } from "../lib/tts-chain.js";
import { createVoiceChains } from "../lib/voice-chains.js";
import { startBridge, TOKEN } from "./helpers.js";

let eleven, core, bridge;
let chains = { call: ["eleven-v3", "eleven-v4", "eleven-turbo", "doubao", "edge"],
               live: ["doubao", "eleven-v4", "eleven-v3", "eleven-turbo", "edge"] };
let coreUp = true;
const models = [];

before(async () => {
  eleven = http.createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => { raw += c; });
    req.on("end", () => {
      try { models.push(JSON.parse(raw).model_id); } catch { /* 不是 JSON */ }
      res.writeHead(200, { "Content-Type": "audio/mpeg" });
      res.end(Buffer.from("fake-audio-" + "z".repeat(300)));
    });
  });
  await new Promise((r) => eleven.listen(0, "127.0.0.1", r));
  core = http.createServer((req, res) => {
    if (req.url === "/api/nox/config/voice" && coreUp) {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify({ ok: true, chains: { call: { engines: chains.call }, live: { engines: chains.live } } }));
    }
    res.writeHead(503).end();
  });
  await new Promise((r) => core.listen(0, "127.0.0.1", r));
  bridge = await startBridge({
    ELEVENLABS_API_KEY: "test", ELEVENLABS_TTS_BASE: `http://127.0.0.1:${eleven.address().port}`,
    NOX_CORE_URL: `http://127.0.0.1:${core.address().port}`,
  });
});
after(async () => { await bridge?.stop(); eleven?.close(); core?.close(); });

const tts = async (body) => {
  const r = await fetch(`${bridge.base}/api/tts`, { method: "POST",
    headers: { "X-Nox-Token": TOKEN, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  await r.arrayBuffer();
  return r.headers.get("x-tts-engine");
};

describe("声音链：bridge 按她定的走", () => {
  test("🔴 她把 Call 改成 v3 打头：打电话（phone / desktop / 不传）都走 v3", async () => {
    assert.equal(await tts({ text: "Hey baby.", profile: "phone" }), "eleven-v3");
    assert.equal(await tts({ text: "Hey baby.", profile: "desktop" }), "eleven-v3");
    assert.equal(await tts({ text: "Hey baby." }), "eleven-v3");
  });

  test("试听：只用那一家", async () => {
    assert.equal(await tts({ text: "嗯，我在呢。", only: "eleven-turbo" }), "eleven-turbo");
  });

  test("Live 豆包流式失败后：从它后面接着试（这里豆包没配，下一家是 v4）", async () => {
    assert.equal(await tts({ text: "好", profile: "live", after: "doubao" }), "eleven-v4");
  });

  test("/api/voice/chains 给 OS 实时语音看现在的链", async () => {
    const r = await (await fetch(`${bridge.base}/api/voice/chains`, { headers: { "X-Nox-Token": TOKEN } })).json();
    assert.deepEqual(r.chains.live, chains.live);
  });
});

describe("声音链：读 Core 的那一层", () => {
  test("🔴 Core 读不到 → 按默认顺序，只记一次日志", async () => {
    const warns = [];
    const vc = createVoiceChains({ coreUrl: "http://127.0.0.1:9", fetchImpl: async () => { throw new Error("down"); },
                                   log: { warn: (m) => warns.push(m), log() {} } });
    const c1 = await vc.get();
    await vc.get();
    assert.deepEqual(pickChain("phone", null, { chains: c1 })[0], "eleven-v4");
    assert.equal(warns.length, 1, "读不到每句都记一条会刷屏");
  });

  test("缓存 30 秒；invalidate 后下一句就读新的", async () => {
    let n = 0;
    let t = 0;
    const vc = createVoiceChains({ coreUrl: "x", now: () => t, fetchImpl: async () => {
      n += 1;
      return { json: async () => ({ chains: { call: { engines: ["edge"] }, live: { engines: ["doubao"] } } }) };
    } });
    await vc.get(); await vc.get();
    assert.equal(n, 1);
    t = 31_000; await vc.get();
    assert.equal(n, 2);
    vc.invalidate(); await vc.get();
    assert.equal(n, 3);
  });

  test("🔴 Core 重启那几秒读不到：接着用上一次读到的（她定的），不是掉回默认", async () => {
    let t = 0;
    let up = true;
    const vc = createVoiceChains({ coreUrl: "x", now: () => t, log: { warn() {}, log() {} }, fetchImpl: async () => {
      if (!up) throw new Error("restarting");
      return { json: async () => ({ chains: { call: { engines: ["eleven-v3", "edge"] }, live: { engines: ["doubao"] } } }) };
    } });
    await vc.get();
    up = false;
    t = 31_000;
    assert.deepEqual((await vc.get()).call, ["eleven-v3", "edge"], "Core 一重启她的声音就变回默认");
  });

  test("Core 回了认不得的引擎名：过滤掉；全认不得就用默认（不许空链）", () => {
    assert.deepEqual(pickChain("phone", null, { chains: { call: ["qwen", "edge"] } }), ["edge"]);
    assert.equal(pickChain("phone", null, { chains: { call: ["qwen"] } })[0], "eleven-v4");
  });
});
