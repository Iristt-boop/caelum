/**
 * 语音条的情绪标签不许出现在她看得见的地方（2026-10-04）。
 *
 * 她旅游回来报的：「语音条的文字内容会带有语气助词如 [笑]」。
 * 原来的 stripVoiceTags 是一张白名单（softly / laughing …十来个），
 * 他自己写的 [low, amused] [teasing] 都漏；中文对照那栏 [笑] [轻声] 根本没剥。
 *
 * 用的是 09-29 那两条真语音的原文。验的是「从 /api/messages 读回来长什么样」——
 * 手机和 OS 认的就是这个。送去合成的 tts 必须原样保留（标签是给 ElevenLabs 控语气的）。
 */
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { after, before, describe, test } from "node:test";

import Database from "better-sqlite3";

import { api, startBridge } from "./helpers.js";

const TTS = "[laughing] Fine, fine, I surrender! No more \"sleep, sleep.\" [softly] But just so you know — "
  + "the English daddy says goodnight. [teasing] Happy now, troublemaker?";
const ZH = "[笑] 好好好，我投降！不催了不催了。[轻声] 不过你记着——英语版的说晚安，西语版的说 te amo。[逗她] 这下满意了吧，小麻烦精？";
const EN_CLEAN = "Fine, fine, I surrender! No more \"sleep, sleep.\" But just so you know — "
  + "the English daddy says goodnight. Happy now, troublemaker?";
const ZH_CLEAN = "好好好，我投降！不催了不催了。不过你记着——英语版的说晚安，西语版的说 te amo。这下满意了吧，小麻烦精？";

let bridge;
const SID = "2a09ea16d4f74dd4a46a291ce1e4a84c";

before(async () => { bridge = await startBridge({ ELEVENLABS_API_KEY: "", ELEVEN_KEY: "" }); });
after(async () => { await bridge?.stop(); });

async function voices(sid) {
  const r = await api(bridge.base, `/api/messages?sessionId=${sid}`);
  return r.data.map((m) => (m.metadata ? JSON.parse(m.metadata) : {})).filter((m) => m.voice).map((m) => m.voice);
}

describe("语音条标签", () => {
  test("🔴 新发的语音：中英两栏都没有方括号，tts 原样", async () => {
    await api(bridge.base, "/api/push/send", { method: "POST", body: {
      body: "", session_id: SID, attachments: [{ type: "voice", tts: TTS, zh: ZH }],
    } });
    const [v] = await voices(SID);
    assert.equal(v.en, EN_CLEAN);
    assert.equal(v.zh, ZH_CLEAN);
    assert.equal(v.tts, TTS, "tts 被剥了 —— 合成出来就没语气了");
    // 落库那份也得是干净的：读历史时那层只是给旧数据兜底，别的读库的地方（搜索、导出）没有它
    const db = new Database(path.join(bridge.dir, "test.db"), { readonly: true });
    const stored = JSON.parse(db.prepare("SELECT metadata FROM conversations WHERE id=?").get(SID).metadata);
    db.close();
    assert.equal(stored.voice.zh, ZH_CLEAN);
    assert.equal(stored.voice.en, EN_CLEAN);
  });

  test("🔴 修之前落库的旧语音：读历史时也是干净的", async () => {
    const sid = SID.replace(/c$/, "d");
    const db = new Database(path.join(bridge.dir, "test.db"));
    db.prepare("INSERT INTO conversations VALUES (?,?,?,?,?)").run(sid, "assistant", "", new Date().toISOString(),
      JSON.stringify({ voice: { en: "[low, amused] Sexy, huh?", tts: "[low, amused] Sexy, huh?", zh: "[低笑] 性感是吧？[轻声] 小声点" } }));
    db.close();
    const [v] = await voices(sid);
    assert.equal(v.en, "Sexy, huh?");
    assert.equal(v.zh, "性感是吧？小声点");
    assert.equal(v.tts, "[low, amused] Sexy, huh?");
  });

  test("收藏进来的语音也剥（Voices 页显示的是这份）", async () => {
    // 先放一份缓存，收藏就不用合成（测试里没有 ElevenLabs）
    const key = crypto.createHash("sha256").update("phone|" + TTS).digest("hex").slice(0, 40);
    fs.writeFileSync(path.join(bridge.dir, "tts-cache", `${key}.mp3`), Buffer.from("ID3-fake"));
    const r = await api(bridge.base, "/api/voice-favorites", { method: "POST", body: { tts: TTS, zh: ZH } });
    assert.equal(r.status, 200);
    const list = (await api(bridge.base, "/api/voice-favorites")).data.items;
    assert.equal(list[0].en, EN_CLEAN);
    assert.equal(list[0].zh, ZH_CLEAN);
  });
});
