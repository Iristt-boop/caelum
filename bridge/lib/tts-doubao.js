/**
 * 豆包（火山引擎）流式 TTS 适配 —— v3 单向流式（HTTP chunked/SSE）。
 *
 * 为什么独立一个文件：**协议常量没在官方静态文档里坐实**（docs.volcengine.com
 * 是 JS 渲染的，抓不到正文），请求头字段名和响应帧结构以 `scripts/tts-doubao-selftest.mjs`
 * 拿真实密钥跑一次为准。这里把所有可变的都提成 env，跑不通时改 env 不用改代码。
 *
 * 协议（按 2026-10 调研的口径）：
 *   POST {DOUBAO_TTS_URL}
 *   头   X-Api-App-Key:    <appid>
 *        X-Api-Access-Key: <access token>
 *        X-Api-Resource-Id: 公版音色 seed-tts-2.0 / 复刻音色 seed-icl-2.0
 *   体   { user:{uid}, req_params:{ text, speaker, audio_params:{format:"mp3", sample_rate:24000} } }
 *   响应 SSE：一行行 `data:{json}`，音频是其中某个字段里的 base64。
 *
 * 🔴 资源 ID 跟着音色走，**传错会报 mismatched**（油猴脚本社区踩过同款坑）：
 *    公版音色配 seed-tts-2.0，声音复刻 2.0 的音色配 seed-icl-2.0。
 *    复刻音色 ID 在控制台长 `S_` 开头 —— 按 `voiceClone` 配置项判断即可，
 *    不猜格式：she 配了 clone 音色走 clone 资源，没配就只能公版。
 *
 * ⚠️ 这是**指令场景专用**（OS 语音指令），和 `/api/tts` 那条降级链是两个产品：
 *    通话/语音条继续走 ele（她的拍板），这边不参与降级链、也**不许**被设成全局默认。
 *    「她没点头的东西不该在代码里等着被打开」—— 见 /api/tts 那段阿里 TTS 的教训。
 */

const DOUBAO_TTS_URL =
  process.env.DOUBAO_TTS_URL || "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse";
const DOUBAO_TTS_APP_ID = process.env.DOUBAO_TTS_APP_ID || "";
const DOUBAO_TTS_ACCESS_TOKEN = process.env.DOUBAO_TTS_ACCESS_TOKEN || "";
const DOUBAO_TTS_RESOURCE_ID = process.env.DOUBAO_TTS_RESOURCE_ID || "seed-tts-2.0";
const DOUBAO_TTS_RESOURCE_ID_CLONE = process.env.DOUBAO_TTS_RESOURCE_ID_CLONE || "seed-icl-2.0";
/** 公版默认音色：她要是想用公版说话（不克隆），配这个；不配 = 只有 clone 可用 */
const DOUBAO_TTS_VOICE = process.env.DOUBAO_TTS_VOICE || "";
/** 声音复刻 2.0 的音色 ID（控制台 `S_` 开头）。复刻完成后由她配进来。 */
const DOUBAO_TTS_VOICE_CLONE = process.env.DOUBAO_TTS_VOICE_CLONE || "";

export function doubaoTtsReady() {
  return Boolean(DOUBAO_TTS_APP_ID && DOUBAO_TTS_ACCESS_TOKEN);
}

/** 想要哪个音色。"clone" = 复刻音色（指令场景的默认）；其他值 = 显式公版音色 ID。 */
export function doubaoTtsPickSpeaker(want) {
  if (want && want !== "clone") return { speaker: want, resourceId: DOUBAO_TTS_RESOURCE_ID };
  if (DOUBAO_TTS_VOICE_CLONE) return { speaker: DOUBAO_TTS_VOICE_CLONE, resourceId: DOUBAO_TTS_RESOURCE_ID_CLONE };
  // clone 没配：退公版（配了就用，没配说明这套 env 只为 clone 服务的，报错让人配）
  if (DOUBAO_TTS_VOICE) return { speaker: DOUBAO_TTS_VOICE, resourceId: DOUBAO_TTS_RESOURCE_ID };
  return null;
}

