// 假 bridge。验聊天渲染用：流式那份和服务端读回来那份到底一不一样。
// ⚠️ 存在的理由：dev 的 /api 直连线上 bridge，不许拿糖糖的正式库做实验
// （never-test-in-tangtang-prod）。
//
// 照抄 bridge/server.js 的真实行为：
//   · 流式：按 split 事件把回复拆成几段，段与段之间的换行原样发出去
//   · 落库：fullReply 存全文，metadata.segments 存 trim() 过的每一段
// 前端重载时走 mapServerMsg（读 segments），和流式那条路是两份代码。
import http from "node:http";

const PORT = 3999;
const store = [];
const now = () => new Date().toISOString();
const seedSid = "seedsession0000000000000000000a";

// 一段回复，把几种最容易掉链子的语法全用上：
// 标题（Tailwind preflight 会把它压成正文）、列表、列表后接正文、
// 表格 + 删除线（要 remark-gfm，且 normalizeMarkdown 不能把表格拆开）、代码围栏。
const SEGMENTS = [
  [
    "## 说谎者悖论",
    "",
    "在的。**说谎者悖论**是这么回事：",
    "",
    "- 如果这句话是真的，那它就是假的",
    "- 如果它是假的，那它就是真的",
    "",
    "往哪个方向推都是死循环。",
    "",
  ].join("\n"),
  [
    "",
    "### 几种解法",
    "",
    "| 解法 | 谁提的 | 好不好使 |",
    "|---|---|---|",
    "| 类型论 | 罗素 | 能挡住，但代价大 |",
    "| 真值间隙 | 克里普克 | ~~完美~~ 也有漏洞 |",
    "",
    "想看代码的话：",
    "",
    "```python",
    "while True:",
    "    truth = not truth",
    "```",
    "",
    "就这么个意思。",
    "",
  ].join("\n"),
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
