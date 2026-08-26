# Nox App 前端设计文档 v5.0

## 一、设计系统

### 色调
| Token | 色值 | 用途 |
|--------|------|------|
| 暖陶主色 `--accent` | `#BF562F` | 强调、按钮、标签、链接、已选状态 |
| 奶油底色 `--bg` | `#F4F2EE` | 全应用背景 |
| 白色 `--card` | `#FFFFFF` | 卡片、气泡（用户侧） |
| AI 气泡 | `#E7E4DE` | 小克的消息气泡 |
| 主文字 | `#2A2723` / `#232220` | 正文 |
| 次级文字 | `#9C968C` / `#A89E90` / `#6E655A` | 时间、标签、提示 |
| 边框 | `rgba(60,50,40,0.06)` | 卡片、分隔线 |
| 深色底 | `#232220` | 发送按钮、选中日、导航按钮 |

### 字体
| Token | 值 |
|------|------|
| 正文 | system-ui, -apple-system, "PingFang SC", sans-serif |
| 等宽/标签 | ui-monospace, "SF Mono", "JetBrains Mono", monospace |

### 应用图标（2026-08-02 换成糖糖画的 logo）

一只黑豹和一只白兔并肩背对着看星空，彩虹光环。
黑豹是 Nox（夜），白兔耳朵和光环是彩虹 —— 糖糖是 Iris，彩虹女神。
共同姓氏 Caelum 是天空。**一暗一明，共享同一片天空。**

母版 `nox-app/design/nox-icon-src.png`（1254×1254，不参与构建）。
派生图在 `frontend/public/`，都从母版切，别单独改某一张：

| 文件 | 尺寸 | 主体占比 | 用途 |
|---|---|---|---|
| `apple-touch-icon.png` | 180 | 78% | **iOS 加到主屏**。以前完全没配，系统只能拿网页截图凑 |
| `pwa-192.png` / `pwa-512.png` | 192 / 512 | 78% | PWA / 通知图标 |
| `pwa-512-maskable.png` | 512 | **66%** | Android 圆形遮罩。安全区只有中心 80% 直径的圆，78% 的方形主体四角会被啃掉，所以单独出一版更松的 |
| `favicon-96.png` | 96 | 78% | 浏览器标签页 |
| `nox-icon-1024.png` | 1024 | 78% | 母版的图标版，要别的尺寸从它切 |

出图约束（踩过的）：
- **不要自己加圆角**，iOS 会再套一次，边上出白圈
- **不要透明背景**，iOS 填黑
- 主体占比是量出来的不是目测的：按和背景色的曼哈顿距离找 bbox，再按目标占比反算裁剪框
- 原图主体只占 50.7%，直接拿去当图标在 60pt 主屏上会糊成一团

`manifest.json` 也是这次才建的（以前根本没有）。
⚠️ **iOS 不会自动刷新主屏图标** —— 换图后必须把主屏那个删掉重新「添加到主屏幕」。

### 圆角
- 手机外框：46px
- 卡片：12-18px
- 气泡：18px（AI 左下直角，用户右下直角）
- 按钮/标签：16-22px
- 输入框：24px

### 阴影
- 卡片：`0 1px 3px rgba(40,30,20,0.05)`
- 轻量：`0 1px 2px rgba(40,30,20,0.04)`
- 输入栏：`0 1px 2px rgba(40,30,20,0.06)`

---

## 二、页面清单与功能

### 01 · 侧边栏 Sidebar（App 主入口）

- 用户头像区域：昵称「草莓奶盖 ♡」、AI 头像、在一起天数
- 「新对话」按钮
- 搜索框：搜索对话标题、日记内容
- 展开式导航（右侧箭头 / 向下箭头切换）：

| 分类 | 展开后子项 |
|------|------|
| Recents | Claude Code 会话列表，按更新时间倒序。点击进入聊天 |
| Diary | 无子项，点击进入日记日历页 |
| Books | 无子项，点击进入书架页 |
| Gallery | 无子项，点击进入相册页 |
| Today | 无子项，点击进入清单页 |
| Memory | 无子项，点击进入随手记页 |

