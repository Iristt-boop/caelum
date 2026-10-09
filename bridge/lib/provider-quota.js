/**
 * 厂商的「额度 / 订阅」读数（手机 Models 页，2026-10-09）。
 *
 * 和 server.js 里的 PROVIDER_LEDGERS（DeepSeek / OpenRouter 的**余额**账本）不是一回事：
 * 这里读的是**订阅额度**——一段时间窗口里用掉多少、还剩多少、什么时候重置。
 * 统一成同一个形状，页面不用认每家的字段：
 *
 *   { ok, configured, tier, status, windows: [{ id, label, used, limit, unit, percent, resets_at }], error }
 *
 * 三家的现实（2026-10-09 实测 / 查文档）：
 *
 *   ElevenLabs  GET /v1/user/subscription，头 `xi-api-key`。官方公开接口。
 *               ⚠️ key 要开「User: Read」权限，否则 401 missing_permissions
 *               （她这把 key 当天实测就缺这个权限）。
 *   智谱 GLM    GET open.bigmodel.cn/api/monitor/usage/quota/limit —— **不在公开文档里**，
 *               是控制台自己用的监控接口，Coding Plan 的额度窗口读它。裸 key 和 Bearer 都认。
 *               字段随时可能变，所以这里**按形状认**（有 usage/currentValue 的才算窗口），
 *               认不出来就如实报「读不出来」，不猜。
 *               **账户余额**：`/api/biz/account/query-customer-account-report`（同样不在公开文档里，
 *               是控制台财务总览页自己调的）。2026-10-09 实测用同一把 API key 就能读，数字和
 *               控制台一致（可用余额 / 累计充值 / 赠送 / 累计消费 / 冻结）。
 *   豆包        余额在火山引擎费用中心 QueryBalanceAcct，要**账号级** IAM 访问密钥（AK/SK）+ V4 签名。
 *               语音产品的 APP ID / Access Token 调不了它。没配 AK/SK 就如实说没配。
 *
 * 失败不抛：每家各报各的 {ok:false, error}，一家挂了不拖累另两家（页面按家显示原因）。
 */

const TIMEOUT_MS = 10000;

const clampPct = (n) => Math.max(0, Math.min(100, Math.round(Number(n) || 0)));

/* ------------------------------------------------------------ ElevenLabs */

export function parseElevenSubscription(d) {
  if (!d || typeof d !== "object" || d.character_limit == null) {
    return { ok: false, configured: true, error: "ElevenLabs sent back something unexpected." };
  }
  const used = Number(d.character_count) || 0;
  const limit = Number(d.character_limit) || 0;
  const reset = Number(d.next_character_count_reset_unix);
  return {
    ok: true,
    configured: true,
    tier: d.tier || "",
    status: d.status || "",
    windows: [{
      id: "characters",
      label: "Characters this period",
      used,
      limit,
      unit: "characters",
      percent: limit > 0 ? clampPct((used / limit) * 100) : 0,
      resets_at: Number.isFinite(reset) && reset > 0 ? new Date(reset * 1000).toISOString() : null,
    }],
  };
}

/** 401 里「缺权限」是最常见的一种，把它翻成一句她能照着做的话 */
export function elevenErrorMessage(status, body) {
  const detail = body && typeof body === "object" ? body.detail : null;
  if (detail?.status === "missing_permissions") {
    return "This ElevenLabs key can't read the subscription. Turn on “User: Read” for the key in ElevenLabs › Developers › API keys.";
  }
  return `ElevenLabs answered HTTP ${status}${detail?.message ? `: ${String(detail.message).slice(0, 120)}` : ""}.`;
}

