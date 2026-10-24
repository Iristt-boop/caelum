/**
 * Moments 第二期（10-24 她：「我和你都可以发图片」「票根 / 书签卡自动进 Moments」「帖子里能引用一本书 / 一部电影」）。
 *
 *   · 🔴 带图发帖：图存进 uploads、出口带签名能取回来；只发图不写字也行
 *   · 引用只认 book / movie，形状不对就当没引用
 *   · 🔴 出票根 → 自动一条「我们俩的」（kind=together，带电影引用）；同一场重出票根不重发
 *   · 🔴 共读的新书签卡 → 自动一条（卡片图下到自己这儿、带书的引用）；同一张卡只出一条；老卡（三天前）不倒出来
 */
import assert from "node:assert/strict";
import http from "node:http";
import { after, before, test } from "node:test";

import { api, startBridge } from "./helpers.js";

const PNG = Buffer.from("89504e470d0a1a0a0000000d4948445200000001000000010806000000", "hex");
const JPEG_B64 = "data:image/jpeg;base64," + Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4]).toString("base64");
let reading; let bridge; const cardHits = [];
const now = new Date().toISOString();
const old = new Date(Date.now() - 5 * 24 * 3600 * 1000).toISOString();

before(async () => {
  const srv = http.createServer((req, res) => {
    if (req.headers.authorization !== "Bearer rt") { res.writeHead(401); return res.end(); }
    if (req.url.startsWith("/api/cards?")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify([
        { id: "card_new", bookId: "zarathustra", bookTitle: "查拉图斯特拉如是说", chunkTitle: "序言 3",
          quote: "人是一根绳索", createdAt: now },
        { id: "card_old", bookId: "zarathustra", bookTitle: "查拉图斯特拉如是说", createdAt: old },
      ]));
    }
    if (req.url === "/api/books") {
      res.writeHead(200, { "Content-Type": "application/json" });
      return res.end(JSON.stringify([
        { bookId: "zarathustra", title: "查拉图斯特拉如是说", lastReadAt: now, chunksRead: 3, chunkCount: 20 },
        { bookId: "sisyphus", title: "西西弗斯神话", lastReadAt: now, chunksRead: 8, chunkCount: 12 },
        { bookId: "old-book", title: "很久没读的", lastReadAt: new Date(Date.now() - 20 * 24 * 3600 * 1000).toISOString() },
      ]));
    }
    if (req.url.startsWith("/api/cards/card_new/image.png")) {
      cardHits.push(req.url);
      res.writeHead(200, { "Content-Type": "image/png" });
      return res.end(PNG);
    }
    res.writeHead(404);
    res.end();
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  reading = { url: `http://127.0.0.1:${srv.address().port}`, close: () => new Promise((r) => srv.close(r)) };
  bridge = await startBridge({ READING_URL: reading.url, CO_READING_TOKEN: "rt", NOX_MOMENTS_CARDS_MS: "300" });
});
after(async () => { await bridge?.stop(); await reading?.close(); });

const feed = async () => (await api(bridge.base, "/api/moments?limit=50")).data.items;

test("🔴 带图发帖：存下来、出口带签名取得回来；只发图也行", async () => {
  const r = await api(bridge.base, "/api/diary", { method: "POST", body: { content: "", images: [JPEG_B64, "不是图"],
    ref: { type: "book", id: "zarathustra", title: "查拉图斯特拉如是说" } } });
  assert.equal(r.status, 200);
  const item = (await feed()).find((x) => x.id === r.data.id);
  assert.equal(item.images.length, 1, "坏的那张不收");
  assert.match(item.images[0], /^\/uploads\/.+\.jpg\?exp=\d+&sig=/);
  const img = await fetch(`${bridge.base}${item.images[0]}`);
  assert.equal(img.status, 200);
  assert.deepEqual(Buffer.from(await img.arrayBuffer()).subarray(0, 2), Buffer.from([0xff, 0xd8]));
  assert.deepEqual(item.ref, { type: "book", id: "zarathustra", title: "查拉图斯特拉如是说" });
});

test("引用形状不对就当没引用；没字也没图不收", async () => {
  const r = await api(bridge.base, "/api/diary", { method: "POST", body: { content: "随便写写", ref: { type: "song", id: "x", title: "y" } } });
  assert.equal((await feed()).find((x) => x.id === r.data.id).ref, null);
  assert.equal((await api(bridge.base, "/api/diary", { method: "POST", body: { content: "" } })).status, 400);
});

test("🔴 出票根 → 自动一条我们俩的，带电影引用；重出票根不重发", async () => {
  await api(bridge.base, "/api/watch/state", { method: "POST", body: { session_id: "w1", title: "绵羊侦探团", mode: "local", state: "playing" } });
  await api(bridge.base, "/api/watch/state", { method: "POST", body: { session_id: "w1", state: "ended" } });
  const t = await api(bridge.base, "/api/tickets", { method: "POST", body: { session_id: "w1", kind: "movie", title: "绵羊侦探团", review: "一群羊破案，比想象中好看。" } });
  assert.equal(t.status, 200, JSON.stringify(t.data));
  let together = [];
  for (let i = 0; i < 40 && !together.length; i++) {
    await new Promise((r) => setTimeout(r, 100));
    together = (await feed()).filter((x) => x.kind === "together" && x.ref?.type === "movie");
  }
  assert.equal(together.length, 1);
  assert.equal(together[0].body, "一群羊破案，比想象中好看。");
  assert.deepEqual([together[0].ref.id, together[0].ref.title], ["w1", "绵羊侦探团"]);
  await api(bridge.base, "/api/tickets", { method: "POST", body: { session_id: "w1", kind: "movie", title: "绵羊侦探团", review: "改一下" } });
  await new Promise((r) => setTimeout(r, 500));
  assert.equal((await feed()).filter((x) => x.kind === "together" && x.ref?.type === "movie").length, 1);
});

test("🔴 共读新书签卡 → 自动一条（卡片图存下来、带书的引用）；一张卡一条；老卡不倒", async () => {
  let cards = [];
  for (let i = 0; i < 50 && !cards.length; i++) {
    await new Promise((r) => setTimeout(r, 100));
    cards = (await feed()).filter((x) => x.kind === "together" && x.ref?.type === "book");
  }
  assert.equal(cards.length, 1, "老卡不出");
  assert.match(cards[0].body, /人是一根绳索/);
  assert.match(cards[0].body, /《查拉图斯特拉如是说》 · 序言 3/);
  assert.match(cards[0].images[0], /^\/uploads\/.+\.png\?exp=/);
  await new Promise((r) => setTimeout(r, 900));   // 再轮两三次
  assert.equal((await feed()).filter((x) => x.kind === "together" && x.ref?.type === "book").length, 1);
  assert.equal(cardHits.length, 1, "同一张卡的图只下一次");
});

test("他发帖的素材：我们俩的动态（带图）在前，最近在读的书也算，老书不算，同一本不重复", async () => {
  // 等书签卡那条先进来（上一条测试已经等过，这里再确认一次）
  const r = await api(bridge.base, "/api/moments/material");
  assert.equal(r.status, 200);
  const items = r.data.items;
  const keys = items.map((x) => `${x.ref.type}:${x.ref.id}`);
  assert.ok(keys.includes("book:zarathustra") && keys.includes("book:sisyphus") && keys.includes("movie:w1"));
  assert.ok(!keys.includes("book:old-book"), "两周以前的不算");
  assert.equal(keys.filter((k) => k === "book:zarathustra").length, 1, "同一本只出一次");
  const z = items.find((x) => x.ref.id === "zarathustra");
  assert.match(z.image, /^\/uploads\/.+\.png$/, "书签卡那条带着自己 uploads 里的裸路径");
});
