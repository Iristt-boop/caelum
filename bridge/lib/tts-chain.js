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
  desktop: ["eleven-v3", "eleven-turbo", "edge"],
  phone: ["eleven-v3", "eleven-turbo", "edge"],
};

/** 不认识的端（以及**根本没传**的老调用方）一律按手机算。 */
export const DEFAULT_PROFILE = "phone";

/**
 * @param {string} [profile] 哪一端：`desktop` / `phone`
 * @param {string} [engine]  这一轮上一句用的引擎 —— 从它开始往下试，
 *                           别在一段话中间换声音
 * @returns {string[]} 这次要按顺序尝试的引擎
 */
export function pickChain(profile, engine) {
  const base = CHAINS[String(profile || "")] || CHAINS[DEFAULT_PROFILE];
  const from = base.indexOf(String(engine || ""));
  //: `from === 0` 也走整条 —— 切片出来是同一个数组，没必要特殊处理；
  //: `-1`（没传/不认识）同理。只有真的降过级才需要砍掉前面几档
  return from > 0 ? base.slice(from) : base;
}