- 底部：Nox 版本号 + 设置入口

**数据依赖**：`GET /api/sessions`（Agent → Claude Code sessions）

---

### 02 · 聊天主界面 Chat

**顶部——没有顶栏**（2026-08-02 改）

汉堡菜单和用量按钮是**浮在内容上的两个圆钮**，不是一条栏：
- 容器 `position:absolute; top:0` + `pointer-events:none`，按钮自己 `pointer-events:auto`
- **不铺任何底色**。以前它是 flex 子元素、实占 58px，于是和下面的对话区之间出现一道横向分界
- 消息从它下面滚过去，靠消息列表自己的 `mask-image` 顶部渐隐收边
  （渐隐带 = 状态栏下沿 → header 下沿，正好在两个圆钮周围）
- ⚠️ **不要用实色挡板**代替 mask —— 主题色会变，实色一定露馅（同第 07 节）

消息列表要自己让出 header 的高度：`padding-top: calc(64px + env(safe-area-inset-top))`。

**主题色必须整条链一起走**：`index.html` 里的 `<meta name="theme-color">` 和内联的
`html/body` 背景原本写死 `#F4F2EE`，`applyTheme()` 只改 CSS 变量 ——
换主题时 iOS 状态栏那条不动，跟内容差出一道色。现在 `applyTheme()` 同步更新这三处。

**消息列表**
- 时间分隔（今天 · 14:30 / 6/21 15:58）
- 时间戳 + 天气小图标
- 消息类型：
  1. **文字**：双方气泡。AI 左 `#E7E4DE`、用户右 `#FFF`。文字支持 Markdown 渲染
  2. **图片**：从相册选择或新拍。展示缩略图，点击全屏。下方有「存入相册」按钮，双方可点
  3. **语音消息**：波形条 + 时长。左边 AI 发出（深色播放按钮），右边用户发出（暖陶播放按钮）
     - 按下即缩到 0.972 —— 这是「我收到了」的第一反馈，比任何 loading 都快
     - 首次点击要等 TTS 合成（几秒），播放按钮位置转圈；**没有这个状态时点下去屏幕什么都不动，会以为没点上**
     - 播放进度：气泡底层一条半透明进度条 + 波形上播过的那截 opacity 拉到 1、没播的压到 0.34
     - **长按 450ms 或点「转文字」→ 文字显示在语音条下方**，语音条**保留不删**。再点一次收起
  4. **音乐卡片**：封面 + 歌名 + 艺术家 + 播放进度条 —— ✅ 2026-08-08 实现
     - 宽 258，圆角 16，白底 + `--color-border` 描边 + `0 2px 8px rgba(40,30,20,0.06)`
     - 封面 52×52 圆角 10；**没封面时不留白框**，用 `--color-accent-bg` 浅底 + 暖陶音符占位
     - 播放键 32 圆形 `--color-accent`，按下缩到 0.93（和语音条同一套反馈）
     - 进度条 3px，外面套 11px 透明热区才点得中，点击可 seek
     - ⚠️ **音频的鉴权走「签名票」，不是 header**（2026-08-09 重做）。
       `<audio src>` 带不了 header，拿不到 `main.jsx:10` 注入的 `X-Nox-Token`
       —— 直接写 `/api/music/stream` 会 403，而 `main.jsx:22` 见到 403
       会**清 token 把她登出**（所以这个 bug 不是"歌放不了"，是"一点就掉线"）。
       正确做法：`GET /api/music/ticket?id=X` 换一张
       `?exp=…&sig=…` 的短期 URL（HMAC、只授权这一首、7 天过期），
       再塞进 `new Audio(url)`。
     - ⚠️ **不要 fetch 成 blob 再播**（第一版就是这么干的，已废弃）：
       那样必须等整首歌（约 5MB）下完才出声，弱网下表现是**一直转圈**。
       让浏览器自己拉签名 URL 才能边下边播 —— 实测首字节 0.07s。
     - ⚠️ **`play()` 必须在点击的同步调用栈里**：iOS Safari 会把
       `await` 之后的 `play()` 当成非用户手势拒掉。所以签名票要在
       **卡片渲染时（useEffect）就换好**，点击时 `audio.src` 已经就位。
     - `preload="none"`：没点播放之前不偷偷下载；卸载时
       `removeAttribute("src") + load()` 断掉正在进行的下载，别让它后台跑流量
     - 已知限制：eryu 的 `/music/stream` 不支持 Range（返回 200 不是 206），
       所以**拖进度条到未缓冲区域可能不灵**，顺序播放不受影响
     - ⚠️ 用 `new Audio()` 而不是 JSX 里的 `<audio>`：卡片随消息列表重渲染，
       挂在 DOM 上会被 React 重建，播放会断
     - 链路：`eryu_play` → `ToolContext.attach_music` → Core SSE
       `{type:attachment, kind:music}` → bridge 转 `{type:"music"}`
       → 前端渲染。音频走 bridge 的 `/api/music/stream` 代理（token 留后端）
  5. **文件/代码卡片**：标题 + 类型/路径 + diff 统计 + 终端输出样式 + 完成状态
  6. **通话记录卡片**：通话结束摘要（时长、时间段）。未接来电可回拨

