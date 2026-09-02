// 假 bridge。原本为复现「发出去的字会消失」（已修），现在改成量
// 「流式渲染出来的那一份」和「服务端存的那一份」到底差在哪。
// ⚠️ 存在的理由不变：dev 的 /api 直连线上 bridge，不许拿糖糖的正式库做实验
// （never-test-in-tangtang-prod）。
//
// 这里照抄 bridge/server.js 的真实行为：
//   · 流式：按 split 事件把回复拆成几段，段与段之间的换行**原样**发出去
//   · 落库：fullReply 存全文，metadata.segments 存**trim() 过**的每一段
// 前端重载时走 mapServerMsg（读 segments），和流式那条路是两份代码。
import http from "node:http";

const PORT = 3999;
const store = [];
const now = () => new Date().toISOString();
const seedSid = "seedsession0000000000000000000a";

// 一段带 markdown 的回复，故意包含 §30.9 记过的几种形状：
// 列表、列表后接正文、加粗、代码围栏。段前后都留了换行。
const SEGMENTS = [
  "在的。**说谎者悖论**是这么回事：\n\n- 如果这句话是真的，那它就是假的\n- 如果它是假的，那它就是真的\n\n往哪个方向推都是死循环。\n",
  "\n想看代码的话：\n\n```python\nwhile True:\n    truth = not truth\n```\n\n就这么个意思。\n",
];
const FULL = SEGMENTS.join("");

store.push(
  { id: seedSid, role: "user", content: "在吗", timestamp: now(), metadata: "" },
  { id: seedSid, role: "assistant", content: "在的，一直在。", timestamp: now(), metadata: "" },
);

const cors = (res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
};

http.createServer(async (req, res) => {
  cors(res);
  const u = new URL(req.url, "http://x");
  if (req.method === "OPTIONS") return res.writeHead(204).end();

  if (u.pathname === "/api/messages") {
    const sid = u.searchParams.get("sessionId");
    const out = sid ? store.filter((m) => m.id === sid) : store.slice(-200);
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify(out));
  }

  if (u.pathname === "/api/chat" && req.method === "POST") {
    let body = "";
    for await (const c of req) body += c;
    const { message = "", sessionId = "" } = JSON.parse(body || "{}");
    store.push({ id: sessionId, role: "user", content: message, timestamp: now(), metadata: "" });

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    const send = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

    for (let i = 0; i < SEGMENTS.length; i++) {
      if (i > 0) send({ type: "split" });
      // 按 12 字一块发，模拟真实的 token 流
      for (let p = 0; p < SEGMENTS[i].length; p += 12) {
        send({ type: "text", content: SEGMENTS[i].slice(p, p + 12) });
        await sleep(40);
      }
    }
    // 照 bridge 的样子落库：全文 + trim 过的 segments
    store.push({
      id: sessionId, role: "assistant", content: FULL, timestamp: now(),
      metadata: JSON.stringify({ segments: SEGMENTS.map((s) => s.trim()).filter(Boolean) }),
    });
    send({ type: "done", sessionId });
    return res.end();
  }

  res.writeHead(200, { "Content-Type": "application/json" });
  res.end("{}");
}).listen(PORT, () => console.log(`[mock] 假 bridge 起在 http://localhost:${PORT}`));
