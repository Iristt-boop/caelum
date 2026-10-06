/**
 * 他的思考给她看（2026-10-06）。
 *
 * 她：「thinking 模式做成可配置后，打开 thinking 在 chat 页也显示 thinking 的内容。」
 * Core 只在「思考」开着时发 thinking 帧。bridge 要做三件事：
 *   ① 转给前端（前端认的键是 content，同 text 帧）
 *   ② 落进这一轮**第一条**的 metadata.thinking（翻历史 / 刷新还在）
 *   ③ **不进正文** —— content 那列是搜索、Recents 预览读的，只放说出口的话
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
    const t = String(req.text);
    if (t.includes("先发表情")) {
      frame({ type: "thinking", text: "她刚起床，" });
      frame({ type: "attachment", kind: "meme", tag: "早安" });
      frame({ type: "thinking", text: "再说一句。" });
      frame({ type: "text", text: "早安呀" });
    } else if (t.includes("不想")) {
      frame({ type: "text", text: "在呢" });
    } else {
      frame({ type: "thinking", text: "她感冒了，" });
      frame({ type: "thinking", text: "先问嗓子。" });
      frame({ type: "text", text: "嗓子还疼吗" });
      frame({ type: "split" });
      frame({ type: "text", text: "多喝热水" });
    }
    frame({ type: "done", session_id: sid, ok: true });
    res.end();
  });
  bridge = await startBridge({ NOX_CORE_URL: core.url });
});
after(async () => { await bridge?.stop(); await core?.close(); });

async function chat(sid, message) {
  const r = await fetch(`${bridge.base}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Nox-Token": TOKEN },
    body: JSON.stringify({ message, sessionId: sid }),
  });
  const text = await r.text();
  return text.split("\n").filter((l) => l.startsWith("data: ")).map((l) => JSON.parse(l.slice(6)));
}

async function rows(sid) {
  const r = await api(bridge.base, `/api/messages?sessionId=${sid}`);
  return r.data.filter((m) => m.role === "assistant")
    .map((m) => ({ content: m.content, meta: m.metadata ? JSON.parse(m.metadata) : {} }));
}

describe("思考", () => {
  test("转给前端，键是 content", async () => {
    const frames = await chat("test-think-1", "在吗");
    const th = frames.filter((f) => f.type === "thinking").map((f) => f.content);
    assert.deepEqual(th, ["她感冒了，", "先问嗓子。"]);
  });

  test("落进第一条的 metadata.thinking，不进正文", async () => {
    await chat("test-think-2", "在吗");
    const r = await rows("test-think-2");
    assert.equal(r.length, 1);
    assert.equal(r[0].meta.thinking, "她感冒了，先问嗓子。");
    assert.ok(!r[0].content.includes("感冒"), "思考进了 content，搜索和预览都会读到草稿纸");
    assert.deepEqual(r[0].meta.segments, ["嗓子还疼吗", "多喝热水"]);
  });

  test("这一轮先发的是表情：思考挂在表情那条上（这一轮最前面），后面的字不再挂", async () => {
    await chat("test-think-3", "先发表情");
    const r = await rows("test-think-3");
    assert.equal(r.length, 2);
    assert.equal(r[0].meta.meme, "早安");
    assert.equal(r[0].meta.thinking, "她刚起床，再说一句。");
    assert.equal(r[1].content, "早安呀");
    assert.equal(r[1].meta.thinking, undefined);
    assert.equal(r[0].meta.turn, r[1].meta.turn, "同一轮");
  });

  test("没思考（开关关着）：metadata 里没有这个键", async () => {
    await chat("test-think-4", "不想");
    const r = await rows("test-think-4");
    assert.equal(r.length, 1);
    assert.equal(r[0].meta.thinking, undefined);
  });
});
