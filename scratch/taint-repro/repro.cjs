// 复现共影抓帧失败：页面在 A 口，媒体在 B 口，媒体不带 CORS 头。
// 跑法: node repro.cjs   然后开 http://127.0.0.1:7801/
const http = require("http");
const fs = require("fs");

// 一张 1x1 之外看得出颜色的 PNG（红），当作"画面"
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAHUlEQVQoz2P8z8Dwn4GKgIm" +
  "KZo0aOGrgqIGjBpJmIABJmAMZjJ0nHAAAAABJRU5ErkJggg==", "base64");

// ---- B 口：媒体服务。ALLOW_CORS=1 时才带 Access-Control-Allow-Origin
const withCors = process.env.ALLOW_CORS === "1";
const media = http.createServer((req, res) => {
  const h = { "Content-Type": "image/png", "Content-Length": PNG.length };
  if (withCors) h["Access-Control-Allow-Origin"] = "*";
  res.writeHead(200, h);
  res.end(PNG);
});
media.listen(0, "127.0.0.1", () => {
  const mediaPort = media.address().port;
  // ---- A 口：页面服务（模拟 Electron 的 UI 服务）
  http.createServer((req, res) => {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(`<!doctype html><meta charset="utf-8"><body style="font:14px monospace">
<div id="out">跑着…</div>
<script>
  const img = new Image();
  ${process.env.SET_CROSSORIGIN === "1" ? 'img.crossOrigin = "anonymous";' : ""}
  img.onload = () => {
    const c = document.createElement("canvas");
    c.width = c.height = 8;
    c.getContext("2d").drawImage(img, 0, 0);
    let r;
    try { c.toDataURL("image/jpeg", 0.7); r = "抓到了 ✅"; }
    catch (e) { r = "抓不到 ❌ " + e.name + ": " + e.message; }
    document.getElementById("out").textContent =
      "页面 " + location.port + " ／ 媒体 ${mediaPort}"
      + " ／ 服务端CORS=${withCors}"
      + " ／ img.crossOrigin=${process.env.SET_CROSSORIGIN === "1"}"
      + "  →  " + r;
  };
  img.onerror = () => { document.getElementById("out").textContent =
    "图根本没加载出来 ❌（设了 crossOrigin 但服务端没给 CORS 头就会这样）"; };
  img.src = "http://127.0.0.1:${mediaPort}/media/x";
</script></body>`);
  }).listen(7801, "127.0.0.1", () => console.log("http://127.0.0.1:7801/"));
});
