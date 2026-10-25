/**
 * Skills P3：他提议装做法的卡（2026-10-25）—— bridge 这一段。
 *   1. Core 的 skill 附件 → 聊天流里一帧 {type:"skill", proposalId, card}，并落进这条消息的 metadata（翻历史还在）
 *   2. 代理：待确认列表 / 查一张 / 装上 / 不要；错误原样带回
 *      （「proposals 别被当成做法名」要紧的是 Core 那边的路由顺序，test_skills.py 守着）
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, describe, test } from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

const seen = [];
let core;
let bridge;
const CARD = { action: "install", name: "movie-after-talk", description: "看完电影聊什么", reason: "她说想要", source: "" };

before(async () => {
  const srv = http.createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => { raw += c; });
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url });
      const json = (code, o) => { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(o)); };
      if (req.url.startsWith("/chat/stream")) {
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        const f = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
        f({ type: "text", text: "我想学个做法" });
        f({ type: "attachment", kind: "skill", proposal_id: "s1", card: CARD });
        f({ type: "done", ok: true, session_id: "sk-s1", outcome: "answered", tools_used: ["skill_propose"] });
        return res.end();
      }
      if (req.url === "/api/nox/skills/proposals" && req.method === "GET") return json(200, { ok: true, items: [{ id: "s1" }] });
      if (req.url === "/api/nox/skills/proposals/s1" && req.method === "GET") return json(200, { ok: true, id: "s1", status: "proposed" });
      if (req.url === "/api/nox/skills/proposals/s1/confirm" && req.method === "POST") return json(200, { ok: true, id: "s1", status: "applied" });
      if (req.url === "/api/nox/skills/proposals/s1/reject" && req.method === "POST") return json(409, { detail: "这张卡已经处理过了（applied）" });
      json(404, { detail: "nope" });
    });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  core = { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
  bridge = await startBridge({ NOX_CORE_URL: core.url });
});

after(async () => { await bridge?.stop(); await core?.close(); });

describe("装做法的提议卡：bridge", () => {
  test("🔴 聊天流里有 skill 帧，翻历史 metadata 里也有", async () => {
    const r = await fetch(`${bridge.base}/api/chat`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Nox-Token": TOKEN },
      body: JSON.stringify({ message: "以后看完电影就这么聊", sessionId: "sk-s1" }),
    });
    const frames = (await r.text()).split("\n\n").filter((l) => l.startsWith("data: ")).map((l) => JSON.parse(l.slice(6)));
    assert.deepEqual(frames.find((f) => f.type === "skill"), { type: "skill", proposalId: "s1", card: CARD });
    const msgs = (await api(bridge.base, "/api/messages?sessionId=sk-s1")).data.filter((m) => m.role === "assistant");
    const metas = msgs.map((m) => (typeof m.metadata === "string" ? JSON.parse(m.metadata || "{}") : m.metadata || {}));
    assert.ok(metas.some((x) => x.skill && x.skill.proposalId === "s1"), `落库的 metadata 里没有 skill：${JSON.stringify(metas)}`);
  });

  test("列表 / 查 / 装上 / 不要 都通；错误码原样带回；别的动作不放行", async () => {
    assert.deepEqual((await api(bridge.base, "/api/nox/skills/proposals")).data.items, [{ id: "s1" }]);
    assert.equal((await api(bridge.base, "/api/nox/skills/proposals/s1")).data.status, "proposed");
    assert.equal((await api(bridge.base, "/api/nox/skills/proposals/s1/confirm", { method: "POST", body: {} })).data.status, "applied");
    const no = await api(bridge.base, "/api/nox/skills/proposals/s1/reject", { method: "POST", body: {} });
    assert.equal(no.status, 409);
    assert.match(JSON.stringify(no.data), /处理过了/);
    const bad = await api(bridge.base, "/api/nox/skills/proposals/s1/install", { method: "POST", body: {} });
    assert.notEqual(bad.status, 200, "只有 confirm / reject 两种动作");
  });
});
