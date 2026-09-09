/**
 * 🎬 共影的提示词不该进她的聊天记录（2026-09-09）。
 *
 * 糖糖看到手机 App 的聊天里冒出来这么一条：
 *
 *   「【共影】她暂停在 315.0 秒问你：【当前画面】这是一个静态的全景镜头，
 *     画面主体是一群站在谷仓门口的绵羊……（几百字场景描述）」
 *
 * 她的原话：「这个共影画面记录到我的上下文太奇怪了吧。隐藏一下。
 * 喂到他内部就行了。」
 *
 * 那段是**喂给 Nox 的上下文**，不是她说的话。所以 `/api/chat` 分成两样：
 *
 *   `message`  → 整段转给 Core（他该看见）
 *   `display`  → 落进 conversations 的那一句（她该看见的）
 *
 * ## 三条都得守
 *
 * 1. 传了 `display`：记录里只有它，**整段一个字都不在**
 * 2. `display: ""`：这一轮她没开口（他主动说的弹幕）—— 一条 user 都不落
 * 3. 不传 `display`：老行为不变 —— 别的调用方（Chat 页、小克）不受影响
 *
 * ⚠️ 测试里 Core 指向死端口，转发必然失败；但**落库发生在转发之前**，
 * 所以这三条照样验得到。
 */
import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

let bridge;
const base = () => bridge.base;

before(async () => {
  bridge = await startBridge();
});
after(async () => {
  await bridge?.stop();
});

//: 线上真实那条的形状（截短了，特征词留着）
const COWATCH_PROMPT =
  "【共影】她暂停在 315.0 秒问你：\n" +
  "【当前画面】这是一个静态的全景镜头，画面主体是一群站在谷仓门口、面向镜头的绵羊。\n" +
  "环境：场景设定在一个暗色调的木质谷仓内部……\n\n" +
  "能看到画面不";

/**
 * 打一发 `/api/chat`。SSE 流我们不关心内容 —— 上游是死端口，
 * 读完即可；要验的是它**落库落了什么**。
 */
async function chat(body) {
  const r = await fetch(`${base()}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Nox-Token": TOKEN },
    body: JSON.stringify(body),
  });
  await r.text().catch(() => "");
  return r.status;
}

/** 这个会话落库的消息。 */
async function saved(sid) {
  const r = await api(base(), `/api/messages?sessionId=${encodeURIComponent(sid)}`);
  return Array.isArray(r.data) ? r.data : [];
}

describe("共影提示词不进聊天记录", () => {
  test("🔴 传了 display：记录里只留她那句，整段场景描述不在", async () => {
    const sid = "cw-ask-1";
    await chat({ message: COWATCH_PROMPT, display: "能看到画面不", sessionId: sid });

    const users = (await saved(sid)).filter((m) => m.role === "user");
    assert.equal(users.length, 1, "该正好落一条她的话");
    assert.equal(users[0].content, "能看到画面不");
    //: 最要紧的一条 —— 那几百字不许出现在她能看到的地方
    assert.ok(!users[0].content.includes("【当前画面】"), "场景描述漏进聊天记录了");
    assert.ok(!users[0].content.includes("绵羊"), "场景描述漏进聊天记录了");
  });

  test("🔴 display 是空串：他主动说的那种，一条 user 都不落", async () => {
    const sid = "cw-proactive-1";
    await chat({ message: "【共影·主动】她在看一部本地的片子……", display: "", sessionId: sid });

    const users = (await saved(sid)).filter((m) => m.role === "user");
    assert.equal(users.length, 0, "她没开口，却落了一条她的话");
  });

  test("不传 display：老行为不变，别的调用方不受影响", async () => {
    const sid = "cw-plain-1";
    await chat({ message: "今天吃了两个包子", sessionId: sid });

    const users = (await saved(sid)).filter((m) => m.role === "user");
    assert.equal(users.length, 1);
    assert.equal(users[0].content, "今天吃了两个包子");
  });

  test("display 只影响落库，不影响发给 Core 的内容", async () => {
    //: 这条守的是「别为了让她看着干净，把他也一起蒙了」——
    //: 400 校验看的仍然是 message，空 message 才该被拒
    const status = await chat({ message: "", display: "她那句", sessionId: "cw-empty-1" });
    assert.equal(status, 400, "message 空了还放行的话，等于允许发一条他看不到内容的消息");
  });
});
