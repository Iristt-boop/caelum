/**
 * 豆包大模型流式语音识别（sauc bigmodel_async）WS 协议封装。
 *
 * 🔴 协议要点（照一个跑通的对照实现逐字校过，2026-10-06）：
 *   WSS wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
 *   握手头（除了鉴权三件套，还有三个容易漏的）：
 *     X-Api-App-Key / X-Api-Access-Key / X-Api-Resource-Id
 *     X-Api-Request-Id: <uuid>  X-Api-Connect-Id: <uuid>
 *     X-Api-Sequence: "-1"   ← 声明「我不发序号，服务端自动编号」；
 *       旧式协议要自己数序号（错一个就被踢），async 版本用这个头免去。
 *   帧 = header(4B) + 长度(4B BE) + 载荷，**全程不压缩**（压缩位恒 0b0000）：
 *     首帧  type 0b0001 flags 0b0000  载荷 = JSON（user/audio/request）
 *     音频  type 0b0010 flags 0b0000  载荷 = 裸 PCM
 *     末包  type 0b0010 flags 0b0010  载荷 = 空（flag 即「最后一包」）
 *   配置的音频键名是 **audio**（不是 audio_config！格式字段读取为空的坑），
 *   request.model_name 目前只有 "bigmodel"。
 *   服务端响应 type 0b1001（JSON），result.text 流式更新、
 *   result.utterances[].definite 表示一句定稿。
 */

import zlib from "node:zlib";
import crypto from "node:crypto";

export const DOUBAO_ASR_WS_URL =
  process.env.DOUBAO_ASR_URL ||
  "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async";
export const DOUBAO_ASR_RESOURCE_ID =
  process.env.DOUBAO_ASR_RESOURCE_ID || "volc.bigasr.sauc.duration";

const MSG_FULL_REQUEST = 0b0001;
const MSG_AUDIO_ONLY = 0b0010;
const FLAG_LAST_PACKET = 0b0010;
const SER_JSON = 0b0001;
const COMP_NONE = 0b0000;

export function doubaoAsrHandshakeHeaders(appId, accessToken) {
  return {
    "X-Api-App-Key": appId,
    "X-Api-Access-Key": accessToken,
    "X-Api-Resource-Id": DOUBAO_ASR_RESOURCE_ID,
    "X-Api-Request-Id": crypto.randomUUID(),
    "X-Api-Connect-Id": crypto.randomUUID(),
    "X-Api-Sequence": "-1",
  };
}

function frame(msgType, flags, payload) {
  const head = Buffer.from([
    0x11,
    (msgType << 4) | flags,
    (SER_JSON << 4) | COMP_NONE,
    0x00,
  ]);
  const size = Buffer.alloc(4);
  size.writeUInt32BE(payload?.byteLength || 0, 0);
  return Buffer.concat([head, size, payload || Buffer.alloc(0)]);
}

/** 首帧：JSON 配置。键名必须是 audio（audio_config 读出来是空）。 */
export function configFrame() {
  const cfg = {
    user: { uid: "nox-os-live" },
    audio: { format: "pcm", codec: "raw", rate: 16000, bits: 16, channel: 1 },
    request: {
      model_name: "bigmodel",
      enable_punc: true,
      enable_itn: true,
      show_utterances: true,
      result_type: "single",
      end_window_size: 200,
      reqid: crypto.randomUUID(),
      workflow: "audio_in,resample,partition,vad,fe,decode,itn,nlu_punctuate",
    },
  };
  return frame(MSG_FULL_REQUEST, 0b0000, Buffer.from(JSON.stringify(cfg)));
}

/** 音频帧：裸 PCM，无压缩无序号。 */
export function audioFrame(pcm) {
  return frame(MSG_AUDIO_ONLY, 0b0000, pcm);
}

/** 末包：flag 即「最后一包」，载荷为空。服务端随后下发定稿结果。 */
export function lastPacketFrame() {
  return frame(MSG_AUDIO_ONLY, FLAG_LAST_PACKET, Buffer.alloc(0));
}

/**
 * 解析一帧服务端响应。服务端帧布局（实测抓包对照）：
 *   header(4) + [序号 4B（flags 0b0001 时才有，服务端自动编号）] + 载荷长度 4B(BE) + 载荷
 * 返回：
 *   { type: "full", text, final }   text=当前全量文本；final=true 表示这是末包回执
 *   { type: "error", code, message }
 * 非 JSON 响应帧返回 null（忽略）。
 * ⚠️ flags=0b0011 的末包回执里 **没有 result.text**（只有 audio_info）——
 *   调用方要自己记住上一条 delta 的文本，在 final 时用。
 */
export function parseResponse(buf) {
  if (buf.length < 8) return null;
  const msgType = (buf[1] >> 4) & 0x0f;
  const flags = buf[1] & 0x0f;
  const compression = buf[2] & 0x0f;
  let offset = 4;
  if (flags & 0b0001) offset += 4;       // 正序号字段（服务端自动编号的回显）
  const size = buf.readUInt32BE(offset);
  offset += 4;
  let payload = buf.subarray(offset, offset + size);
  if (compression === 0b0001) {
    try { payload = zlib.gunzipSync(payload); } catch { return null; }
  }
  if (msgType === 0b1111) {
    let code = 0, message = "asr error";
    try {
      const obj = JSON.parse(payload.toString());
      code = obj?.code || 0;
      message = obj?.message || message;
    } catch { /* 原样 */ }
    return { type: "error", code, message: String(message).slice(0, 200) };
  }
  if (msgType !== 0b1001) return null;
  let obj;
  try { obj = JSON.parse(payload.toString()); } catch { return null; }
  const code = obj?.error?.code || obj?.code || 0;
  if (code && code !== 20000000 && code !== 1013) {
    // 20000000=OK；1013=这段没有有效语音（静音），都不是故障
    const message = obj?.error?.message || obj?.message || "";
    if (message) return { type: "error", code, message: String(message).slice(0, 200) };
  }
  const result = obj?.result;
  if (!result) return null;
  const utts = result.utterances || [];
  return {
    type: "full",
    text: String(result.text || ""),
    final: (flags & FLAG_LAST_PACKET) === FLAG_LAST_PACKET,
    lastUtterance: utts.length ? utts[utts.length - 1].text : undefined,
  };
}
