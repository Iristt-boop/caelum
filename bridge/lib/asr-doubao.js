/**
 * 豆包大模型流式语音识别（sauc bigmodel）WS 协议封装 —— 给 /ws/stt/doubao 中继用。
 *
 * 协议（volcengine 大模型流式语音识别 V3，全双工）：
 *   WSS wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
 *   头  X-Api-App-Key / X-Api-Access-Key / X-Api-Resource-Id: volc.bigasr.sauc.duration
 *   帧结构：4 字节头 + payload
 *     byte0 = (版本 0b0001 << 4) | (头长 0b0001)            → 0x11
 *     byte1 = (消息类型 << 4) | (标志)
 *             类型：0b0001 全量请求(首帧配置) / 0b0010 纯音频 / 0b1001 服务端响应 / 0b1011 错误
 *             标志：0b0001 正序号在前 / 0b0010 负序号（末包）
 *     byte2 = (序列化 << 4) | (压缩)  —— JSON 0b0001，无 0b0000，gzip 0b0001
 *     byte3 = 保留 0x00
 *   音频帧 payload = int32 序号(BE) + gzip(PCM)；末包 = int32 负序号，无音频。
 *   服务端响应 payload = gzip(JSON)：{ result: { text, utterances:[{text, definite}] } }
 */

import zlib from "node:zlib";

const URL_ASR =
  process.env.DOUBAO_ASR_URL || "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel";
const APP_ID = process.env.DOUBAO_TTS_APP_ID || "";       // 与 TTS 同一对凭证（同应用）
const ACCESS_TOKEN = process.env.DOUBAO_TTS_ACCESS_TOKEN || "";
const RESOURCE_ID = process.env.DOUBAO_ASR_RESOURCE_ID || "volc.bigasr.sauc.duration";

export function doubaoAsrReady() {
  return Boolean(APP_ID && ACCESS_TOKEN);
}

function frame(msgType, flags, serialization, compression, payload) {
  const head = Buffer.from([
    0x11,
    (msgType << 4) | flags,
    (serialization << 4) | compression,
    0x00,
  ]);
  return payload?.length ? Buffer.concat([head, payload]) : head;
}

const gzip = (buf) => zlib.gzipSync(buf);

/** 首帧：全量请求，JSON 配置（16k PCM、大模型名、标点） */
export function configFrame() {
  const cfg = {
    user: { uid: "nox-os-live" },
    audio_config: { channel: 1, format: "pcm", sample_rate: 16000 },
    request: { model_name: "bigmodel", enable_punc: true, show_utterances: true },
  };
  return frame(0x01, 0x00, 0x01, 0x01, gzip(Buffer.from(JSON.stringify(cfg))));
}

/** 音频帧：正序号 + gzip(PCM) */
export function audioFrame(seq, pcm) {
  const s = Buffer.alloc(4);
  s.writeInt32BE(seq, 0);
  return frame(0x02, 0x01, 0x00, 0x01, Buffer.concat([s, gzip(pcm)]));
}

/** 末包（说完了）：负序号，无音频——服务端会回一帧带定稿文本的响应 */
export function endUtteranceFrame(seq) {
  const s = Buffer.alloc(4);
  s.writeInt32BE(-seq, 0);
  return frame(0x02, 0x02, 0x00, 0x01, s);
}

/**
 * 解析一帧服务端响应。返回：
 *   { type: "full", text, final } —— text 为当前全量文本；final=true 表示含定稿结果
 *   { type: "error", message }
 * 无法解析返回 null（非响应帧，忽略）。
 */
export function parseResponse(buf) {
  if (buf.length < 4) return null;
  const msgType = (buf[1] >> 4) & 0x0f;
  const compression = buf[2] & 0x0f;
  const serialization = (buf[2] >> 4) & 0x0f;
  const headerSize = (buf[0] & 0x0f) * 4;
  let payload = buf.subarray(headerSize);
  try {
    if (compression === 0x01) payload = zlib.gunzipSync(payload);
  } catch {
    return { type: "error", message: "gunzip failed" };
  }
  if (msgType === 0x0b) {
    let msg = "asr error";
    try { msg = JSON.parse(payload.toString()).message || msg; } catch { /* 原样 */ }
    return { type: "error", message: String(msg).slice(0, 200) };
  }
  if (msgType !== 0x09) return null;
  if (serialization !== 0x01) return null;
  let obj;
  try { obj = JSON.parse(payload.toString()); } catch { return null; }
  const result = obj?.result;
  if (!result) return null;
  const text = String(result.text || "");
  // 定稿判定：最后一个 utterance definite，或服务端在末包响应里给出的完整文本
  const utts = result.utterances || [];
  const final = utts.length > 0 && utts[utts.length - 1].definite === true;
  return { type: "full", text, final };
}
