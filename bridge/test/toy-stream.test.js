/**
 * /api/toy/stream —— 设备状态的 SSE 推送（2026-09-20）。
 *
 * ## 为什么有这条
 * 中继页原来每秒轮询 /api/toy/state，而浏览器把后台页的定时器节流到
 * 一分钟一次：Nox 调了 toy_set，她的设备要等她切回页面才动。
 * 这条流让 bridge 在状态落库的同一刻把新状态推给页面 —— 页面挂在
 * 后台也照收（网络事件不吃定时器节流）。
 *
 * 顺带钉死两条别回退的行为：
 *   1. token 必须过 ensureApiAuth —— EventSource 带不了 header，
 *      所以页面用 ?token=，这条流不许变成匿名可读的广播
 *   2. 连上先补发当前状态 —— 页面重连不丢最后一条指令
 */
import assert from "node:assert/strict";
import test from "node:test";
import { startBridge, api, TOKEN } from "./helpers.js";

/** 把 SSE 响应包成一个 next()：闭包里持有同一个 reader，可以连续读多帧 */
function sseReader(res) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  return async function next(timeoutMs = 5000) {
    while (true) {
      const chunk = await Promise.race([
        reader.read(),
        new Promise((_, rej) => setTimeout(() => rej(new Error("等 SSE 帧超时")), timeoutMs)),
      ]);
      if (chunk.done) throw new Error("流先结束了，没等到下一帧");
      buf += dec.decode(chunk.value, { stream: true });
      const idx = buf.indexOf("data: ");
      if (idx !== -1) {
        const eol = buf.indexOf("\n", idx);
        if (eol === -1) continue;   // 帧还没到行尾，继续读
        const data = JSON.parse(buf.slice(idx + 6, eol));
        buf = buf.slice(eol + 1);   // 消费掉这帧，下次 next() 从后面的帧读
        return data;
      }
    }
  };
}

test("toy stream: 状态落库同一刻推送新状态，连上先补当前状态", async () => {
  const b = await startBridge();
  try {
    const res = await fetch(`${b.base}/api/toy/stream?token=${encodeURIComponent(TOKEN)}`);
    assert.equal(res.status, 200);
    assert.match(res.headers.get("content-type") || "", /text\/event-stream/);
    const next = sseReader(res);

    // 第一帧：连上即补当前状态（全新库是空对象）
    assert.deepEqual(await next(), {});

    const posted = await api(b.base, "/api/toy/set", {
      method: "POST",
      body: { cmd: "set", mode: 1, intensity: 37 },
    });
    assert.equal(posted.status, 200);
    assert.equal(posted.data.ok, true);

    // 推送帧和落库是同一份数据：指令发出去的那一刻页面就该收到
    const pushed = await next();
    assert.equal(pushed.cmd, "set");
    assert.equal(pushed.intensity, 37);
    assert.equal(pushed.mode, 1);
    assert.ok(pushed.updated_at > 0);
  } finally {
    await b.stop();
  }
});

test("toy stream: 没 token 是 403，不是匿名可读的广播", async () => {
  const b = await startBridge();
  try {
    const r = await fetch(`${b.base}/api/toy/stream`);
    assert.equal(r.status, 403);
    // 拿着别的（错误）token 也一样进不来
    const r2 = await fetch(`${b.base}/api/toy/stream?token=wrong-token`);
    assert.equal(r2.status, 403);
  } finally {
    await b.stop();
  }
});

test("toy stream: stop 指令同样推送；同一连接持续可收多帧", async () => {
  const b = await startBridge();
  try {
    const res = await fetch(`${b.base}/api/toy/stream?token=${encodeURIComponent(TOKEN)}`);
    const next = sseReader(res);
    await next(); // 补发帧

    await api(b.base, "/api/toy/set", { method: "POST", body: { cmd: "set", mode: 1, intensity: 20 } });
    const a = await next();
    assert.equal(a.intensity, 20);

    await api(b.base, "/api/toy/set", { method: "POST", body: { cmd: "stop", mode: 0, intensity: 0 } });
    const s = await next();
    assert.equal(s.cmd, "stop");
  } finally {
    await b.stop();
  }
});
