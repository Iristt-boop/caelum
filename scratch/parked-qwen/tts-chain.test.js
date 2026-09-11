/**
 * TTS 引擎顺序（2026-09-07）。
 *
 * 这几条守的是一个**已经发生过**的错：加新厂商的时候把它写成了全局默认，
 * 结果手机端和聊天里的语音条一起被切走了。糖糖问「手机端也降级成 qwen3 了？」
 * 我才发现 —— 当时没有任何东西在守这件事。
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { CHAINS, pickChain } from "../lib/tts-chain.js";

describe("TTS 引擎顺序", () => {
  test("🔴 不传 profile = 手机那套，不许被新厂商顶掉", () => {
    // 语音条（Chat.jsx 的播放按钮）从来不传这个字段。
    // 默认必须是「和今天之前一模一样」—— 她的声音不能因为我接了个新厂商就变
    assert.equal(pickChain()[0], "eleven-v3");
    assert.equal(pickChain(undefined, undefined)[0], "eleven-v3");
    assert.equal(pickChain("")[0], "eleven-v3");
  });

  test("桌面打头的是阿里 —— 她要的就是这个", () => {
    assert.equal(pickChain("desktop")[0], "qwen");
  });

  test("认不出的端名落回手机，不是空链", () => {
    //: 空链的话下面那个 for 一轮都不跑，直接 502 —— 一句话都不会说
    assert.deepEqual(pickChain("tablet"), CHAINS.phone);
    assert.ok(pickChain("tablet").length > 0);
  });

  test("上一句降过级，就从那一档接着往下 —— 一段话里不来回换声音", () => {
    assert.deepEqual(pickChain("desktop", "eleven-turbo"), ["eleven-turbo", "edge"]);
    assert.deepEqual(pickChain("phone", "qwen"), ["qwen", "edge"]);
  });

  test("上一句用的就是这一端的首选，那还是走整条", () => {
    //: 不能因为「它在第 0 位」就把链子砍成只剩它自己 —— 它失败了还得有得降
    assert.deepEqual(pickChain("desktop", "qwen"), CHAINS.desktop);
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
