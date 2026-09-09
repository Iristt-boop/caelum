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
  /* 🔴 **qwen 现在不在任何一条链上**（2026-09-07 回滚）。
   *
   * 换厂商那一版把手机也一起切走了（见 PROJECT.md 47.7 ⑤），糖糖说
   * 「app 没改过来。你先回滚到之前。」—— 所以两条链都回到今天之前的样子：
   * **纯 ElevenLabs 三级降级，和她昨天听到的一模一样**。
   *
   * 桌面那条阿里的路**代码全在、一行就能开**（把 "qwen" 放回 desktop 链首），
   * 但要等两件事先发生：
   *   ① 她跑 `scripts/tts-audition.mjs` 听过音色、定下 `QWEN_TTS_VOICE`
   *   ② 在她真机上验一通，确认桌面这条链没有反过来影响手机
   *
   * ⚠️ 在那之前**不要因为「代码写好了」就顺手打开** ——
   * 这次的教训就是「能用」和「该当默认」是两件事。 */
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
