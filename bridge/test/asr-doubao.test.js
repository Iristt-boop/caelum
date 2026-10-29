// 豆包流式识别的配置帧 + 回包解析（2026-10-29：一段话只留最后一句）。
import test from "node:test";
import assert from "node:assert/strict";
import { configFrame, parseResponse } from "../lib/asr-doubao.js";

const payloadOf = (frame) => JSON.parse(frame.subarray(8, 8 + frame.readUInt32BE(4)).toString());

test("🔴 配置帧要 full：single 下她停 200ms 就断句，定稿只剩最后一句", () => {
  const cfg = payloadOf(configFrame());
  assert.equal(cfg.request.result_type, "full");
  assert.equal(cfg.audio.rate, 16000, "前端喂的是 16k");
});

/** 照 10-29 抓到的服务端帧拼：header(4) + 序号(4) + 长度(4) + JSON */
function serverFrame(flags, obj) {
  const body = Buffer.from(JSON.stringify(obj));
  const head = Buffer.from([0x11, (0b1001 << 4) | flags, 0x10, 0x00]);
  const seq = Buffer.alloc(4); seq.writeInt32BE(7, 0);
  const size = Buffer.alloc(4); size.writeUInt32BE(body.length, 0);
  return Buffer.concat([head, seq, size, body]);
}

test("末包回执（flags=0011）认成 final，full 模式下带着整段文字", () => {
  const text = "今天晚上我们吃什么呀？我有点饿了。";
  const p = parseResponse(serverFrame(0b0011, { audio_info: {}, result: { text, utterances: [{ text: "我有点饿了。" }] } }));
  assert.equal(p.final, true);
  assert.equal(p.text, text);
});

test("中间帧（flags=0001）不是 final", () => {
  const p = parseResponse(serverFrame(0b0001, { audio_info: {}, result: { text: "今天", utterances: [] } }));
  assert.equal(p.final, false);
  assert.equal(p.text, "今天");
});
