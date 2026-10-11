/**
 * 共读转给 Core 的那一轮带工牌字段（Runtime Context R1b，2026-10-11）。
 * 从使用方断言：抓假 Core 收到的 body。
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, test } from "node:test";

const seen = [];
let srv;
let replyToNote;

before(async () => {
  srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      seen.push(JSON.parse(body || "{}"));
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify({ text: "嗯，这句我也停了一下。", outcome: "ok", tools_used: [] }));
    });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  process.env.NOX_CORE_URL = `http://127.0.0.1:${srv.address().port}`;
  ({ replyToNote } = await import("../src/nox-client.js"));
});

after(() => new Promise((r) => srv.close(r)));

test("回批注：runtime=reading + 书名，正文不再写「这不是聊天窗口」", async () => {
  const note = { id: "n1", bookId: "b1", quote: "人是一根绳索", note: "这句好难懂" };
  await replyToNote({ note, chunkText: "", bookTitle: "查拉图斯特拉如是说", chunkTitle: "序" });
  const reply = seen.find((b) => b.session_id === "reading-n1");
  assert.deepEqual(reply.runtime, { surface: "reading", object: { title: "查拉图斯特拉如是说" } });
  assert.ok(!reply.text.includes("这不是聊天窗口"));
  assert.ok(!reply.text.includes("系统提示"));
  // 后台记忆那一趟也带
  await new Promise((r) => setTimeout(r, 100));
  const mem = seen.find((b) => b.session_id === "reading-n1-mem");
  assert.equal(mem?.runtime?.surface, "reading");
});
