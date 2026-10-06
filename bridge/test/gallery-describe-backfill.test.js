/**
 * 没描述的相册图要补上（2026-10-06）。
 *
 * 「想逗她」会从她收藏的照片里想起旧事，只认带描述的。上线那天她收藏的 2 张全空：
 * 7 月的图早于自动识图，后来也有识图超时的 —— 失败一次就永远空着。
 *   ① 收藏一张没描述的 → 当场去识
 *   ② 后台隔一阵补几张没描述的，收藏的先补；同一张连挂 3 次就不再撞
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, describe, test } from "node:test";

import { api, startBridge } from "./helpers.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fakeCore() {
  const calls = [];
  let ready = false;
  const srv = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      const url = String(JSON.parse(body || "{}").url || "");
      const name = (url.match(/\/uploads\/([^?]+)/) || [])[1] || "?";
      calls.push(name);
      if (name.startsWith("bad")) { res.writeHead(500); res.end("boom"); return; }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(ready ? { description: `描述:${name}` } : {}));
    });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  return {
    url: `http://127.0.0.1:${srv.address().port}`, calls,
    setReady: (v) => { ready = v; },
    close: () => new Promise((r) => srv.close(r)),
  };
}

async function save(base, name) {
  await api(base, "/api/gallery/save", { method: "POST", body: { imageUrl: `/uploads/${name}`, album: "聊天" } });
}
async function list(base) {
  const r = await api(base, "/api/gallery/list?filter=all");
  return Object.fromEntries(r.data.map((g) => [String(g.url).replace(/^\/uploads\/|\?.*$/g, ""), g]));
}

describe("收藏时当场识图", () => {
  let core; let bridge;
  before(async () => {
    core = await fakeCore();
    bridge = await startBridge({ NOX_CORE_URL: core.url, NOX_GALLERY_BACKFILL_MS: "0" });   // 后台补的关掉，只看收藏那一下
  });
  after(async () => { await bridge?.stop(); await core?.close(); });

  test("没描述的图一收藏就去识；已经有描述的不重识", async () => {
    await save(bridge.base, "cat.jpg");           // 存的时候识图没给描述（ready=false）
    await sleep(200);
    core.setReady(true);
    const id = (await list(bridge.base))["cat.jpg"].id;
    const before = core.calls.filter((n) => n === "cat.jpg").length;
    await api(bridge.base, `/api/gallery/${id}/favorite`, { method: "POST", body: { favorited: true } });
    await sleep(300);
    assert.equal((await list(bridge.base))["cat.jpg"].description, "描述:cat.jpg");
    assert.equal(core.calls.filter((n) => n === "cat.jpg").length, before + 1);

    await api(bridge.base, `/api/gallery/${id}/favorite`, { method: "POST", body: { favorited: true } });
    await sleep(200);
    assert.equal(core.calls.filter((n) => n === "cat.jpg").length, before + 1, "有描述了还去识，白花钱");
  });
});

describe("后台慢慢补", () => {
  let core; let bridge;
  before(async () => {
    core = await fakeCore();
    bridge = await startBridge({ NOX_CORE_URL: core.url, NOX_GALLERY_BACKFILL_MS: "400" });
  });
  after(async () => { await bridge?.stop(); await core?.close(); });

  test("没描述的补上；坏图挂 3 次之后不再撞", async () => {
    await save(bridge.base, "old.jpg");
    await save(bridge.base, "bad.jpg");
    core.setReady(true);
    await sleep(2600);   // 6 轮左右
    const g = await list(bridge.base);
    assert.equal(g["old.jpg"].description, "描述:old.jpg");
    assert.ok(!g["bad.jpg"].description);
    const badCalls = core.calls.filter((n) => n === "bad.jpg").length;
    assert.equal(badCalls, 1 + 3, `存的时候 1 次 + 后台 3 次就该停（实际 ${badCalls}）`);
    const oldCalls = core.calls.filter((n) => n === "old.jpg").length;
    assert.ok(oldCalls <= 2, `补上了就别再识（实际 ${oldCalls}）`);
  });
});
