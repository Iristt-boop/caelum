/**
 * 🪪 工牌（Runtime Context，2026-10-11 设计稿 R1）：bridge 转给 Core 的那一轮带着「她从哪来」。
 *
 * 原来入口信息是拼进正文的（「（系统提示：这不是聊天窗口…」「【共影】…」），
 * 落进历史还要 `data/origin.py` 去认出「这不是她说的」。现在入口走结构字段 `runtime`。
 *
 * 守三条：
 *   1. 平常聊天 → surface=chat
 *   2. 共影现场还在（心跳够新）→ surface=watch，带片名 + 集数，**不带进度**
 *      （进度已经在【共影】那段消息里，写进工牌会每次暂停冷一次缓存）
 *   3. 电话 → 不填，交给 Core 按 voice 定成 call
 *
 * 从**使用方**断言：抓的是假 Core 收到的 body，不是 runtimeFor 的返回值。
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, describe, test } from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

const seen = [];
let core;
let bridge;

before(async () => {
  const srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      seen.push(JSON.parse(body || "{}"));
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.write(`data: ${JSON.stringify({ type: "text", text: "嗯" })}\n\n`);
      res.write(`data: ${JSON.stringify({ type: "done", ok: true, session_id: "s", outcome: "ok" })}\n\n`);
      res.end();
    });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  core = { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
  // 共影心跳窗口放宽到 60 秒：这里要验的是「在看」，不是「过期」
  bridge = await startBridge({ NOX_CORE_URL: core.url, WATCH_STALE_MS: "60000" });
});

after(async () => {
  await bridge?.stop();
  await core?.close();
});

async function chat(body) {
  const r = await fetch(`${bridge.base}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Nox-Token": TOKEN },
    body: JSON.stringify(body),
  });
  await r.text();
  return seen.at(-1);
}

//: 放在共影那组前面：共影现场会在窗口里一直算「在看」，后面的轮次都会变成 watch
describe("工牌：她从哪个端、在哪一页（Guide A4，10-18）", () => {
  test("OS 的 Chat 页：client=os + page + 刚从哪页过来", async () => {
    const sent = await chat({ message: "这个是什么", sessionId: "rt-os1", client: "os", page: "chat", from: "hisday" });
    assert.deepEqual(sent.runtime, { surface: "chat", client: "os", page: "chat", from: "hisday" });
  });

  test("语音浮层在别的页面呼出：电话的工牌也带页面", async () => {
    const sent = await chat({ message: "这个页面怎么用", sessionId: "rt-os2", voice: true, client: "os", page: "voices" });
    assert.deepEqual(sent.runtime, { surface: "call", caller: "her", client: "os", page: "voices" });
  });

  test("🔴 形状不对的一律不传（不认识的端、带空格 / 大写 / 太长 / 不是字符串的页面）", async () => {
    const sent = await chat({ message: "嗯", sessionId: "rt-os3", client: "web", page: "Voices 页", from: { x: 1 } });
    assert.deepEqual(sent.runtime, { surface: "chat" });
    const sent2 = await chat({ message: "嗯", sessionId: "rt-os4", client: "os", page: "a".repeat(25) });
    assert.deepEqual(sent2.runtime, { surface: "chat", client: "os" });
  });
});

describe("工牌：bridge 转给 Core 时带 runtime", () => {
  test("平常聊天：surface=chat", async () => {
    const sent = await chat({ message: "在吗", sessionId: "rt-chat" });
    assert.deepEqual(sent.runtime, { surface: "chat" });
  });

  test("她拨的电话：caller=her", async () => {
    const sent = await chat({ message: "hello", sessionId: "rt-call", voice: true });
    assert.deepEqual(sent.runtime, { surface: "call", caller: "her" });
  });

  test("🔴 他打来、她接了：caller=nox + 原因（A1 尺子 #2：她问「干嘛给我打电话」）", async () => {
    const inv = await api(bridge.base, "/api/call/invite", { method: "POST", body: { reason: "想你了，想听听你的声音", opener: "喂" } });
    await api(bridge.base, "/api/call/answer", { method: "POST", body: { id: inv.data.id } });
    const sent = await chat({ message: "干嘛给我打电话", sessionId: "rt-call2", voice: true });
    assert.deepEqual(sent.runtime, { surface: "call", caller: "nox", call_reason: "想你了，想听听你的声音" });
    // 挂了之后再说话（她回拨 / 开 Live）就不是他打的了
    await api(bridge.base, "/api/call/end", { method: "POST", body: { id: inv.data.id, duration: 30 } });
    const after = await chat({ message: "再说两句", sessionId: "rt-call2", voice: true });
    assert.equal(after.runtime.caller, "her");
  });

  test("日记批注（R1b）：surface=diary，正文不再写「这不是聊天窗口」", async () => {
    await api(bridge.base, "/api/diary", { method: "POST", body: { content: "test-今天有点累" } });
    let sent;
    for (let i = 0; i < 40 && !sent; i++) {
      await new Promise((r) => setTimeout(r, 50));
      sent = seen.find((b) => String(b.session_id || "").startsWith("diary-"));
    }
    assert.ok(sent, "日记批注没转给 Core");
    assert.deepEqual(sent.runtime, { surface: "diary" });
    assert.ok(!sent.text.includes("这不是聊天窗口"));
  });

  test("🔴 共影现场：surface=watch，带片名和集数，不带进度", async () => {
    await api(bridge.base, "/api/watch/state", {
      method: "POST",
      body: { session_id: "rt-w1", title: "摩登家庭", episode: "S03E07", mode: "stream",
              duration_s: 1300, position_ms: 315000 },
    });
    const sent = await chat({ message: "【共影】她暂停在 315.0 秒问你：能看到画面不",
                              display: "能看到画面不", sessionId: "rt-watch" });
    assert.deepEqual(sent.runtime, { surface: "watch", object: { title: "摩登家庭", episode: "S03E07" } });
    assert.ok(!JSON.stringify(sent.runtime).includes("315"), "进度不该进工牌");
  });
});

describe("工牌：来电过期（通话页崩了没回写 end）", () => {
  let b2;
  before(async () => { b2 = await startBridge({ NOX_CORE_URL: core.url, CALL_LIVE_MS: "1" }); });
  after(async () => { await b2?.stop(); });

  test("🔴 接了但早过了窗口 → 不再算他打的", async () => {
    const inv = await api(b2.base, "/api/call/invite", { method: "POST", body: { reason: "想你", opener: "喂" } });
    await api(b2.base, "/api/call/answer", { method: "POST", body: { id: inv.data.id } });
    await new Promise((r) => setTimeout(r, 20));
    const r = await fetch(`${b2.base}/api/chat`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Nox-Token": TOKEN },
      body: JSON.stringify({ message: "喂", sessionId: "rt-stale", voice: true }),
    });
    await r.text();
    assert.equal(seen.at(-1).runtime.caller, "her");
  });
});
