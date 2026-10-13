/**
 * TTS 引擎顺序（2026-09-07 定，09-09 补「阿里不许回来」那条）。
 *
 * 这几条守的是一个**已经发生过**的错：加新厂商的时候把它写成了全局默认，
 * 结果手机端和聊天里的语音条一起被切走了。糖糖问「手机端也降级成 qwen3 了？」
 * 我才发现 —— 当时没有任何东西在守这件事。
 *
 * 09-09 她说明白了：「我没听过阿里的音色，很不好听」「我还没决定用什么 tts
 * 那个会话就直接用了阿里」。于是阿里那套整个删掉，**并且由测试守着别回来** ——
 * 上一次只是把它从链上摘掉、代码留着「一行就能开」，那条后路本身就是问题。
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { CHAINS, ELEVEN_ENGINES, pickChain } from "../lib/tts-chain.js";

const serverSrc = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "server.js"), "utf8");

describe("TTS 引擎顺序", () => {
  test("🔴 不传 profile = 手机那套，不许被新厂商顶掉", () => {
    // 语音条（Chat.jsx 的播放按钮）从来不传这个字段。
    // 🔴 2026-10-07 修订：v4 上线（她指定 PWA 通话换 v4），链首从 v3 换成 v4，
    //   v3 降为第二档 —— 这次是**她点名**的（顺序：她听 → 她点头 → 再改链）。
    //   守的门没变：新厂商进链首，只能走「她点头」这条路。
    assert.equal(pickChain()[0], "eleven-v4");
    assert.equal(pickChain(undefined, undefined)[0], "eleven-v4");
    assert.equal(pickChain("")[0], "eleven-v4");
  });

  test("🔴 阿里不许出现在任何一条链上", () => {
    // 糖糖没听过那个音色就被换掉过一次。要再接任何一家新的，
    // 顺序是**先让她听 → 她点头 → 再改 CHAINS → 最后才改这条测试**。
    // 反过来先改测试，就等于自己把守门的人辞了
    for (const [name, chain] of Object.entries(CHAINS)) {
      assert.ok(!chain.includes("qwen"), `${name} 链上不该有 qwen`);
    }
    assert.equal(pickChain("desktop")[0], "eleven-v4");          // 老的端名照样认成 call
    assert.equal(pickChain("phone")[0], "eleven-v4");
    assert.equal(pickChain("live")[0], "doubao");               // P4（10-13）：Live 默认豆包打头
    // v3 还在第二档：v4 出问题自动落回她听了两个月的那个声音
    assert.equal(pickChain("phone")[1], "eleven-v3");
  });

  test("🔴 server.js 里也不许留着阿里 TTS 的代码", () => {
    //: 光把它从链上摘掉不够 —— 09-07 就是这么做的，代码全留着「一行就能开回来」，
    //: 于是它在仓库里躺着等一个没人问过她的开关。这条盯的是**代码本身没了**。
    //: ⚠️ 认的是标识符不是「qwen」这个词：`DASHSCOPE_API_KEY` 还给语音识别用着，
    //: `qwen3.5-flash` 是他的眼睛，两个都不在这次范围里
    for (const dead of ["qwenTts", "QWEN_TTS_", "tryQwen", 'sendEngine("qwen")']) {
      assert.ok(!serverSrc.includes(dead),
        `server.js 里还留着 ${dead} —— 阿里 TTS 该是整套删掉的`);
    }
  });

  test("认不出的端名落回 call，不是空链", () => {
    //: 空链的话下面那个 for 一轮都不跑，直接 502 —— 一句话都不会说
    assert.deepEqual(pickChain("tablet"), CHAINS.call);
    assert.ok(pickChain("tablet").length > 0);
  });

  test("上一句降过级，就从那一档接着往下 —— 一段话里不来回换声音", () => {
    assert.deepEqual(pickChain("desktop", "eleven-turbo"), ["eleven-turbo", "doubao", "edge"]);
    assert.deepEqual(pickChain("phone", "edge"), ["edge"]);
    assert.deepEqual(pickChain("phone", "eleven-v3"), ["eleven-v3", "eleven-turbo", "doubao", "edge"]);
    assert.deepEqual(pickChain("phone", "doubao"), ["doubao", "edge"]);
  });

  test("🔴 Call 默认：ElevenLabs 全挂了先落豆包（复刻声音），不是直接掉 edge —— 她 10-10 点名的", () => {
    //: 搬到 N150 后 ElevenLabs 经东京节点出口，节点一断整家都连不上。
    //: 她点名用 OS 实时通话里一直在听的那个豆包 clone；顺序还是「她听过 → 她点头 → 改链 → 改测试」
    //: （Live 默认豆包打头，不在这条规矩里；她在 Models 页改过的也不在 —— 那是她的耳朵定的）
    const chain = CHAINS.call;
    const d = chain.indexOf("doubao");
    assert.ok(d > chain.lastIndexOf("eleven-turbo"), "call：豆包要排在所有 ElevenLabs 之后");
    assert.equal(chain[d + 1], "edge", "call：豆包后面紧跟 edge 兜底");
  });

  test("Live 默认和 10-13 之前一样：豆包流式打头，失败了按老的 ElevenLabs 那几档接着来", () => {
    assert.deepEqual(CHAINS.live, ["doubao", "eleven-v4", "eleven-v3", "eleven-turbo", "edge"]);
    assert.deepEqual(pickChain("live", null, { after: "doubao" }), ["eleven-v4", "eleven-v3", "eleven-turbo", "edge"]);
  });

  test("网络层失败跳过整家 ElevenLabs 用的名单，和链上的 ElevenLabs 档位对得上", () => {
    for (const chain of Object.values(CHAINS)) {
      assert.deepEqual(chain.filter((e) => e.startsWith("eleven-")), ELEVEN_ENGINES);
    }
  });

  test("上一句用的就是这一端的首选，那还是走整条", () => {
    //: 不能因为「它在第 0 位」就把链子砍成只剩它自己 —— 它失败了还得有得降
    assert.deepEqual(pickChain("desktop", "eleven-v4"), CHAINS.call);
    assert.deepEqual(pickChain("phone", "eleven-v4"), CHAINS.call);
  });

  test("认不出的 engine 当没传", () => {
    assert.deepEqual(pickChain("phone", "whisper"), CHAINS.call);
  });

  test("每条链都以 edge 收尾 —— 兜底不能缺", () => {
    for (const [name, chain] of Object.entries(CHAINS)) {
      assert.equal(chain.at(-1), "edge", `${name} 的最后一档该是 edge`);
    }
  });
});
