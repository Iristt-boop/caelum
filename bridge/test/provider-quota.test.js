/**
 * 厂商额度读数（Models 页）。夹具里的 GLM 响应是 2026-10-09 从线上实测抄下来的原样，
 * 不是照文档编的 —— 文档里写的是 TOKENS_LIMIT，真接口回的是 CREDIT_LIMIT。
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  collectProviderInfo, elevenErrorMessage, fetchElevenQuota, fetchZhipu, fetchZhipuQuota,
  parseElevenSubscription, parseZhipuAccount, parseZhipuQuota,
} from "../lib/provider-quota.js";

const GLM_REAL = {
  code: 200, msg: "操作成功", success: true,
  data: {
    level: "lite",
    limits: [
      { type: "CREDIT_LIMIT", unit: 3, number: 5, usage: 2000, currentValue: 902, remaining: 1097, percentage: 45, nextResetTime: 1791525977725 },
      { type: "CREDIT_LIMIT", unit: 6, number: 1, usage: 10000, currentValue: 9819, remaining: 180, percentage: 98, nextResetTime: 1791716082964 },
    ],
  },
};

/** 2026-10-09 实测的账户余额响应，原样 */
const GLM_ACCOUNT = {
  code: 200, msg: "操作成功", success: true,
  data: { balance: 86.912993840, rechargeAmount: 350.0, giveAmount: 6.276707, totalSpendAmount: 269.363713160, todaySpendAmount: null, availableBalance: 86.912993840, frozenBalance: 0, creditBalance: null, creditStatus: "NOT_OPEN" },
};

const jsonRes = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

describe("智谱 GLM", () => {
  test("真实响应：5 小时窗口和每周窗口，用量与重置时间都认得出来", () => {
    const r = parseZhipuQuota(GLM_REAL);
    assert.equal(r.ok, true);
    assert.equal(r.tier, "lite");
    assert.equal(r.windows.length, 2);
    assert.deepEqual(
      [r.windows[0].label, r.windows[0].used, r.windows[0].limit, r.windows[0].percent],
      ["5-hour window", 902, 2000, 45]);
    assert.deepEqual(
      [r.windows[1].label, r.windows[1].used, r.windows[1].limit, r.windows[1].percent, r.windows[1].unit],
      ["Weekly", 9819, 10000, 98, "credits"]);
    assert.equal(r.windows[1].resets_at, new Date(1791716082964).toISOString());
  });

  test("🔴 空窗口不是「用了 0%」：如实报读不出来", () => {
    const r = parseZhipuQuota({ code: 200, data: { limits: [], level: "lite" } });
    assert.equal(r.ok, false);
    assert.match(r.error, /no quota windows/);
  });

  test("认窗口按形状：缺总量或已用的条目不算窗口，不画成 0%", () => {
    const d = { data: { level: "lite", limits: [{ type: "TIME_LIMIT", unit: 5, number: 1 }, GLM_REAL.data.limits[0]] } };
    const r = parseZhipuQuota(d);
    assert.equal(r.windows.length, 1);
    assert.equal(r.windows[0].used, 902);
  });

  test("形状变了（没有 limits）：报错带上对方的话，不抛", () => {
    const r = parseZhipuQuota({ code: 1001, msg: "Header中未收到Authorization参数" });
    assert.equal(r.ok, false);
    assert.match(r.error, /Authorization/);
  });

  test("没配 key：说明去哪里配，并且不发请求", async () => {
    let called = false;
    const r = await fetchZhipuQuota({}, async () => { called = true; });
    assert.equal(r.configured, false);
    assert.match(r.error, /ZHIPU_API_KEY/);
    assert.equal(called, false);
  });

  test("发请求时 key 只放在 Authorization 头里，不进 URL", async () => {
    let seen;
    await fetchZhipuQuota({ ZHIPU_API_KEY: "sekret" }, async (url, init) => { seen = { url, init }; return jsonRes(200, GLM_REAL); });
    assert.equal(seen.init.headers.Authorization, "sekret");
    assert.ok(!String(seen.url).includes("sekret"));
  });
});

