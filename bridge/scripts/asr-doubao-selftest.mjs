#!/usr/bin/env node
/**
 * 豆包流式识别冒烟 —— 拿真实凭证连 sauc bigmodel，喂一段 PCM 听写。
 *
 *   # 先造测试音频（服务器上有 ffmpeg；doubao-smoke.mp3 是 TTS 自检的产物）
 *   ffmpeg -y -i doubao-smoke.mp3 -f s16le -ar 16000 -ac 1 smoke.pcm
 *   DOUBAO_TTS_APP_ID=xxx DOUBAO_TTS_ACCESS_TOKEN=xxx node scripts/asr-doubao-selftest.mjs smoke.pcm
 *
 * 通过 = 打出接近「指令收到，这就去办」的识别文本。
 */
import WebSocket from "ws";
import fs from "node:fs";
import zlib from "node:zlib";

const APP_ID = process.env.DOUBAO_TTS_APP_ID || "";
const TOKEN = process.env.DOUBAO_TTS_ACCESS_TOKEN || "";
const pcmPath = process.argv[2] || "smoke.pcm";
if (!APP_ID || !TOKEN) { console.error("缺 DOUBAO_TTS_APP_ID / DOUBAO_TTS_ACCESS_TOKEN"); process.exit(1); }
if (!fs.existsSync(pcmPath)) { console.error(`PCM 不存在：${pcmPath}（用 ffmpeg 从 mp3 转，见文件头注释）`); process.exit(1); }

// ---- 帧构造（与 lib/asr-doubao.js 同一套，自检要独立跑所以复制一份）
const frame = (type, flags, ser, comp, payload) => {
  const head = Buffer.from([0x11, (type << 4) | flags, (ser << 4) | comp, 0x00]);
  return payload?.length ? Buffer.concat([head, payload]) : head;
};
const gz = (b) => zlib.gzipSync(b);

const ws = new WebSocket("wss://openspeech.bytedance.com/api/v3/sauc/bigmodel", {
  headers: {
    "X-Api-App-Key": APP_ID,
    "X-Api-Access-Key": TOKEN,
    "X-Api-Resource-Id": "volc.bigasr.sauc.duration",
  },
});

const t0 = Date.now();
let seq = 0;
const pcm = fs.readFileSync(pcmPath);
const CHUNK = 3200; // 0.1s @16k s16

ws.on("open", () => {
  console.log(`WS opened，喂 ${pcm.length}B PCM（${(pcm.length / 32000).toFixed(1)}s）`);
  const cfg = {
    user: { uid: "nox-selftest" },
    audio_config: { channel: 1, format: "pcm", sample_rate: 16000 },
    request: { model_name: "bigmodel", enable_punc: true, show_utterances: true },
  };
  ws.send(frame(0x01, 0x00, 0x01, 0x01, gz(Buffer.from(JSON.stringify(cfg)))));
  let off = 0;
  const pump = setInterval(() => {
    if (off >= pcm.length) {
      clearInterval(pump);
      const s = Buffer.alloc(4);
      s.writeInt32BE(-(seq + 1), 0);
      ws.send(frame(0x02, 0x02, 0x00, 0x01, s)); // 末包（负序号）
      return;
    }
    seq += 1;
    const s = Buffer.alloc(4);
    s.writeInt32BE(seq, 0);
    const chunk = pcm.subarray(off, off + CHUNK);
    off += CHUNK;
    ws.send(frame(0x02, 0x01, 0x00, 0x01, Buffer.concat([s, gz(chunk)])));
  }, 100);
});

ws.on("message", (data, isBinary) => {
  const buf = isBinary ? Buffer.from(data) : Buffer.from(String(data));
  if (buf.length < 4) return;
  const type = (buf[1] >> 4) & 0x0f;
  let payload = buf.subarray((buf[0] & 0x0f) * 4);
  try { if ((buf[2] & 0x0f) === 1) payload = zlib.gunzipSync(payload); } catch { return; }
  if (type === 0x0b) {
    console.error("上游错误:", payload.toString().slice(0, 300));
    process.exit(2);
  }
  if (type !== 0x09) return;
  try {
    const obj = JSON.parse(payload.toString());
    const text = obj?.result?.text;
    if (text) console.log(`[+${Date.now() - t0}ms] ${text}`);
    const utts = obj?.result?.utterances || [];
    if (utts.length && utts[utts.length - 1].definite) {
      console.log(`定稿：${text}`);
      ws.close();
      process.exit(0);
    }
  } catch { /* 非JSON帧 */ }
});

ws.on("error", (e) => { console.error("WS error:", e.message); process.exit(3); });
setTimeout(() => { console.error("超时（8s 内没有定稿）"); process.exit(4); }, 8000);
