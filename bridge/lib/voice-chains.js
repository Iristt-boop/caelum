/**
 * 她在 Models 页定的声音链（配置层 P4，2026-10-13）。真源在 Core（settings_store/voice.py），这里每次合成前读。
 *
 * - 缓存 30 秒：通话一句一请求，不能每句都多打一次 Core
 * - 🔴 读不到就用 tts-chain.js 里的默认 —— **永远不会因为配置读不到而没声音**；读不到只记一次日志（不刷屏）
 * - 不含任何 key（Core 那个接口本来就只有引擎名）
 */
import { CHAINS } from "./tts-chain.js";

const TTL_MS = 30_000;

export function createVoiceChains({ coreUrl, fetchImpl = fetch, now = () => Date.now(), log = console } = {}) {
  let cache = null;          // { at, chains }
  let warned = false;

  async function get() {
    if (cache && now() - cache.at < TTL_MS) return cache.chains;
    try {
      const r = await fetchImpl(`${coreUrl}/api/nox/config/voice`, { signal: AbortSignal.timeout(2000) });
      const body = await r.json();
      const chains = {};
      for (const [name, c] of Object.entries(body?.chains || {})) {
        if (Array.isArray(c?.engines) && c.engines.length) chains[name] = c.engines;
      }
      if (!chains.call || !chains.live) throw new Error("Core 回的声音链不全");
      cache = { at: now(), chains };
      if (warned) { log.log?.("[voice-chains] Core 的声音链又读到了"); warned = false; }
      return chains;
    } catch (e) {
      if (!warned) {
        log.warn?.(`[voice-chains] 读不到 Core 的声音链，先按默认顺序走：${e?.message || e}`);
        warned = true;
      }
      //: 不缓存失败 —— 下一句再试；但也不让这一句等：直接用默认
      return cache?.chains || CHAINS;
    }
  }

  /** 她刚在页面上改过：下一句就按新的走，不等 30 秒 */
  function invalidate() { cache = null; }

  return { get, invalidate };
}
