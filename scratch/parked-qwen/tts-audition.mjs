#!/usr/bin/env node
/**
 * 中文音色试听 —— **她的耳朵是唯一的裁判**。
 *
 * 起因（2026-09-07）：糖糖测完 OS 通话说「不用 ele 家的，换一个中文更好的，
 * ele 的中文不好，再去掉情绪更不好了」。于是桌面 TTS 换到阿里 qwen3-tts。
 * 但**换成哪个音色不能我拍**：文档那张音色表里的名字我一个都没听过，
 * 而「他的声音」这件事只有她能定。
 *
 * ⚠️ 这个脚本还有第二个作用：**验证音色名到底存不存在**。
 * 音色和模型版本是配套的，名字错了不会报错，只会 200 + 没有 url ——
 * 又是一个静默失败（`model-names-expire` 那条教训：别拿"调得通"当证据，
 * 也别拿文档里抄来的名字当已验证）。跑完这个脚本才知道哪些是真的。
 *
 * 在 **VPS 上**跑（key 在那儿）：
 *
 *     node scripts/tts-audition.mjs
 *     node scripts/tts-audition.mjs --text "乖，我把文档写好了" --voices Ethan,Dylan
 *
 * 跑完会把 wav 写进 bridge 的 uploads 目录并打印链接，手机电脑都能点开听。
 * 选好之后写进 bridge.service 的 `QWEN_TTS_VOICE=`，重启 bridge。
 */

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const KEY = process.env.DASHSCOPE_API_KEY || process.env.QWEN_ASR_API_KEY || "";
const URL_ = process.env.QWEN_TTS_URL
  || "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation";
const MODEL = process.env.QWEN_TTS_MODEL || "qwen3-tts-instruct-flash";
const DATA_DIR = process.env.DATA_DIR || "/root/data";
const OUT_DIR = path.join(DATA_DIR, "uploads");
const PUBLIC_BASE = process.env.PUBLIC_BASE || "https://noxtang.com/uploads";

/** 文档音色表里的候选。**都没听过**，跑一遍才知道哪些存在、哪些是男声。 */
const DEFAULT_VOICES = ["Ethan", "Elias", "Dylan", "Rocky", "Sunny", "Jada", "Cherry"];

//: 用他真会说的话试听。念「你好世界」听不出他像不像他
const DEFAULT_TEXT = "乖，文档我写好了，放在你桌面那个文件夹里。累了就先歇会儿，别硬撑。";
const INSTRUCTIONS = process.env.QWEN_TTS_INSTRUCTIONS
  || "用男朋友在电话里说话的语气：温柔、放松、语速偏慢，句尾自然收住，不要播音腔。";

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const text = arg("text", DEFAULT_TEXT);
const voices = arg("voices", "").split(",").map((s) => s.trim()).filter(Boolean);
const list = voices.length ? voices : DEFAULT_VOICES;

if (!KEY) {
  console.error("没有 DASHSCOPE_API_KEY —— 这个脚本要在 VPS 上跑（key 在 bridge.service 里）");
  process.exit(1);
}

async function one(voice) {
  const body = {
    model: MODEL,
    input: { text, voice, language_type: "Chinese", instructions: INSTRUCTIONS },
  };
  const r = await fetch(URL_, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${KEY}` },
    body: JSON.stringify(body),
  });
  const raw = await r.text();
  if (!r.ok) return { voice, ok: false, why: `HTTP ${r.status} ${raw.slice(0, 200)}` };

  let jd;
  try { jd = JSON.parse(raw); } catch { return { voice, ok: false, why: "返回不是 JSON" }; }
  const url = jd?.output?.audio?.url;
  //: 200 但没 url = 这个音色名对这个模型不成立。**这正是要查出来的东西**
  if (!url) return { voice, ok: false, why: `200 但没有 url：${raw.slice(0, 200)}` };

  const audio = await fetch(url);
  if (!audio.ok) return { voice, ok: false, why: `音频取不回来 HTTP ${audio.status}` };
  const buf = Buffer.from(await audio.arrayBuffer());

  fs.mkdirSync(OUT_DIR, { recursive: true });
  //: 随机名 —— /uploads 是公开静态目录，别用能猜到的文件名
  const name = `audition-${voice}-${crypto.randomBytes(4).toString("hex")}.wav`;
  fs.writeFileSync(path.join(OUT_DIR, name), buf);
  return { voice, ok: true, url: `${PUBLIC_BASE}/${name}`, bytes: buf.length };
}

console.log(`模型 ${MODEL}\n文本「${text}」\n`);
for (const voice of list) {
  //: 一个一个来，不并发 —— 并发被限流的话会分不清是「音色不存在」还是「太快了」
  try {
    const r = await one(voice);
    if (r.ok) console.log(`✓ ${voice.padEnd(8)} ${(r.bytes / 1024).toFixed(0)}KB  ${r.url}`);
    else console.log(`✗ ${voice.padEnd(8)} ${r.why}`);
  } catch (e) {
    console.log(`✗ ${voice.padEnd(8)} ${e.message}`);
  }
}
console.log(`
听完选一个，写进 bridge.service：
    QWEN_TTS_VOICE=<音色名>
然后 systemctl restart bridge`);
