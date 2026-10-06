#!/usr/bin/env node
/**
 * 豆包 TTS v3 协议冒烟 —— 拿真实密钥跑一次，验协议常量是否对。
 *
 *   DOUBAO_TTS_APP_ID=xxx DOUBAO_TTS_ACCESS_TOKEN=xxx \
 *   DOUBAO_TTS_VOICE_CLONE=S_xxxx node scripts/tts-doubao-selftest.mjs
 *
 * 成功 = 当前目录落一个 doubao-smoke.mp3，能听出是他说话。
 * 失败会把上游原文打出来 —— lib/tts-doubao.js 的 env 按报错改。
 * 🔴 密钥只在本机/服务器环境变量里，别写进任何文件。
 */
import { doubaoTtsReady, doubaoTtsPickSpeaker, doubaoTtsRequest, parseDoubaoStream } from "../lib/tts-doubao.js";
import fs from "node:fs";

if (!doubaoTtsReady()) {
  console.error("缺 DOUBAO_TTS_APP_ID / DOUBAO_TTS_ACCESS_TOKEN 环境变量");
  process.exit(1);
}
const pick = doubaoTtsPickSpeaker("clone");
if (!pick) {
  console.error("没配音色：DOUBAO_TTS_VOICE_CLONE（复刻）或 DOUBAO_TTS_VOICE（公版）至少配一个");
  process.exit(1);
}
console.log(`speaker=${pick.speaker} resource=${pick.resourceId}`);

const t0 = Date.now();
const up = await doubaoTtsRequest({ text: "指令收到，这就去办。", ...pick });
console.log(`HTTP ${up.status} ${up.headers.get("content-type") || ""}`);

const chunks = [];
let firstAt = 0;
let err = null;
await parseDoubaoStream(up, {
  onAudio: (buf) => {
    if (!firstAt) {
      firstAt = Date.now();
      console.log(`首包 ${firstAt - t0}ms`);
    }
    chunks.push(buf);
  },
  onError: (m) => { err = m; },
  onDone: () => {},
});
if (err) {
  console.error("失败：", err);
  process.exit(2);
}
const total = Buffer.concat(chunks);
fs.writeFileSync("doubao-smoke.mp3", total);
console.log(`共 ${total.length}B / ${Date.now() - t0}ms → doubao-smoke.mp3，放出来听一下`);