describe("智谱 GLM 账户余额", () => {
  test("真实响应 → 可用余额 / 累计充值 / 赠送 / 累计消费", () => {
    const r = parseZhipuAccount(GLM_ACCOUNT);
    assert.equal(r.ok, true);
    assert.ok(Math.abs(r.balance.amount - 86.91) < 0.01);
    assert.equal(r.balance.currency, "CNY");
    assert.equal(r.balance.recharged, 350);
    assert.ok(Math.abs(r.balance.spent_total - 269.36) < 0.01);
  });

  test("形状不对不当成 ¥0：没有 availableBalance 就是读不出来", () => {
    assert.equal(parseZhipuAccount({ code: 200, data: {} }).ok, false);
    assert.equal(parseZhipuAccount({ code: 1001, msg: "没有权限" }).ok, false);
  });

  test("余额和额度窗口各取各的：窗口读不到时，余额照样给", async () => {
    const f = async (url) => (String(url).includes("query-customer-account-report")
      ? jsonRes(200, GLM_ACCOUNT)
      : jsonRes(200, { code: 200, data: { limits: [], level: "" } }));
    const r = await fetchZhipu({ ZHIPU_API_KEY: "k" }, f);
    assert.equal(r.ok, true);
    assert.deepEqual(r.windows, []);
    assert.ok(r.balance.amount > 86);
  });

  test("余额读不到时，窗口照样给；两样都读不到才算失败", async () => {
    const onlyQuota = async (url) => (String(url).includes("query-customer-account-report") ? jsonRes(500, {}) : jsonRes(200, GLM_REAL));
    const a = await fetchZhipu({ ZHIPU_API_KEY: "k" }, onlyQuota);
    assert.equal(a.ok, true);
    assert.equal(a.balance, null);
    assert.equal(a.windows.length, 2);
    const none = async () => jsonRes(500, {});
    assert.equal((await fetchZhipu({ ZHIPU_API_KEY: "k" }, none)).ok, false);
  });

  test("余额请求的 key 只在 Authorization 头里", async () => {
    const seen = [];
    await fetchZhipu({ ZHIPU_API_KEY: "sekret" }, async (url, init) => { seen.push({ url: String(url), init }); return jsonRes(200, GLM_ACCOUNT); });
    assert.equal(seen.length, 2);
    for (const x of seen) {
      assert.equal(x.init.headers.Authorization, "sekret");
      assert.ok(!x.url.includes("sekret"));
    }
  });
});

describe("ElevenLabs", () => {
  const SUB = { tier: "creator", status: "active", character_count: 30000, character_limit: 100000, next_character_count_reset_unix: 1792000000 };

  test("订阅响应 → 一个字符窗口", () => {
    const r = parseElevenSubscription(SUB);
    assert.equal(r.ok, true);
    assert.equal(r.tier, "creator");
    assert.deepEqual([r.windows[0].used, r.windows[0].limit, r.windows[0].percent], [30000, 100000, 30]);
    assert.equal(r.windows[0].resets_at, new Date(1792000000 * 1000).toISOString());
  });

  test("🔴 实测的 401（key 缺 user_read）翻成她能照着做的一句话", async () => {
    const body = { detail: { type: "authentication_error", code: "unauthorized", status: "missing_permissions", message: "The API key you used is missing the permission user_read" } };
    const r = await fetchElevenQuota({ ELEVENLABS_API_KEY: "k" }, async () => jsonRes(401, body));
    assert.equal(r.ok, false);
    assert.match(r.error, /User: Read/);
  });

  test("别的错误带状态码", () => {
    assert.match(elevenErrorMessage(500, null), /HTTP 500/);
  });

  test("key 在 xi-api-key 头里", async () => {
    let seen;
    await fetchElevenQuota({ ELEVENLABS_API_KEY: "abc" }, async (url, init) => { seen = { url, init }; return jsonRes(200, SUB); });
    assert.equal(seen.init.headers["xi-api-key"], "abc");
    assert.match(seen.url, /\/v1\/user\/subscription$/);
  });
});

describe("汇总", () => {
  test("一家抛异常不拖累另一家，且被留痕", async () => {
    const logged = [];
    const fetchImpl = async (url) => {
      if (String(url).includes("elevenlabs")) throw new Error("socket hang up");
      return jsonRes(200, GLM_REAL);
    };
    const r = await collectProviderInfo({ ELEVENLABS_API_KEY: "k", ZHIPU_API_KEY: "z" }, fetchImpl, { warn: (...a) => logged.push(a.join(" ")) });
    assert.equal(r.providers.zhipu.ok, true);
    assert.equal(r.providers.elevenlabs.ok, false);
    assert.match(r.providers.elevenlabs.error, /socket hang up/);
    assert.equal(logged.length, 1);
  });

  test("汇总里的 GLM 同时带窗口和余额", async () => {
    const f = async (url) => (String(url).includes("query-customer-account-report") ? jsonRes(200, GLM_ACCOUNT) : jsonRes(200, GLM_REAL));
    const r = await collectProviderInfo({ ZHIPU_API_KEY: "k" }, f, { warn() {} });
    assert.equal(r.providers.zhipu.windows.length, 2);
    assert.ok(r.providers.zhipu.balance.amount > 86);
  });

  test("豆包没配账号级密钥：说清楚为什么拿不到，而不是装作没有这回事", async () => {
    const r = await collectProviderInfo({}, async () => jsonRes(200, {}), { warn() {} });
    assert.equal(r.providers.doubao.configured, false);
    assert.match(r.providers.doubao.error, /access key/);
  });
});

describe("接线", () => {
  test("server.js 真的挂了 /api/provider-info，并且是从 lib 里 import 的", async () => {
    const { readFileSync } = await import("node:fs");
    const { fileURLToPath } = await import("node:url");
    const { dirname, join } = await import("node:path");
    const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "server.js"), "utf8");
    assert.match(src, /app\.get\("\/api\/provider-info"/);
    assert.match(src, /import \{ collectProviderInfo \} from "\.\/lib\/provider-quota\.js"/);
  });
});