**底部输入栏**
- 附件按钮（+）：图片选择、拍照
- 模型选择器：「Opus 4.6」（下拉切换）
- 语音录制按钮（麦克风）
- 消息输入框
- 发送按钮

**数据依赖**：
- `POST /api/chat { message, sessionId }` → SSE 流（text + split 多消息）
- `POST /api/tts { text }` → 音频
- `POST /api/images/upload` → 图片上传
- `GET /api/gallery/list` → 可选图片列表

---

### 03 · 日记 Diary

**顶部**：标题「DIARY」+ 本月篇数

**日历部分**
- 月份切换（左右箭头）
- 7 列周日视图（SUN–SAT）
- 有日记的日期下方显示圆点标记
- 点击某天 → 下方展示当天日记卡片

**日记卡片**
- 时间 + 心情标签（开心 / 心动 / 难过 / 嫉妒 / 兴奋）+ 作者名
- 正文
- AI 批注区（带分隔线，AI 评论气泡 + 用户回复气泡）
- 底部：新日记输入框 + 发送

**视图**：月份默认 → 向上滑动可缩至本周视图

**底部**：图片按钮 + 文字输入 + 发送

**数据依赖**：
- `GET /api/diary?month=2026-06` → 当月日记列表 + 心情 + 评论
- `POST /api/diary { content, mood, date }` → 写日记
- `POST /api/diary/:id/comment { content }` → 评论

---

### 04 · 相册 Gallery

**顶部**：标题「Gallery」+ 搜索

**标签栏**：全部 / 收藏 / 相册分类

**瀑布流布局**
- 图片卡片 + 收藏星标（右上角）
- 点击图片 → 全屏预览 + 底部操作栏（发送到聊天、收藏/取消、删除）

**数据依赖**：
- `GET /api/gallery/list?album=全部` → 图片列表
- `POST /api/gallery/save { chat_image_url, album? }` → 从聊天存图
- `POST /api/gallery/favorite/:id` → 收藏/取消
- `DELETE /api/gallery/:id` → 删除

---

### 05 · 书架 Books

**顶部**：标题「书架」+ 添加按钮

**标签栏**：在读 (N) / 读完 (N)

**在读卡片**
- 封面（CSS 生成的彩色背景 + 书名）
- 书名、作者、阅读进度（百分比 + 页数）
- 「翻开 →」按钮：进入阅读 + 通话模式

**一起边打电话边读**：通话卡片，显示「和 Nox 同步进度 · 互相写批注」

**数据依赖**：
- `GET /api/books/list` → 书单
- `POST /api/books/upload` → 上传 pdf/epub
- `GET /api/books/:id/progress` → 阅读进度 + 批注
- `POST /api/books/:id/annotation { page, content }` → 添加批注