/** 发起一次流式合成。返回原生 fetch Response（body 是流）。 */
export async function doubaoTtsRequest({ text, speaker, resourceId }) {
  const headers = {
    "Content-Type": "application/json",
    "X-Api-App-Key": DOUBAO_TTS_APP_ID,
    "X-Api-Access-Key": DOUBAO_TTS_ACCESS_TOKEN,
    "X-Api-Resource-Id": resourceId,
  };
  const body = {
    user: { uid: "nox-os-command" },
    req_params: {
      text,
      speaker,
      audio_params: { format: "mp3", sample_rate: 24000 },
    },
  };
  return fetch(DOUBAO_TTS_URL, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(30000),
  });
}

/**
 * 🔴 从一帧响应 JSON 里把音频抠出来 —— **字段名没坐实，所以容错**。
 *
 * v3 的帧里音频是 base64，但字段可能叫 data / audio / chunk.data……
 * 与其赌一个名字，不如取「整棵 JSON 里最长的、长得像 base64 的字符串」。
 * 音频帧的 base64 至少几百字节，和别的字段（id、event 名）不会混淆。
 * 自检脚本会把它抠出的第一段音频写盘验证 —— 错了当场就听得出来。
 */
export function extractAudioB64(obj) {
  let best = "";
  const walk = (v) => {
    if (typeof v === "string") {
      if (v.length > best.length && /^[A-Za-z0-9+/=\r\n]+$/.test(v) && v.length >= 128) best = v;
    } else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object") Object.values(v).forEach(walk);
  };
  walk(obj);
  return best || null;
}

/**
 * 把上游响应流解析成音频片段。SSE 为主（/sse 端点），非 SSE 的
 * JSON 行流也顺手兼容（同一套按行解析）。
 *
 * handlers: { onAudio(Buffer), onDone(), onError(message) } —— 三选一必有一个被调到。
 */
export async function parseDoubaoStream(res, handlers) {
  const { onAudio, onDone, onError } = handlers;
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    onError?.(`upstream ${res.status}: ${detail.slice(0, 300)}`);
    return;
  }

  const decoder = new TextDecoder();
  let buf = "";
  let sawAudio = false;
  let errored = false;
  /** 错误只报第一个，而且**已经吐出音频后不再报错** —— 半截音频加一个错误帧会让前端没法处理 */
  const emitError = (m) => {
    if (errored || sawAudio) return;
    errored = true;
    onError?.(m);
  };

  const handleLine = (line) => {
    const t = line.trim();
    if (!t) return;
    const payload = t.startsWith("data:") ? t.slice(5).trim() : t;
    if (!payload || payload === "[DONE]") return;
    let obj;
    try { obj = JSON.parse(payload); } catch { return; }
    const b64 = extractAudioB64(obj);
    if (b64) {
      sawAudio = true;
      onAudio?.(Buffer.from(b64, "base64"));
      return;
    }
    // 没有音频的帧 = 元信息或错误。错误要说出来，元信息记一笔就走
    const errMsg = obj?.error?.message || obj?.message || obj?.error;
    if (errMsg && !sawAudio) {
      emitError(String(errMsg).slice(0, 300));
      return;
    }
    // 终止语义（字段名同样没坐实，见谁像就认谁）
    if (obj?.is_final === true || obj?.finished === true || obj?.done === true) onDone?.();
  };

  const reader = res.body.getReader();
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        handleLine(buf.slice(0, nl));
        buf = buf.slice(nl + 1);
      }
    }
    buf += decoder.decode();
    handleLine(buf);
  } finally {
    try { reader.releaseLock(); } catch { /* 已经关了 */ }
  }
  if (sawAudio) onDone?.();
  else if (!errored) onError?.("no audio frames in stream");
}
