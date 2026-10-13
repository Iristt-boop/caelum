/**
 * 这一次说话，按什么顺序试各家 TTS。
 *
 * 抽出来是因为**它出过一次事，而且是糖糖替我发现的**（2026-09-07）：
 * 我把 `["qwen", ...]` 写成了全局默认，手机通话的第一句和聊天里的语音条
 * 都不带 `engine`/`profile`，于是**全被切到了阿里** —— 她当场问
 * 「手机端也降级成 qwen3 了？」。
 *
 * 更糟的是英语陪练：降级模型拿到的是剥掉情绪标签的文本，
 * 于是英文会被一个中文优先的模型念、`[whining]` 那套全丢 ——
 * 和 2026-08-15 修掉的「edge-tts 用中文发音人念英文」是同一种错。
 *
 * 一个纯函数 + 几条断言就能挡住，所以它现在住在 lib 里。
 */

/** 各端的偏好顺序。**顺序本身就是需求**，不是实现细节。 */
export const CHAINS = {
  /* 🔴 **两端都是纯 ElevenLabs 三级降级，阿里 2026-09-09 整套删掉了。**
   *
   * 09-07 接过一版并且当了全局默认，糖糖那天说「app 没改过来。你先回滚到之前。」
   * 09-09 她把话说全了：**她从来没听过那个音色**，
   * 「我没听过阿里的音色，很不好听」「我还没决定用什么 tts 那个会话就直接用了阿里」。
   *
   * 所以这次不是把它从链上摘掉、代码留着「一行就能开」—— 是连 `qwenTts()`
   * 一起删干净了。留一条后路等于把「要不要换声音」这个决定继续放在代码里，
   * 而那个决定**从来不属于代码**，属于她的耳朵。
   *
   * ⚠️ 以后要再接任何一家：**先让她听 → 她点头 → 再动这里**。
   * 「跑通了」和「该当默认」是两件事，这一节已经栽过两次。 */
  /* 2026-10-07：v4 上线（09-28），她指定 PWA 通话换 v4——链首插 eleven-v4，
     v3 降为第二档（v4 失败自动落回熟悉的声音）。 */
  /* 2026-10-10：搬到 N150 后 ElevenLabs 经东京节点出口，节点断了整档 ElevenLabs 都连不上。
     **她点名**：降级到豆包，用 OS 实时通话里她一直在听的那个复刻声音（clone），
     不要直接掉到 edge 那个陌生男声。edge 仍是最后一档。 */
  /* 2026-10-13（配置层 P4）：**按场景分两条，不分手机电脑**（她定的）——
   *   call  打电话（手机 / 电脑一样），ElevenLabs 打头
   *   live  OS 实时语音，豆包打头（在链首时走流式端点，首句快）
   * 这两条只是**默认**：她在 Models 页 Voice 里改过的，以 Core 那份为准（lib/voice-chains.js 读）。 */
  call: ["eleven-v4", "eleven-v3", "eleven-turbo", "doubao", "edge"],
  live: ["doubao", "eleven-v4", "eleven-v3", "eleven-turbo", "edge"],
};

/** 接好了的引擎（试听只许这几个；加新的一家要写代码） */
export const ENGINES = ["eleven-v4", "eleven-v3", "eleven-turbo", "doubao", "edge"];

/** ElevenLabs 那几档。**网络层**失败（连不上 / 超时）时同一家的其余档位一起跳过 ——
 *  同一个主机，换个模型名也连不上，挨个等超时一分多钟，电话就卡死了。 */
export const ELEVEN_ENGINES = ["eleven-v4", "eleven-v3", "eleven-turbo"];

/** 不认识的 profile（以及**根本没传**的老调用方，比如语音条、老的 phone / desktop）一律按 call 算。 */
export const DEFAULT_PROFILE = "call";

/** profile → 链名。只有 `live` 是 live，其余（phone / desktop / 没传）都是 call */
export function chainName(profile) {
  return String(profile || "") === "live" ? "live" : DEFAULT_PROFILE;
}

/**
 * @param {string} [profile] `live` = OS 实时语音；其余都按 call
 * @param {string} [engine]  这一轮上一句用的引擎 —— 从它开始往下试，
 *                           别在一段话中间换声音
 * @param {object} [opts]
 * @param {object} [opts.chains] 她在 Models 页定的链（Core 那份）；没有就用上面的默认
 * @param {string} [opts.after]  这一档刚失败过（Live 的豆包流式）—— 从它**后面**接着试，不再重试它
 * @returns {string[]} 这次要按顺序尝试的引擎（永远不为空）
 */
export function pickChain(profile, engine, { chains, after } = {}) {
  const name = chainName(profile);
  const mine = chains && Array.isArray(chains[name]) && chains[name].filter((e) => ENGINES.includes(e));
  const base = mine && mine.length ? mine : CHAINS[name];
  if (after) {
    const i = base.indexOf(String(after));
    if (i >= 0 && i < base.length - 1) return base.slice(i + 1);
  }
  const from = base.indexOf(String(engine || ""));
  //: `from === 0` 也走整条 —— 切片出来是同一个数组，没必要特殊处理；
  //: `-1`（没传/不认识）同理。只有真的降过级才需要砍掉前面几档
  return from > 0 ? base.slice(from) : base;
}
