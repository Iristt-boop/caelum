/**
 * 语音条缓存 + 收藏（2026-09-29 她要的）。
 *
 * 她问「是不是每播一次都耗一次额度」—— 原来是：bridge 不缓存，重开 App 再点就重新合成，
 * 而且 v3 每次合成语气不一样。现在：
 *   · 语音条（cache:true）第一次合成的结果存下来，以后直接给文件
 *   · 收藏存的是**那一段音频**，缓存里有就拷，没有才合成一次
 *
 * 测试不碰 ElevenLabs（key 置空）：缓存命中的路径靠预先放一份文件造出来。
 */
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { after, before, describe, test } from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

let bridge;
const TTS = "[softly] Close your eyes, baby. The eggs can wait.";
const AUDIO = Buffer.from("ID3-fake-mp3-bytes-" + "x".repeat(200));

const key = (t) => crypto.createHash("sha256").update("phone|" + t).digest("hex").slice(0, 40);
const cacheFile = (t) => path.join(bridge.dir, "tts-cache", `${key(t)}.mp3`);

before(async () => { bridge = await startBridge({ ELEVENLABS_API_KEY: "", ELEVEN_KEY: "" }); });
after(async () => { await bridge?.stop(); });

async function raw(p, init = {}) {
  return fetch(`${bridge.base}${p}`, { ...init, headers: { "X-Nox-Token": TOKEN, "Content-Type": "application/json", ...(init.headers || {}) } });
}

describe("语音条缓存", () => {
  test("🔴 带 cache:true 且缓存里有：直接给那份文件，不再合成", async () => {
    fs.writeFileSync(cacheFile(TTS), AUDIO);
    const r = await raw("/api/tts", { method: "POST", body: JSON.stringify({ text: TTS, cache: true }) });
    assert.equal(r.status, 200);
    assert.equal(r.headers.get("x-tts-engine"), "cache");
    assert.deepEqual(Buffer.from(await r.arrayBuffer()), AUDIO);
  });

  test("不带 cache（通话那条路）不读缓存", async () => {
    fs.writeFileSync(cacheFile(TTS), AUDIO);
    const r = await raw("/api/tts", { method: "POST", body: JSON.stringify({ text: TTS }) });
    assert.notEqual(r.headers.get("x-tts-engine"), "cache");
    await r.arrayBuffer().catch(() => {});
  });
});

describe("语音条收藏", () => {
  test("🔴 收藏存的是缓存里那一段（她听到的那一版），能列出来、能播、能取消", async () => {
    const t = "[whispers] Miss you, baby.";
    fs.writeFileSync(cacheFile(t), AUDIO);
    const r = await api(bridge.base, "/api/voice-favorites", { method: "POST", body: {
      tts: t, en: "Miss you, baby.", zh: "想你了宝贝", message_id: 7462, session_id: "s1" } });
    assert.equal(r.status, 200);
    assert.equal(r.data.from, "cache");
    const id = r.data.item.id;

    const list = (await api(bridge.base, "/api/voice-favorites")).data.items;
    assert.deepEqual(list.map((x) => [x.id, x.zh, x.message_id]), [[id, "想你了宝贝", "7462"]]);

    const audio = await raw(`/api/voice-favorites/${id}/audio`);
    assert.equal(audio.status, 200);
    assert.deepEqual(Buffer.from(await audio.arrayBuffer()), AUDIO, "放出来的不是收藏时那一段");

    //: 缓存被清掉了，收藏照样在 —— 两份是分开存的
    fs.unlinkSync(cacheFile(t));
    assert.equal((await raw(`/api/voice-favorites/${id}/audio`)).status, 200, "收藏跟着缓存一起没了");

    const again = await api(bridge.base, "/api/voice-favorites", { method: "POST", body: { tts: t } });
    assert.equal(again.data.item.id, id, "同一句收藏两次应该是同一条");
    assert.equal(again.data.existed, true);

    assert.equal((await api(bridge.base, `/api/voice-favorites/${id}`, { method: "DELETE" })).status, 200);
    assert.equal((await raw(`/api/voice-favorites/${id}/audio`)).status, 404);
    assert.deepEqual((await api(bridge.base, "/api/voice-favorites")).data.items, []);
  });

  test("合成不了就不落库 —— 不留一条点了没声音的收藏", async () => {
    const r = await api(bridge.base, "/api/voice-favorites", { method: "POST", body: { tts: "never cached" } });
    assert.equal(r.status, 502);
    assert.deepEqual((await api(bridge.base, "/api/voice-favorites")).data.items, []);
  });

  test("收藏要鉴权", async () => {
    const r = await fetch(`${bridge.base}/api/voice-favorites`);
    assert.equal(r.status, 403);
  });
});

// ---------------------------------------------------------------- 写缓存（假的 ElevenLabs 上游）

import http from "node:http";

describe("第一次合成就存下来", () => {
  let up, b2, calls = 0;
  const SPOKEN = Buffer.from("fake-eleven-audio-" + "y".repeat(500));

  before(async () => {
    up = http.createServer((req, res) => {
      calls += 1;
      req.resume();
      res.writeHead(200, { "Content-Type": "audio/mpeg" });
      res.end(SPOKEN);
    });
    await new Promise((r) => up.listen(0, "127.0.0.1", r));
    b2 = await startBridge({
      ELEVENLABS_API_KEY: "test", ELEVENLABS_TTS_BASE: `http://127.0.0.1:${up.address().port}`,
    });
  });
  after(async () => { await b2?.stop(); up?.close(); });

  const tts = (text) => fetch(`${b2.base}/api/tts`, { method: "POST",
    headers: { "X-Nox-Token": TOKEN, "Content-Type": "application/json" },
    body: JSON.stringify({ text, cache: true }) });

  test("🔴 第二次播同一条不再打上游，给的是同一段", async () => {
    const t = "Good night, my love.";
    const r1 = await tts(t);
    const a1 = Buffer.from(await r1.arrayBuffer());
    //: 2026-10-07 链首换成 eleven-v4（她点名），mock 上游不分模型都会回 200
    assert.equal(r1.headers.get("x-tts-engine"), "eleven-v4");
    await new Promise((r) => setTimeout(r, 100));        // 写缓存在流结束之后
    const before = calls;
    const r2 = await tts(t);
    const a2 = Buffer.from(await r2.arrayBuffer());
    assert.equal(r2.headers.get("x-tts-engine"), "cache");
    assert.equal(calls, before, "第二次又去合成了 —— 还是每播一次花一次额度");
    assert.deepEqual(a2, a1);
  });

  test("收藏一条没播过的：合成一次，同时进缓存（聊天里再点就是同一个声音）", async () => {
    const t = "[laughing] You're spoiled.";
    const r = await fetch(`${b2.base}/api/voice-favorites`, { method: "POST",
      headers: { "X-Nox-Token": TOKEN, "Content-Type": "application/json" },
      body: JSON.stringify({ tts: t }) });
    const d = await r.json();
    assert.equal(d.from, "synth");
    const before = calls;
    const again = await tts(t);
    assert.equal(again.headers.get("x-tts-engine"), "cache");
    assert.deepEqual(Buffer.from(await again.arrayBuffer()), SPOKEN);
    assert.equal(calls, before);
  });
});
