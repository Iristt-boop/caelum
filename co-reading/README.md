# co-reading 前端（本地镜像）

⚠️ **这里是镜像，不是源头。** 真正跑着的代码在 VPS：`/root/co-reading-mcp`。

改之前**先拉一次**，不然会像当年 Ombre Brain 那样，本地停在旧版本、
在线上早就变了，查半天查不出来。

```powershell
$k = "C:\Users\14372\.ssh\id_ed25519"

# 1. 先拉
scp -i $k root@43.133.211.140:/root/co-reading-mcp/public/reader.css public\reader.css
scp -i $k root@43.133.211.140:/root/co-reading-mcp/public/reader.html public\reader.html
scp -i $k root@43.133.211.140:/root/co-reading-mcp/public/reader.js   public\reader.js

# 2. 改

# 3. 推回（静态文件，不用重启服务）
scp -i $k public\reader.css root@43.133.211.140:/root/co-reading-mcp/public/reader.css
```

## 改样式记得改版本号

静态文件服务**不发 `Cache-Control` / `ETag` / `Last-Modified`**，
浏览器可能一直吃旧的 CSS。所以 `reader.html` 里是：

```html
<link rel="stylesheet" href="/reader.css?v=20260729" />
```

改样式时把那个日期一起改，否则改了等于没改。

## repro.html

调布局用的最小复现页：真实的 `reader.css` + `renderBooks()` 生成的真实 DOM，
数据写死。直接用浏览器打开，配合 `getBoundingClientRect()` 量像素。

书架那个"选中态溢出"就是这么定位的 —— 光读 CSS 猜了三轮全错。
**布局 bug 别猜，量。**

复现那个 bug 的条件（现已修复）：窗口 ≤980px + `document.body.classList.add('has-book')`。