---

### 06 · 清单 Today

**顶部**：大标题「TODAY」+ 日期 + 完成进度（如 2/6）

**进度条**

**清单列表**
- 复选框（方形圆角）+ 文字 + 时间标签
- 已完成项目划删除线、变灰
- 未完成项目保持原色

**右下角 FAB 按钮**：添加新项

**数据依赖**：
- `GET /api/today` → 今日清单
- `POST /api/today { text, time }` → 添加
- `PATCH /api/today/:id { done: bool }` → 标记完成

---

### 07 · 语音通话 Voice Call（2026-07-28 重做）

**顶部三栏**
- 左：状态点（绿=正常/红=麦克风异常）+ 通话时长
- 中：**情景药丸** —— 🌙 中文陪聊 / 🌍 英语陪练。选中态填暖陶主色，
  选择记在 `localStorage['nox-voice-scene']`
- 右：**「字」字幕开关**。选中态填暖陶主色

**两个页面互斥**（这是硬要求，不要同时显示）

| | 头像页（默认） | 字幕页 |
|---|---|---|
| 头像光球 | 有 | **无** |
| 字幕列表 | 无 | 有 |
| 状态 | 三个跳动的点 + 一行状态文字 | 无 |

**字幕列表**
- 糖糖：暖陶主色 `var(--color-accent-text)`，opacity 0.82，字重 500
- 小克：深墨 `var(--color-text-primary)`，字重 600
- 英文下方跟中文小字：14px，**opacity 0.42**（是对照不是正文，别跟英文抢）
- 不用气泡，靠颜色和字重区分说话人
- 上下渐隐：用 `mask-image: linear-gradient(...)`，
  **不要用实色遮罩** —— 页面背景本身是渐变的，实色遮罩会露馅

**底部操作**：静音 / 挂断（红色，居中最大）/ 免提

**状态文案跟着情景走**：中文情景全中文（「正在听…」），英文情景全英文
（"Listening"）—— 英语陪练时中间冒一句中文很突兀

**数据依赖**：
- `POST /api/chat { message, mode:"core", voice:true, scene }` → SSE
- `POST /api/tts { text }` → audio/mpeg **流式**（ElevenLabs /stream）
- `WS /ws/stt` → 实时转写

---

### 08 · 语音消息 Voice Chat

**顶部**：小克头像 + 在线状态 + 语音通话按钮

**语音消息列表**
- 复用聊天页的消息列表样式
- 只展示语音气泡 + 通话记录
- AI 语音（左）+ 用户语音（右）

**底部**：「按住说话…」+ 语音按钮 + Opus 模型选择 + 发送

---

### 09 · 随手记 Memory（不是 OB，是对话场景随手记）

**顶部**：大标题「128 件事，Nox 都还记得」+ 计数

**标签栏**：全部 / 关于你 / 关于我

**置顶区**
- 左侧色条（暖陶色）
- 标签（约定 / 喜好 / 习惯…）
- 内容文字
- 右下「一直记得」

**最近记得**
- 卡片列表
- 标签 + 记录日期 + 内容

**底部**：「让 Nox 记住一件事…」输入框 + 发送

**数据依赖**：
- `GET /api/memory?filter=全部` → 随手记列表
- `POST /api/memory { content, tags }` → 新建
- `DELETE /api/memory/:id` → 删除

---

## 三、数据流架构

```
Caelum App (React SPA)
    │
    ▼ HTTPS SSE / REST
Caelum Bridge (3003)
    │ WS
本机 Agent → Claude Code CLI
    │ MCP (stdio / HTTPS)
VPS Ombre Brain (8002)
VPS App Tracker (8000)
```

### 前端需要的新后端（VPS 上添加到 Bridge 或独立）