export async function fetchElevenQuota(env = process.env, fetchImpl = fetch) {
  const key = env.ELEVENLABS_API_KEY || env.ELEVEN_KEY || "";
  if (!key) return { ok: false, configured: false, error: "No ElevenLabs key on the server." };
  const r = await fetchImpl(`${env.ELEVENLABS_TTS_BASE || "https://api.elevenlabs.io"}/v1/user/subscription`, {
    headers: { "xi-api-key": key },
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  const body = await r.json().catch(() => null);
  if (!r.ok) return { ok: false, configured: true, error: elevenErrorMessage(r.status, body) };
  return parseElevenSubscription(body);
}

/* ------------------------------------------------------------ 智谱 GLM */

/** unit 3 = 小时、6 = 周（5 小时窗口 / 每周窗口，对上了她那天的两条重置时间）；认不得的单位就不编 */
function zhipuWindowLabel(unit, number) {
  const n = Number(number) || 1;
  if (unit === 3) return n === 1 ? "Hourly" : `${n}-hour window`;
  if (unit === 6) return n === 1 ? "Weekly" : `${n}-week window`;
  if (unit === 5) return n === 1 ? "Daily" : `${n}-day window`;
  return "Quota window";
}

const ZHIPU_UNITS = { CREDIT_LIMIT: "credits", TOKENS_LIMIT: "tokens", TIME_LIMIT: "calls" };

export function parseZhipuQuota(d) {
  const limits = d?.data?.limits;
  if (!Array.isArray(limits)) {
    return { ok: false, configured: true, error: d?.msg ? `GLM said: ${String(d.msg).slice(0, 120)}` : "GLM sent back something unexpected." };
  }
  // 按形状认：既有总量（usage）又有已用（currentValue）才算一个窗口
  const windows = limits
    .filter((x) => x && typeof x === "object" && x.usage != null && x.currentValue != null)
    .map((x, i) => {
      const limit = Number(x.usage) || 0;
      const used = Number(x.currentValue) || 0;
      const reset = Number(x.nextResetTime);
      return {
        id: `${x.type || "limit"}-${x.unit ?? ""}-${x.number ?? i}`,
        label: x.type === "TIME_LIMIT" ? "Tool calls" : zhipuWindowLabel(x.unit, x.number),
        used,
        limit,
        unit: ZHIPU_UNITS[x.type] || "units",
        percent: x.percentage != null ? clampPct(x.percentage) : (limit > 0 ? clampPct((used / limit) * 100) : 0),
        resets_at: Number.isFinite(reset) && reset > 0 ? new Date(reset).toISOString() : null,
      };
    });
  if (!windows.length) {
    // 空窗口 ≠ 用了 0%：团队版缺请求头时就是这个样子（社区报过），不能画成一条空的进度条
    return { ok: false, configured: true, error: "GLM returned no quota windows — this key may not be on a Coding Plan." };
  }
  return { ok: true, configured: true, tier: d.data.level || "", status: "", windows };
}

export function parseZhipuAccount(d) {
  const a = d?.data;
  if (!a || typeof a !== "object" || a.availableBalance == null) {
    return { ok: false, error: d?.msg ? `GLM said: ${String(d.msg).slice(0, 120)}` : "GLM sent back something unexpected." };
  }
  const n = (v) => (v == null ? null : Number(v));
  return {
    ok: true,
    balance: {
      amount: n(a.availableBalance),
      currency: "CNY",
      spent_total: n(a.totalSpendAmount),
      recharged: n(a.rechargeAmount),
      gifted: n(a.giveAmount),
      frozen: n(a.frozenBalance),
    },
  };
}

export async function fetchZhipuAccount(env = process.env, fetchImpl = fetch) {
  const key = env.ZHIPU_API_KEY || "";
  const r = await fetchImpl("https://open.bigmodel.cn/api/biz/account/query-customer-account-report", {
    headers: { Authorization: key, "Accept-Language": "zh-CN,zh" },
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  const body = await r.json().catch(() => null);
  if (!r.ok) return { ok: false, error: `GLM answered HTTP ${r.status}.` };
  return parseZhipuAccount(body);
}

/** 账户余额 + Coding Plan 额度窗口，各取各的：没开套餐的账号窗口读不到是正常的，不能因此丢掉余额 */
export async function fetchZhipu(env = process.env, fetchImpl = fetch) {
  if (!(env.ZHIPU_API_KEY || "")) {
    return { ok: false, configured: false, error: "No GLM key on the bridge — add ZHIPU_API_KEY to the bridge environment." };
  }
  const [q, b] = await Promise.all([
    fetchZhipuQuota(env, fetchImpl).catch((e) => ({ ok: false, configured: true, error: `Couldn't reach GLM: ${e?.message || "failed"}.` })),
    fetchZhipuAccount(env, fetchImpl).catch((e) => ({ ok: false, error: `Couldn't reach GLM: ${e?.message || "failed"}.` })),
  ]);
  if (!q.ok && !b.ok) return { ok: false, configured: true, error: q.error || b.error };
  return {
    ok: true,
    configured: true,
    tier: q.ok ? q.tier : "",
    status: "",
    windows: q.ok ? q.windows : [],
    balance: b.ok ? b.balance : null,
  };
}

export async function fetchZhipuQuota(env = process.env, fetchImpl = fetch) {
  const key = env.ZHIPU_API_KEY || "";
  if (!key) return { ok: false, configured: false, error: "No GLM key on the bridge — add ZHIPU_API_KEY to the bridge environment." };
  const r = await fetchImpl("https://open.bigmodel.cn/api/monitor/usage/quota/limit", {
    headers: { Authorization: key, "Accept-Language": "zh-CN,zh", "Content-Type": "application/json" },
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  const body = await r.json().catch(() => null);
  if (!r.ok) return { ok: false, configured: true, error: `GLM answered HTTP ${r.status}.` };
  return parseZhipuQuota(body);
}

/* ------------------------------------------------------------ 豆包（火山引擎） */

export function volcConfigured(env = process.env) {
  return !!(env.VOLC_ACCESS_KEY_ID && env.VOLC_SECRET_ACCESS_KEY);
}

/* ------------------------------------------------------------ 汇总 */

/** 每家各自 try：挂了只影响它自己。失败要留痕（docs/LOGGING.md） */
export async function collectProviderInfo(env = process.env, fetchImpl = fetch, log = console) {
  const one = async (name, fn) => {
    try {
      return await fn();
    } catch (e) {
      log.warn?.(`[ProviderInfo] ${name} 失败:`, e?.message || e);
      return { ok: false, configured: true, error: `Couldn't reach ${name}: ${e?.name === "TimeoutError" ? "timed out" : e?.message || "failed"}.` };
    }
  };
  const [elevenlabs, zhipu] = await Promise.all([
    one("ElevenLabs", () => fetchElevenQuota(env, fetchImpl)),
    one("GLM", () => fetchZhipu(env, fetchImpl)),
  ]);
  const doubao = volcConfigured(env)
    ? { ok: false, configured: true, error: "Doubao balance isn't wired yet." }
    : {
      ok: false,
      configured: false,
      error: "Doubao's balance lives in the Volcengine billing console and needs an account access key (billing read). The speech App ID and token can't read it.",
    };
  return { ok: true, at: new Date().toISOString(), providers: { elevenlabs, zhipu, doubao } };
}
