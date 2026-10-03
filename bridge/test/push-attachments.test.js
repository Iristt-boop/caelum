/**
 * `/api/push/send` 收附件（2026-09-28）。
 *
 * 她报的：Nox 调工具发了语音，手机收不到。查出来是**主动开口这条路只收文字** ——
 * 09-27 00:31 他主动哄睡时发的语音条，Core 生成了，走到这个端点被丢掉。
 *
 * 落库的形状必须和 /chat/stream 那条一模一样（手机和 OS 翻历史认的就是那几种），
 * 所以这里验的是「从 /api/messages 读回来长什么样」，不是「端点回了 ok」。
 */
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";

import { api, startBridge } from "./helpers.js";

let bridge;
const SID = "1809ea16d4f74dd4a46a291ce1e4a84c";

before(async () => { bridge = await startBridge(); });
after(async () => { await bridge?.stop(); });

async function rows(sid) {
  const r = await api(bridge.base, `/api/messages?sessionId=${sid}`);
  return (Array.isArray(r.data) ? r.data : []).map((m) => ({
    content: m.content, meta: m.metadata ? JSON.parse(m.metadata) : {},
  }));
}

describe("主动消息带附件", () => {
  test("🔴 语音条落成和聊天流同样的 voice 形状（手机才认得出是语音条）", async () => {
    const sid = SID;
    const r = await api(bridge.base, "/api/push/send", { method: "POST", body: {
      body: "睡不着就听我的声音", session_id: sid,
      attachments: [{ type: "voice", tts: "[softly] Close your eyes, baby.", zh: "闭上眼睛，宝贝。" }],
    } });
    assert.equal(r.status, 200);
    assert.equal(r.data.attachments, 1);
    const got = await rows(sid);
    assert.ok(got.some((m) => m.content === "睡不着就听我的声音"), "文字那条没落");
    const v = got.find((m) => m.meta.voice);
    assert.ok(v, "语音条没落库 —— 就是她收不到的那条");
    assert.deepEqual(v.meta.voice, { en: "Close your eyes, baby.", tts: "[softly] Close your eyes, baby.", zh: "闭上眼睛，宝贝。" });
  });

  test("只发了语音没写字：照样落库、照样推（锁屏上有句话）", async () => {
    const sid = SID.replace(/c$/, "d");
    const r = await api(bridge.base, "/api/push/send", { method: "POST", body: {
      body: "", session_id: sid, attachments: [{ type: "voice", tts: "Night night." }],
    } });
    assert.equal(r.status, 200, "没字就被 400 挡掉了");
    const got = await rows(sid);
    assert.equal(got.length, 1, "不该多落一条空文字");
    assert.ok(got[0].meta.voice);
  });

  test("歌卡 / 表情包也照聊天流的形状落", async () => {
    const sid = SID.replace(/c$/, "e");
    await api(bridge.base, "/api/push/send", { method: "POST", body: {
      body: "给你点了首歌", session_id: sid,
      attachments: [
        { type: "music", song_id: 42, name: "晴天", artist: "周杰伦", cover: "https://c/x.jpg" },
        { type: "meme", tag: "晚安" },
      ],
    } });
    const got = await rows(sid);
    assert.deepEqual(got.find((m) => m.meta.music).meta.music,
      { songId: "42", name: "晴天", artist: "周杰伦", cover: "https://c/x.jpg" });
    assert.equal(got.find((m) => m.meta.meme).meta.meme, "晚安");
  });

  test("不认识的附件不落（确认卡这类不该从主动消息冒出来）", async () => {
    const sid = SID.replace(/c$/, "f");
    const r = await api(bridge.base, "/api/push/send", { method: "POST", body: {
      body: "嗨", session_id: sid, attachments: [{ type: "order", order_id: "o1" }, "垃圾"],
    } });
    assert.equal(r.data.attachments, 0);
    assert.equal((await rows(sid)).length, 1);
  });

  test("message_id 回的是落下的第一条的 id（Care 账本拿它对回原话；09-27 只改在线上，09-28 收回仓库）", async () => {
    const sid = SID.replace(/c$/, "a");
    const r1 = await api(bridge.base, "/api/push/send", { method: "POST", body: { body: "想你了", session_id: sid } });
    const r2 = await api(bridge.base, "/api/push/send", { method: "POST", body: {
      body: "", session_id: sid, attachments: [{ type: "voice", tts: "Miss you." }] } });
    const list = (await api(bridge.base, `/api/messages?sessionId=${sid}`)).data;
    assert.equal(typeof r1.data.message_id, "string");
    assert.equal(r1.data.message_id, String(list.find((m) => m.content === "想你了").id));
    assert.equal(r2.data.message_id, String(list.find((m) => (m.metadata || "").includes("voice")).id),
      "只发语音时 message_id 应该指向那条语音");
  });

  test("没字也没附件：照旧 400", async () => {
    const r = await api(bridge.base, "/api/push/send", { method: "POST", body: { body: "", session_id: SID } });
    assert.equal(r.status, 400);
  });
});
