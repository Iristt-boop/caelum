/**
 * edge-tts 兜底（2026-10-13）：微软把老版本拒了（403），它一个字节不吐 ——
 * 原来 bridge 先设好头再等数据，回的是 **200 + 空 body**：她那边是一段空白音频，不是报错。
 * 现在：一块音频都没有就 502 并说清楚；有音频照常 200。
 */
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { startBridge, TOKEN } from "./helpers.js";

const FAKE = join(dirname(fileURLToPath(import.meta.url)), "fixtures", "fake-edge-tts.mjs");
const CMD = JSON.stringify([process.execPath, FAKE]);
let broken, working;

before(async () => {
  broken = await startBridge({ EDGE_TTS_CMD: CMD, FAKE_EDGE: "fail", NOX_CORE_URL: "http://127.0.0.1:9" });
  working = await startBridge({ EDGE_TTS_CMD: CMD, FAKE_EDGE: "ok", NOX_CORE_URL: "http://127.0.0.1:9" });
});
after(async () => { await broken?.stop(); await working?.stop(); });

const tts = (b) => fetch(`${b.base}/api/tts`, { method: "POST",
  headers: { "X-Nox-Token": TOKEN, "Content-Type": "application/json" },
  body: JSON.stringify({ text: "嗯，我在呢。", only: "edge" }) });

describe("edge-tts 兜底", () => {
  test("🔴 一个字节都没合成出来：502 + 说清楚，不许 200 空音频", async () => {
    const r = await tts(broken);
    assert.equal(r.status, 502);
    const body = await r.json();
    assert.equal(body.error, "tts_all_failed");
    assert.match(body.detail, /403/);
    assert.ok(broken.logs.join("").includes("一个字节都没合成出来"));
  });

  test("合成出来了：200 + 音频 + 引擎头", async () => {
    const r = await tts(working);
    assert.equal(r.status, 200);
    assert.equal(r.headers.get("x-tts-engine"), "edge");
    assert.ok((await r.arrayBuffer()).byteLength > 100);
  });
});
