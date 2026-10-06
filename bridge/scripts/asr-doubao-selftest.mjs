#!/usr/bin/env node
/**
 * 豆包流式识别冒烟 —— 真实凭证连 sauc bigmodel_async，喂 PCM 听写。
 *
 *   # 造测试音频（服务器有 ffmpeg；doubao-smoke.mp3 是 TTS 自检产物）
 *   ffmpeg -y -i doubao-smoke.mp3 -f s16le -ar 16000 -ac 1 smoke.pcm
 *   DOUBAO_TTS_APP_ID=xxx DOUBAO_TTS_ACCESS_TOKEN=xxx \
 *     node scripts/asr-doubao-selftest.mjs smoke.pcm
 *
 * 通过 = 打出接近「指令收到，这就去办」的识别文本。
 * 协议细节见 lib/asr-doubao.js 顶上的注释（async 版：无序号、不压缩）。
 */
import WebSocket from "ws";
import fs from "node:fs";
import {
  DOUBAO_ASR_WS_URL,
  DOUBAO_ASR_RESOURCE_ID,
  doubaoAsrHandshakeHeaders,
  configFrame,
  audioFrame,
  lastPacketFrame,
  parseResponse,
} from "../lib/asr-doubao.js";

const APP_ID = process.env.DOUBAO_TTS_APP_ID || "";
const TOKEN = process.env.DOUBAO_TTS_ACCESS_TOKEN || "";
const pcmPath = process.argv[2] || "smoke.pcm";
if (!APP_ID || !TOKEN) { console.error("缺 DOUBAO_TTS_APP_ID / DOUBAO_TTS_ACCESS_TOKEN"); process.exit(1); }
if (!fs.existsSync(pcmPath)) { console.error(`PCM 不存在：${pcmPath}（ffmpeg 转，见文件头注释）`); process.exit(1); }
void DOUBAO_ASR_RESOURCE_ID;

const ws = new WebSocket(DOUBAO_ASR_WS_URL, {
  headers: doubaoAsrHandshakeHeaders(APP_ID, TOKEN),
});

const t0 = Date.now();
const pcm = fs.readFileSync(pcmPath);
let lastText = "";
const CHUNK = 3200; // 0.1s @16k s16

ws.on("open", () => {
  console.log(`WS opened，喂 ${(pcm.length / 32000).toFixed(1)}s 音频`);
  ws.send(configFrame());
  let off = 0;
  const pump = setInterval(() => {
    if (off >= pcm.length) {
      clearInterval(pump);
      ws.send(lastPacketFrame());   // 末包：服务端随后回定稿
      return;
    }
    ws.send(audioFrame(pcm.subarray(off, off + CHUNK)));
    off += CHUNK;
  }, 100);
});

ws.on("message", (data, isBinary) => {
  const buf = isBinary ? Buffer.from(data) : Buffer.from(String(data));
  const parsed = parseResponse(buf);
  if (!parsed) return;
  if (parsed.type === "error") {
    console.error(`上游错误 code=${parsed.code}:`, parsed.message);
    process.exit(2);
  }
  if (parsed.text) {
    console.log(`[+${Date.now() - t0}ms] ${parsed.text}`);
    lastText = parsed.text;
  }
  if (parsed.final) {
    console.log(`定稿：${lastText}`);
    ws.close();
    process.exit(0);
  }
});

ws.on("error", (e) => { console.error("WS error:", e.message); process.exit(3); });
setTimeout(() => { console.error("超时（10s 内没有定稿）"); process.exit(4); }, 10000);
