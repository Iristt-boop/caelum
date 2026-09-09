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

import { CHAINS, pickChain } from "../lib/tts-chain.js";

const serverSrc = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "server.js"), "utf8");

describe("TTS 引擎顺序", () => {
  test("🔴 不传 profile = 手机那套，不许被新厂商顶掉", () => {
    // 语音条（Chat.jsx 的播放按钮）从来不传这个字段。
    // 默认必须是「和今天之前一模一样」—— 她的声音不能因为我接了个新厂商就变
    assert.equal(pickChain()[0], "eleven-v3");
    assert.equal(pickChain(undefined, undefined)[0], "eleven-v3");
    assert.equal(pickChain("")[0], "eleven-v3");
  });

  test("🔴 阿里不许出现在任何一条链上", () => {
    // 糖糖没听过那个音色就被换掉过一次。要再接任何一家新的，
    // 顺序是**先让她听 → 她点头 → 再改 CHAINS → 最后才改这条测试**。
    // 反过来先改测试，就等于自己把守门的人辞了
    for (const [name, chain] of Object.entries(CHAINS)) {
      assert.ok(!chain.includes("qwen"), `${name} 链上不该有 qwen`);
    }
    assert.equal(pickChain("desktop")[0], "eleven-v3");
    assert.equal(pickChain("phone")[0], "eleven-v3");
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

  test("认不出的端名落回手机，不是空链", () => {
    //: 空链的话下面那个 for 一轮都不跑，直接 502 —— 一句话都不会说
    assert.deepEqual(pickChain("tablet"), CHAINS.phone);
    assert.ok(pickChain("tablet").length > 0);
  });

  test("上一句降过级，就从那一档接着往下 —— 一段话里不来回换声音", () => {
    assert.deepEqual(pickChain("desktop", "eleven-turbo"), ["eleven-turbo", "edge"]);
    assert.deepEqual(pickChain("phone", "edge"), ["edge"]);
  });

  test("上一句用的就是这一端的首选，那还是走整条", () => {
    //: 不能因为「它在第 0 位」就把链子砍成只剩它自己 —— 它失败了还得有得降
    assert.deepEqual(pickChain("desktop", "eleven-v3"), CHAINS.desktop);
    assert.deepEqual(pickChain("phone", "eleven-v3"), CHAINS.phone);
  });

  test("认不出的 engine 当没传", () => {
    assert.deepEqual(pickChain("phone", "whisper"), CHAINS.phone);
  });

  test("每条链都以 edge 收尾 —— 兜底不能缺", () => {
    for (const [name, chain] of Object.entries(CHAINS)) {
      assert.equal(chain.at(-1), "edge", `${name} 的最后一档该是 edge`);
    }
  });
});
