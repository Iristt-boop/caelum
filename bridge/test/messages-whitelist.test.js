/**
 * `/api/messages`（不带 sessionId 的那条）只许回**真会话**（2026-09-19）。
 *
 * 为什么这条必须守：
 *
 * 这条列表是 PWA「恢复会话」的兜底 —— 本地没存会话 id 时，它拿**最后一条**
 * 的 sessionId 当自己的会话。原来这里只排除 `test-` 前缀的 id，于是上一晚
 * 端到端验证留下的 `sse-verify-final` 被手机捡走。捡走之后：
 *
 *   · 它的内容不在 Recents 里（`/api/conv-sessions` 有 32 位 hex 白名单）
 *   · 界面上删不掉（她原话「他妈的我也删不了」）
 *   · OS 走白名单，永远追不上它
 *
 * 于是表现成「OS 的会话和 pwa 不同步」，她 20:32–20:46 说的话全落在那个
 * 影子会话里。所以这条列表必须和 /api/conv-sessions 用**同一把尺子**。
 */
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

let bridge;
const JUNK = "sse-verify-final";                        // 那晚的影子会话
const REAL = "1809ea16d4f74dd4a46a291ce1e4a84b";        // 真会话（32 位 hex）

before(async () => {
  //: 上游指向死端口：/api/chat 的落库发生在转发**之前**，所以照样造得出库里的行
  bridge = await startBridge();
});
after(async () => {
  await bridge?.stop();
});

/** 打一发 /api/chat 把一行落进指定会话。上游连不上不影响落库。 */
async function seed(sessionId, text) {
  const r = await fetch(`${bridge.base}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Nox-Token": TOKEN },
    body: JSON.stringify({ message: text, sessionId }),
  });
  await r.text().catch(() => "");
}

describe("/api/messages 的「最近 200 条」", () => {
  test("影子会话不许出现，真会话照常出现", async () => {
    await seed(REAL, "真会话里的一句");
    await seed(JUNK, "影子会话里的一句");

    const rows = await api(bridge.base, "/api/messages");
    const sids = (Array.isArray(rows.data) ? rows.data : []).map((r) => r.sessionId);

    assert.ok(sids.includes(REAL), "真会话被挡掉了 —— 恢复会捡不到东西");
    assert.ok(
      !sids.includes(JUNK),
      "影子会话漏进来了：PWA 恢复时会再拿它当自己的会话，就是那晚不同步的起点"
    );
  });

  test("带 sessionId 查那条影子会话仍然读得到（白名单只管「最近 200 条」）", async () => {
    //: 她真点开一条会话时不该被拦 —— 那条路是明确的 id，不是「捡」
    const rows = await api(bridge.base, `/api/messages?sessionId=${JUNK}`);
    const list = Array.isArray(rows.data) ? rows.data : [];
    //: 死上游会让 bridge 多落一条「连不上脑子」的兜底回复，所以不是恰好 1 条
    assert.ok(list.length >= 1, "明确的 id 反而读不到 —— 白名单管过头了");
    assert.ok(list.some((r) => r.role === "user" && r.content === "影子会话里的一句"));
  });
});
