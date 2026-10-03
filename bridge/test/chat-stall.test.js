/**
 * 上游静默卡死 → bridge 必须自己收口（2026-09-19 卡死事故，二版修复）。
 *
 * ## 现场
 *
 * 17:55:07 工具执行完 → 17:55:14 她那页断开。之后一晚又复现两次（19:07 开风扇、
 * 19:57「111」），表现都是「工具卡长出来了，然后一直转圈，发什么都发不出去」。
 *
 * ## 这条测试守的是什么
 *
 * 判死只能由**离上游最近**的这一层做 —— 前端的眼睛会被心跳喂活：上游越静默，
 * bridge 的 `: ka` 越密，前端那个"多久没字节"的计时器就越不会到期。所以：
 *
 *   1. 工具帧照常透传（工具卡该长就长）
 *   2. 静默期有 `: ka` 心跳（它保的是**链路**，不是判活）
 *   3. 静默超过 slow 阈值 → 补 `{"type":"slow"}` —— 真事件，让她看得见"还在等"
 *   4. 静默超过 stall 阈值 → 掐上游、补 `{"type":"error"}` + `{"type":"done"}`
 *      —— **流一定收口**，前端 loading 一定落得下来
 *   5. 上游正常流完的路径不许误报（阈值压到毫秒级都不许）
 *
 * ⚠️ 阈值靠 NOX_BRIDGE_{KA,SLOW,STALL}_MS 压成毫秒级才验得动 —— 线上是
 * 5s / 45s / 180s。这里不压的话每条测试要跑三分钟。
 *
 * ⚠️ 假上游按 `text` 分流，所以**只需要一个 bridge 进程**：
 *   「卡死」 → 工具帧之后一个字都不吐（复刻现场）
 *   别的     → 工具帧 + 文字 + done（正常一轮）
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, describe, test } from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

/** 假 Core：SSE 形状和 core 那边一致（data: 分帧、空行收尾） */
async function fakeCore(onChat) {
  const srv = http.createServer((req, res) => {
    if (!req.url.startsWith("/chat/stream")) {
      res.statusCode = 404;
      res.end();
      return;
    }
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      });
      const frame = (o) => res.write(`data: ${JSON.stringify(o)}\n\n`);
      onChat(JSON.parse(body || "{}"), frame, res);
    });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  return {
    url: `http://127.0.0.1:${srv.address().port}`,
    close: () => new Promise((r) => srv.close(r)),
  };
}

let bridge;
let core;

before(async () => {
  core = await fakeCore((req, frame, res) => {
    // 每一轮都先「调工具」——她要的那张卡就是这么长出来的
    frame({ type: "tool_start", tool: "ha_control", args: { entity: "风扇" } });
    frame({
      type: "tool_end", tool: "ha_control", ok: true,
      summary: "风扇已开", duration_ms: 1200,
    });

    if (String(req.text || "").includes("卡死")) {
      // 🔴 现场复刻：工具跑完 → 下一段文字之前**一个字都不吐**，也不关连接
      //（bigmodel 那晚就是这样挂 8 秒到几分钟的）
      return;
    }
    // 「他在想」800ms —— 比 slow 阈值(400ms)长、比 stall 阈值(2000ms)短。
    // 这一段就是拿来验「慢 ≠ 死」的：slow 帧可以发，掐断不行。
    setTimeout(() => {
      frame({ type: "text", text: "风扇开好了。" });
      frame({
        type: "done", session_id: req.session_id || "ok-sid", ok: true,
        outcome: "answered", iterations: 2, input_tokens: 10, output_tokens: 5,
        model: "test-model", tools_used: ["ha_control"],
      });
      res.end();
    }, 800);
  });

  bridge = await startBridge({
    NOX_CORE_URL: core.url,
    NOX_BRIDGE_KA_MS: "200",
    NOX_BRIDGE_SLOW_MS: "400",
    NOX_BRIDGE_STALL_MS: "2000",
  });
});

after(async () => {
  await bridge?.stop();
  await core?.close();
});

/**
 * 打一发 /api/chat 把整条流读干净。
 * ⚠️ 流不收口的话这里就一直等 —— 测试超时本身就是断言的一部分。
 */
async function chat(text, sessionId) {
  const r = await fetch(`${bridge.base}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Nox-Token": TOKEN },
    body: JSON.stringify({ message: text, sessionId }),
  });
  return r.text();
}

describe("上游静默卡死（2026-09-19 事故）", () => {
  test("工具卡之后上游不出声：慢提示 → error → done，流一定收口", async () => {
    const text = await chat("（卡死用例）开风扇", "stall-1");

    assert.ok(text.includes('"type":"tool_end"'), "工具调用那两帧应该照常透传（她的工具卡靠它长）");
    assert.ok(text.includes(": ka"), "静默期必须有 SSE 心跳 —— 它保的是链路，不是判活");
    assert.ok(text.includes('"type":"slow"'),
      "静默超过 slow 阈值要有 slow 帧：让她看得见「还在等」，而不是无信息量的转圈");
    assert.ok(text.includes("卡在上游"), "判定卡死之后必须落一句人话给她");
    assert.ok(text.includes('"type":"done"'),
      "必须有终态 done —— 前端 loading 落不落得下来全看它");

    const errAt = text.indexOf('"type":"error"');
    const doneAt = text.indexOf('"type":"done"');
    assert.ok(errAt >= 0, "缺 error 帧");
    assert.ok(doneAt > errAt, "顺序必须是先 error 再 done（done 是解锁信号，不能没有）");
  });

  test("卡死那句人话要落库 —— 不然一刷新就没了", async () => {
    const rows = await api(bridge.base, "/api/messages?sessionId=stall-1");
    const assistant = (Array.isArray(rows.data) ? rows.data : []).filter((m) => m.role === "assistant");
    assert.ok(assistant.length >= 1, "这一轮一条 assistant 都没落");
    assert.ok(
      assistant.some((m) => String(m.content || "").includes("卡在上游")),
      "落库的应该是她屏幕上真看到的那句话"
    );
  });

  test("上游正常流完：阈值压到毫秒级也不许误报卡死", async () => {
    // 假上游延迟 800ms 才吐下一帧（> slow 400ms、< stall 2000ms）——
    // 这模拟的是「他在想」，不是「他死了」。slow 帧可以发，掐断绝对不行。
    const text = await chat("正常一轮", "ok-1");

    assert.ok(text.includes("风扇开好了"), "正文要照常送到");
    assert.ok(text.includes('"type":"done"'), "正常收尾");
    assert.ok(!text.includes("卡在上游"), "上游只是慢，不许判死 —— 否则会把好好的一轮掐断");
    assert.ok(!text.includes('"type":"error"'), "正常一轮不该出现 error 帧");
  });
});