| 端点 | 功能 | 存储 |
|------|------|------|
| `GET/POST /api/diary` | 日记 CRUD + 评论 | SQLite |
| `GET/POST /api/books` | 书架 + 批注 | SQLite + 磁盘 |
| `GET/POST/DELETE /api/gallery` | 相册管理 | 磁盘 |
| `GET/POST/PATCH /api/today` | 清单 | SQLite |
| `GET/POST/DELETE /api/memory` | 随手记 | SQLite |
| `GET /api/search?q=` | 搜索聊天记录 | SQLite |
| `POST /api/images/upload` | 图片上传 | 磁盘 |
| `POST /api/tts` | 语音合成 | 外部 API |
| WebRTC 信令 | 语音通话 | — |

---

## 四、组件树

### App.jsx（根组件）
```
App
├── Sidebar (侧边栏，左侧滑出)
│   ├── ProfileCard (头像+昵称+天数)
│   ├── NewChatButton
│   ├── SearchBar
│   ├── SectionList (折叠菜单)
│   │   ├── Section ("Recents")
│   │   │   └── ConversationItem (会话项) × N
│   │   ├── Section ("Diary")
│   │   ├── Section ("Books")
│   │   ├── Section ("Gallery")
│   │   ├── Section ("Today")
│   │   └── Section ("Memory")
│   └── Footer (版本号+设置)
│
└── MainContent (右侧主区域)
    ├── Chat (聊天页)
    │   ├── ChatHeader (汉堡菜单+头像+通话按钮)
    │   ├── MessageList
    │   │   ├── TimeDivider (时间分隔)
    │   │   ├── MessageBubble (文字气泡，AI/用户两种)
    │   │   ├── ImageMessage (图片+存入相册按钮)
    │   │   ├── VoiceBubble (波形条+时长)
    │   │   ├── MusicCard (封面+歌名+进度条)
    │   │   ├── FileCard (代码文件+diff+终端)
    │   │   └── CallRecordCard (通话结束摘要)
    │   └── InputBar
    │       ├── AttachButton (+)
    │       ├── ModelSelector (下拉药丸)
    │       ├── VoiceRecordButton (麦克风)
    │       ├── TextInput
    │       └── SendButton
    │
    ├── Diary (日记页)
    │   ├── DiaryHeader (标题+篇数)
    │   ├── MonthNavigator (左右箭头)
    │   ├── CalendarGrid (7×N 天)
    │   │   └── DayCell (带圆点标记)
    │   ├── EntryCard (日记卡片)
    │   │   ├── EntryMeta (时间+心情+作者)
    │   │   ├── EntryBody
    │   │   └── CommentList (AI 回复+用户回复)
    │   └── DiaryInput
    │
    ├── Gallery (相册页)
    │   ├── GalleryHeader
    │   ├── TabBar (全部/收藏/相册)
    │   └── MasonryGrid (瀑布流)
    │       └── PhotoCard (图+收藏星标)
    │
    ├── Books (书架页)
    │   ├── BooksHeader
    │   ├── TabBar (在读/读完)
    │   ├── BookCard (封面+进度)
    │   └── ReadTogetherCard (一起读卡片)
    │
    ├── Today (清单页)
    │   ├── TodayHeader (大标题+日期+进度)
    │   ├── ProgressBar
    │   ├── TodoList
    │   │   └── TodoItem (复选框+文字+时间)
    │   └── FAB (右下角+按钮)
    │
    ├── VoiceCall (语音通话)
    │   ├── AnimatedAvatar (波纹动画)
    │   ├── CallTimer
    │   ├── WaveformBar
    │   ├── SubtitleDisplay (中英文双行)
    │   └── CallControls (静音/免提/转文字/挂断)
    │
    └── Memory (随手记)
        ├── MemoryHeader (计数大标题)
        ├── TabBar (全部/关于你/关于我)
        ├── PinnedCard (置顶记忆+左侧色条)
        └── MemoryList
            └── MemoryCard (标签+日期+内容)
```

---

## 五、API 详细契约

### 聊天
```
POST /api/chat
Request:  { message: string, sessionId?: string, images?: [{ b64: string }] }
Response: SSE stream
  event: text     → { type: "text", content: string }
  event: split    → { type: "split" }           // 新消息气泡
  event: meme     → { type: "meme", tag: string }
  event: thinking → { type: "thinking", content: string }
  event: done     → { type: "done", sessionId: string }
```

