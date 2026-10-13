/**
 * 配置层 P5：改配置的提议卡（2026-10-13）—— bridge 这一段。
 *   1. Core 的 config 附件 → 聊天流里一帧 {type:"config", proposalId, card}，并落进这条消息的 metadata（翻历史还在）
 *   2. 三条代理：查状态 / 同意（带 key）/ 不要；key 不进 bridge 日志
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, describe, test } from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

const KEY = "KEY-MARK-CARD-555";
const seen = [];
let core;
let bridge;

before(async () => {
  const srv = http.createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => { raw += c; });
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, body: raw ? JSON.parse(raw) : null });
      const json = (code, o) => { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(o)); };
      if (req.url.startsWith("/chat/stream")) {
        res.writeHead(200, { "Content-Type": "text/event-stream" });
        const f = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
        f({ type: "text", text: "我提了一张卡" });
        f({ type: "attachment", kind: "config", proposal_id: "p1", card: { kind: "slot", summary: "把杂活换成 DeepSeek", reason: "便宜" } });
        f({ type: "done", ok: true, session_id: "cfg-s1", outcome: "answered", tools_used: ["propose_config_change"] });
        return res.end();
      }
      if (req.url === "/api/nox/config/proposals/p1" && req.method === "GET") return json(200, { ok: true, id: "p1", status: "proposed" });
      if (req.url === "/api/nox/config/proposals/p1/confirm") return json(200, { ok: true, id: "p1", status: "applied" });
      if (req.url === "/api/nox/config/proposals/p1/reject") return json(409, { detail: "这条提议已经处理过了（applied）" });
      json(404, { detail: "nope" });
    });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  core = { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
  bridge = await startBridge({ NOX_CORE_URL: core.url });
});

after(async () => { await bridge?.stop(); await core?.close(); });

describe("改配置的提议卡：bridge", () => {
  test("🔴 聊天流里有 config 帧，翻历史 metadata 里也有", async () => {
    const r = await fetch(`${bridge.base}/api/chat`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Nox-Token": TOKEN },
      body: JSON.stringify({ message: "杂活换便宜点的吧", sessionId: "cfg-s1" }),
    });
    const frames = (await r.text()).split("\n\n").filter((l) => l.startsWith("data: ")).map((l) => JSON.parse(l.slice(6)));
    assert.deepEqual(frames.find((f) => f.type === "config"),
      { type: "config", proposalId: "p1", card: { kind: "slot", summary: "把杂活换成 DeepSeek", reason: "便宜" } });
    const msgs = (await api(bridge.base, "/api/messages?sessionId=cfg-s1")).data.filter((m) => m.role === "assistant");
    const metas = msgs.map((m) => (typeof m.metadata === "string" ? JSON.parse(m.metadata || "{}") : m.metadata || {}));
    assert.ok(metas.some((x) => x.config && x.config.proposalId === "p1"), `落库的 metadata 里没有 config：${JSON.stringify(metas)}`);
  });

  test("查 / 同意（带 key）/ 不要 都通；错误码原样带回；key 不进日志", async () => {
    assert.equal((await api(bridge.base, "/api/nox/config/proposals/p1")).data.status, "proposed");
    const ok = await api(bridge.base, "/api/nox/config/proposals/p1/confirm", { method: "POST", body: { key: KEY } });
    assert.equal(ok.data.status, "applied");
    assert.deepEqual(seen.find((x) => x.url.endsWith("/p1/confirm")).body, { key: KEY });
    const no = await api(bridge.base, "/api/nox/config/proposals/p1/reject", { method: "POST", body: {} });
    assert.equal(no.status, 409);
    assert.match(no.data.error, /处理过了/);
    assert.ok(!bridge.logs.join("").includes(KEY));
  });
});
