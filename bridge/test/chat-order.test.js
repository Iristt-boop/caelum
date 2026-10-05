/**
 * 一轮里的文字和组件按发生顺序落库（2026-10-05 她报的）。
 *
 * 「说个早安，然后 send meme。这一轮对话结束后，meme 会自动跳到会话的第一行，
 *   语音也是这样……正确做法应该是说了什么，接着他用了什么 tool、发了什么东西，
 *   然后接着又发了文字，这样按着顺序。」
 *
 * 原因：组件一到就 saveMessage，文字等整轮说完才存 —— 按 rowid 排，组件全在文字前面。
 * 当场看对（SSE 按顺序到），一刷新就乱。验的是「从 /api/messages 读回来的顺序」。
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, describe, test } from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

async function fakeCore(script) {
  const srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      const frame = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
      script(JSON.parse(body || "{}"), frame, res);
    });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  return { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
}

let bridge;
let core;

before(async () => {
  core = await fakeCore((req, frame, res) => {
    const sid = req.session_id;
    if (String(req.text).includes("打断")) {
      // 发出一个表情之后就不吭声了 —— 等她那头断开
      frame({ type: "text", text: "等我一下" });
      frame({ type: "attachment", kind: "meme", tag: "早安" });
      return;
    }
    frame({ type: "text", text: "早安呀" });
    frame({ type: "tool_start", tool: "send_meme", args: { tag: "早安" } });
    frame({ type: "attachment", kind: "meme", tag: "早安" });
    frame({ type: "tool_end", tool: "send_meme", ok: true });
    frame({ type: "text", text: "今天也要好好吃饭" });
    frame({ type: "split" });
    frame({ type: "text", text: "我给你录了一句" });
    frame({ type: "attachment", kind: "voice", tts: "[softly] Morning, baby.", zh: "早安宝贝" });
    frame({ type: "attachment", kind: "music", song_id: 42, name: "晴天", artist: "周杰伦" });
    frame({ type: "text", text: "听完记得起床" });
    frame({ type: "done", session_id: sid, tools_used: ["send_meme"], ok: true });
    res.end();
  });
  bridge = await startBridge({ NOX_CORE_URL: core.url });
});
after(async () => { await bridge?.stop(); await core?.close(); });

async function rows(sid) {
  const r = await api(bridge.base, `/api/messages?sessionId=${sid}`);
  return r.data.filter((m) => m.role === "assistant").map((m) => {
    const meta = m.metadata ? JSON.parse(m.metadata) : {};
    if (meta.meme) return `表情:${meta.meme}`;
    if (meta.voice) return `语音:${meta.voice.zh}`;
    if (meta.music) return `歌:${meta.music.name}`;
    return `字:${(meta.segments || [m.content]).join("|")}`;
  });
}

async function chat(sid, message, opts = {}) {
  const r = await fetch(`${bridge.base}/api/chat`, {
    method: "POST", signal: opts.signal,
    headers: { "Content-Type": "application/json", "X-Nox-Token": TOKEN },
    body: JSON.stringify({ message, sessionId: sid }),
  });
  return r;
}

describe("一轮里的顺序", () => {
  test("🔴 说了什么 → 发了什么 → 又说了什么，刷新之后还是这个顺序", async () => {
    const sid = "5d09ea16d4f74dd4a46a291ce1e4a84c";
    await (await chat(sid, "早")).text();
    assert.deepEqual(await rows(sid), [
      "字:早安呀",
      "表情:早安",
      "字:今天也要好好吃饭|我给你录了一句",
      "语音:早安宝贝",
      "歌:晴天",
      "字:听完记得起床",
    ]);
  });

  test("同一轮的几条带同一个 turn（手机端据此归成一组，只挂一次时间戳）", async () => {
    const sid = "5d09ea16d4f74dd4a46a291ce1e4a84f";
    await (await chat(sid, "早")).text();
    const r = await api(bridge.base, `/api/messages?sessionId=${sid}`);
    const turns = r.data.filter((m) => m.role === "assistant").map((m) => JSON.parse(m.metadata || "{}").turn);
    assert.equal(turns.length, 6);
    assert.ok(turns[0], "没打 turn");
    assert.ok(turns.every((t) => t === turns[0]), `不是同一个 turn：${turns}`);
  });

  test("工具轨迹挂在这一轮第一条文字上，只挂一次", async () => {
    const sid = "5d09ea16d4f74dd4a46a291ce1e4a84d";
    await (await chat(sid, "早")).text();
    const r = await api(bridge.base, `/api/messages?sessionId=${sid}`);
    const withTrace = r.data.filter((m) => (m.metadata || "").includes("toolsTrace"));
    assert.equal(withTrace.length, 1);
    assert.equal(withTrace[0].content, "早安呀");
  });

  test("她中途打断：已经发出去的组件照旧留着（订单卡丢了她就没地方点确认）", async () => {
    const sid = "5d09ea16d4f74dd4a46a291ce1e4a84e";
    const ac = new AbortController();
    const r = await chat(sid, "打断", { signal: ac.signal });
    const reader = r.body.getReader();
    let seen = "";
    while (!seen.includes("早安")) seen += new TextDecoder().decode((await reader.read()).value);
    ac.abort();
    await new Promise((res) => setTimeout(res, 300));
    assert.deepEqual(await rows(sid), ["表情:早安"], "半句话不落库，组件要留");
  });
});