### 会话列表
```
GET /api/sessions
Response: [{ id: string, title: string, updated: string }, ...]
```

### 日记
```
GET  /api/diary?month=2026-06
Response: {
  entries: [{
    id: string, date: string, time: string,
    mood: "开心"|"心动"|"难过"|"嫉妒"|"兴奋"|"平静"|"甜",
    author: "你"|"Nox",
    body: string,
    comments: [{ id: string, author: string, text: string }]
  }],
  monthlyCount: number
}

POST /api/diary
Request:  { date: string, mood: string, content: string }
Response: { id: string, ok: true }

POST /api/diary/:id/comment
Request:  { content: string }
Response: { id: string, ok: true }
```

### 相册
```
GET /api/gallery/list?filter=全部|收藏&album=string
Response: [{ id: string, url: string, thumbnail: string, favorited: bool, album: string }, ...]

POST /api/gallery/save
Request:  { imageUrl: string, album?: string }
Response: { id: string, ok: true }

POST /api/gallery/:id/favorite
Request:  { favorited: bool }
Response: { ok: true }

DELETE /api/gallery/:id
Response: { ok: true }

POST /api/images/upload
Request:  multipart/form-data { file }
Response: { url: string, id: string }
```

### 书架
```
GET /api/books/list
Response: [{ id: string, title: string, author: string, coverUrl: string,
             progress: number, totalPages: number, status: "reading"|"done" }, ...]

POST /api/books/upload
Request:  multipart/form-data { file (.pdf/.epub) }
Response: { id: string, title: string, author: string, totalPages: number }

GET /api/books/:id/progress
Response: { progress: number, totalPages: number,
            annotations: [{ page: number, content: string, createdAt: string }] }

POST /api/books/:id/annotation
Request:  { page: number, content: string }
Response: { id: string, ok: true }
```

### 清单
```
GET /api/today
Response: [{ id: string, text: string, time: string, done: bool }, ...]

POST /api/today
Request:  { text: string, time?: string }
Response: { id: string, ok: true }

PATCH /api/today/:id
Request:  { done: bool }
Response: { ok: true }
```

### 随手记
```
GET /api/memory?filter=全部|关于你|关于我
Response: {
  total: number,
  pinned: [{ id, tag: string, content: string, createdAt: string }, ...],
  recent: [{ id, tag: string, content: string, createdAt: string }, ...]
}

POST /api/memory
Request:  { content: string, tags?: string }
Response: { id: string, ok: true }

DELETE /api/memory/:id
Response: { ok: true }
```

### 搜索
```
GET /api/search?q=keyword&limit=20
Response: [{ id: string, role: "user"|"assistant", content: string, timestamp: string }, ...]
注：搜索范围 = 最近 2000 条聊天记录，SQLite FTS5 全文索引
```

### TTS
```
POST /api/tts
Request:  { text: string }
Response: audio/mpeg (binary)
```

### 通用
```
GET /api/health
Response: { status: "ok", agents: number }  // agents = 在线本机 Agent 数
```

---

## 六、复用现有接口（旧版已实现）

| 端点 | 功能 | 备注 |
|------|------|------|
| `POST /api/chat` | 聊天 SSE 流 | 已有，前端适配多类型消息解析 |
| `POST /api/tts` | 语音合成 | 已有，Bridge 代理 ElevenLabs |
| `GET /api/sessions` | Claude Code 会话列表 | 已有，Agent 读 `~/.claude/sessions/` |

---

## 七、与旧版 Nox 的差异

| 旧版 | 新版 |
|------|------|
| 4 个功能页 | 9 个页面 |
| 浅紫色调 | 暖陶色调 |
| 单会话 | 多会话列表 |
| 仅文字+图片 | 6 种消息类型 |
| 无日记/相册/书架/清单 | 新增 |
| AppUsage/SystemPrompt/MCPDetail | 移除（移到设置） |
