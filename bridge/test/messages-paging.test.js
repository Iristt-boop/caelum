/**
 * 聊天记录往前翻页（2026-10-04）。
 *
 * 她报的：「上下滑动是有上限的，到某条内容就上文就没有了」——
 * /api/messages 只回最后 500 行，手机再只留 200。现在 `before` + `limit` 能接着往前要。
 */
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";

import { api, startBridge } from "./helpers.js";

let bridge;
const SID = "3b09ea16d4f74dd4a46a291ce1e4a84c";

before(async () => {
  bridge = await startBridge();
  for (let i = 1; i <= 7; i++) {
    await api(bridge.base, "/api/push/send", { method: "POST", body: { body: `第${i}条`, session_id: SID } });
  }
});
after(async () => { await bridge?.stop(); });

const page = async (q) => (await api(bridge.base, `/api/messages?sessionId=${SID}${q}`)).data.map((m) => m.content);
const ids = async (q) => (await api(bridge.base, `/api/messages?sessionId=${SID}${q}`)).data.map((m) => m.id);

describe("翻页", () => {
  test("不带参数：还是全部（最多 500），正序", async () => {
    assert.deepEqual(await page(""), ["第1条", "第2条", "第3条", "第4条", "第5条", "第6条", "第7条"]);
  });

  test("🔴 limit 取最后几条，before 接着往前要，一条不漏一条不重", async () => {
    assert.deepEqual(await page("&limit=3"), ["第5条", "第6条", "第7条"]);
    const first = (await ids("&limit=3"))[0];
    assert.deepEqual(await page(`&limit=3&before=${first}`), ["第2条", "第3条", "第4条"]);
    const second = (await ids(`&limit=3&before=${first}`))[0];
    assert.deepEqual(await page(`&limit=3&before=${second}`), ["第1条"], "最后一页不足 limit —— 前端靠这个知道到头了");
  });

  test("limit 乱填不炸：上限 500", async () => {
    assert.equal((await page("&limit=abc")).length, 7);
    assert.equal((await page("&limit=99999")).length, 7);
  });
});
