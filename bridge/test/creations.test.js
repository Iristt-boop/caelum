/**
 * Creations（2026-10-11）：bridge 这一段。
 *   1. Core 的 creation 附件 → 聊天流里一帧 {type:"creation", id, title}，并落进这条消息的 metadata（翻历史还在）
 *   2. 三条代理：Studio 列表 / 打开取 HTML / 删除；删了的原样 404（前端显示「已删」）
 * 从使用方断言：读的是她前端收到的 SSE 和 /api/messages，不是函数返回值。
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, describe, test } from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

const store = { abc: { id: "abc", title: "五子棋", html: "<html><body>棋</body></html>" } };
let core;
let bridge;

before(async () => {
  const srv = http.createServer((req, res) => {
    const json = (code, o) => { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(o)); };
    if (req.url.startsWith("/chat/stream")) {
      req.resume();
      req.on("end", () => {
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        const f = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
        f({ type: "text", text: "做好了" });
        f({ type: "attachment", kind: "creation", creation_id: "abc", title: "五子棋" });
        f({ type: "done", ok: true, session_id: "cr-s1", outcome: "answered", tools_used: ["make_creation"] });
        res.end();
      });
      return;
    }
    const m = req.url.match(/^\/api\/nox\/creations(?:\/([^/?]+))?/);
    if (m) {
      if (!m[1]) return json(200, { ok: true, items: Object.values(store).map(({ html, ...x }) => x) });
      const c = store[m[1]];
      if (!c) return json(404, { detail: "没有这个 Creation（可能删了）" });
      if (req.method === "DELETE") { delete store[m[1]]; return json(200, { ok: true }); }
      return json(200, { ok: true, ...c });
    }
    json(404, { detail: "nope" });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  core = { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
  bridge = await startBridge({ NOX_CORE_URL: core.url });
});

after(async () => { await bridge?.stop(); await core?.close(); });

describe("Creations：bridge", () => {
  test("🔴 聊天流里有 creation 帧，翻历史 metadata 里也有", async () => {
    const r = await fetch(`${bridge.base}/api/chat`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Nox-Token": TOKEN },
      body: JSON.stringify({ message: "帮我做个五子棋", sessionId: "cr-s1" }),
    });
    const sse = await r.text();
    const frames = sse.split("\n\n").filter((l) => l.startsWith("data: ")).map((l) => JSON.parse(l.slice(6)));
    assert.deepEqual(frames.find((f) => f.type === "creation"), { type: "creation", id: "abc", title: "五子棋" });
    const msgs = (await api(bridge.base, "/api/messages?sessionId=cr-s1")).data;
    const mine = msgs.filter((m) => m.role === "assistant");
    const meta = mine.map((m) => (typeof m.metadata === "string" ? JSON.parse(m.metadata || "{}") : m.metadata || {}));
    assert.ok(meta.some((x) => JSON.stringify(x).includes('"creation"') && JSON.stringify(x).includes("abc")),
      `落库的 metadata 里没有 creation：${JSON.stringify(meta)}`);
  });

  test("Studio 列表 / 打开 / 删除，删了 404", async () => {
    const list = await api(bridge.base, "/api/nox/creations");
    assert.deepEqual(list.data.items.map((i) => i.id), ["abc"]);
    assert.ok(!("html" in list.data.items[0]), "列表不带 HTML");
    const one = await api(bridge.base, "/api/nox/creations/abc");
    assert.equal(one.data.html, "<html><body>棋</body></html>");
    const del = await api(bridge.base, "/api/nox/creations/abc", { method: "DELETE" });
    assert.equal(del.data.ok, true);
    const gone = await api(bridge.base, "/api/nox/creations/abc");
    assert.equal(gone.status, 404);
    assert.equal(gone.data.ok, false);
  });

  test("没带 token 的拿不到", async () => {
    const r = await fetch(`${bridge.base}/api/nox/creations`);
    assert.equal(r.status, 403);
  });
});
