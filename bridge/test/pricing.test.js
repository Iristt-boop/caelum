/**
 * 价格表要跟上模型表（2026-09-08）。
 *
 * ## 🔴 这个洞栽过两次
 *
 * `rowCost()` 认不出型号时落 `DEFAULT_PRICE`（按 Sonnet 估）。
 * 那是个**安静的错**：页面照常显示，只是数字虚高十几到几十倍。
 *
 *   2026-09-06  deepseek-v4-flash-vision-exp 缺条目 → 虚高约 20 倍
 *   2026-09-08  glm-5.3-flash 缺条目          → 虚高约 27 倍，
 *               日志三天里喊了 **8728 次**「未知型号按 Sonnet 估价」而没人看
 *
 * 第一次修的时候在 PRICING 上面写了红字注释，第二次照样漏。
 * **注释拦不住，测试才拦得住** —— 所以有了这个文件。
 *
 * ## 守的是什么
 *
 * `nox-core/config.py` 的 `models` 表里每个能被切到的型号，
 * 在 `bridge/server.js` 的 `PRICING` 里都要有一条。
 * 两边是同一件事的两半：那边决定"能用哪个"，这边决定"怎么算钱"。
 *
 * ⚠️ 不打网络、不起服务 —— 直接读两个源文件的文本。
 * 起 bridge 要连 DB 和一堆下游，为一张常量表不值得。
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const serverSrc = readFileSync(join(here, "..", "server.js"), "utf8");
const configSrc = readFileSync(
  join(here, "..", "..", "nox-core", "config.py"), "utf8");

/** 从 server.js 里把 PRICING 的键抠出来。 */
function pricingKeys() {
  const block = serverSrc.split("const PRICING = {")[1].split("};")[0];
  return new Set([...block.matchAll(/"([^"]+)"\s*:\s*\{/g)].map((m) => m[1]));
}

/** 从 config.py 的 models 表里把型号名抠出来：ModelChoice("型号", "后端", "标签") */
function modelNames() {
  const block = configSrc.split("models: dict[str, ModelChoice]")[1].split("})")[0];
  return [...block.matchAll(/ModelChoice\(\s*\n?\s*"([^"]+)"/g)].map((m) => m[1]);
}

test("每个可切换的型号都要有价格", () => {
  const priced = pricingKeys();
  const missing = modelNames().filter((m) => !priced.has(m));
  assert.deepEqual(
    missing, [],
    `这些型号没有价格，会按 Sonnet 估价（虚高十几到几十倍）：${missing.join(", ")}\n` +
    "在 bridge/server.js 的 PRICING 里补上，别只改 nox-core 的 models。",
  );
});

test("GLM 和 DeepSeek 的价格没有写反", () => {
  const block = serverSrc.split("const PRICING = {")[1].split("};")[0];
  const priceOf = (model) => {
    const m = block.match(
      new RegExp(`"${model}"\\s*:\\s*\\{\\s*miss:\\s*([\\d.]+),\\s*hit:\\s*([\\d.]+),\\s*out:\\s*([\\d.]+)`),
    );
    assert.ok(m, `PRICING 里没有 ${model}`);
    return { miss: +m[1], hit: +m[2], out: +m[3] };
  };
  const glm = priceOf("glm-5.3-flash");
  //: 官方口径 0.8 / 0.23 / 2.8（2026-09-08 核对）。
  //: 写错一位数，Console 上的钱就差一个量级 —— 而它不会报错
  assert.equal(glm.miss, 0.8);
  assert.equal(glm.hit, 0.23);
  assert.equal(glm.out, 2.8);
  //: 🔴 命中价必须比未命中便宜 —— 反了的话「省下的钱」会算成负数
  assert.ok(glm.hit < glm.miss, "缓存命中比未命中还贵？");
});

test("认不出的型号仍然按最贵的估，且会出声", () => {
  //: 宁可高估。低估会让她以为很省，然后某天收到账单 ——
  //: 而且必须 console.warn，否则又是一次「安静地骗人」
  assert.ok(serverSrc.includes("未知型号按 Sonnet 估价"),
    "认不出型号时必须留痕，不然下次还是靠人肉发现");
  const def = serverSrc.match(/const DEFAULT_PRICE = \{\s*miss:\s*([\d.]+)/);
  assert.ok(def && +def[1] >= 20, "默认价该按贵的估，宁可高估不可低估");
});
