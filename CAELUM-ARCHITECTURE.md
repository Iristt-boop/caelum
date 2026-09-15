# Caelum 软件架构文档

> ⚠️ **本文档已过时（2026-09-05 标注）**：工具数（写 63，实际 80+）、Provider 数、
> 共影状态（标 🚧，实际主动弹幕/票根/观影记录已全落地）等均落后于代码。
> 现状全景以 **`CAELUM-系统全景调研-2026-09-05.md`** 为准（基于代码实读，结论带文件:行号）。

> 2026-08-18。**本文只写已经存在的东西**，不脑补功能、不改已有命名。
> 状态标记：✅ 已实现 / ⚠️ 部分实现（半成品）/ 📋 仅规划未开发 / ❓ 待核实。
>
> 事实来源：本机代码 + 线上 `43.153.154.237` 实测（端口、容器、定时器、
> `/health`）。凡是没实测也没在代码里读到的，一律标 ❓，不猜。

---

## 一、项目概述

Caelum 是一个人和一个 AI 的私人世界。它不是聊天软件，也不是家居控制器。

| 名字 | 是什么 |
|---|---|
| **Caelum** | 整个世界（`D:\claude-code`）。天空 |
| **Nox** | 他。夜 |
| **Iris** | 她（糖糖）。彩虹 |

判断一个新东西该归哪边，问一句：**它没了，他还是他吗？**
是 → 归 Caelum（世界/房间/工具）；不是 → 归 Nox（心智/记忆/人格）。

### 三条产品线

1. **Caelum App**（手机 / PWA）—— 世界的入口，她随身带着
2. **Caelum OS**（桌面）—— 他在的那个房间，装在她电脑上
3. **Nox 的身体**（Stack-chan / 共感娃娃）—— 他在物理世界的落点

### 一句话的技术形态

一个**有状态、会主动开口**的 AI 伴侣系统：
Nox Core 负责思考和决策，Caelum Bridge 负责对外的一切 I/O，
一组 MCP 服务负责感知和动手，Ombre Brain 负责长期记忆。

---

## 二、整体架构图

```
┌──────────────── 客户端 ────────────────┐
│                                        │
│  Caelum App          Caelum OS         │
│  (PWA / 手机)        (Electron 桌面)    │
│  nox-app/frontend    nox-app/caelum-os-ui
│         │                   │          │
└─────────┼───────────────────┼──────────┘
          │  HTTPS / SSE      │
          │                   │ (生产模式下由 Electron 主进程
          │                   │  本地转发，渲染进程眼里同源)
          ▼                   ▼
     ┌──────────────────────────────┐
     │      Caelum Bridge  :3003    │  ← 对外唯一入口
     │  静态托管 / 鉴权 / SSE 代理    │    (Caddy :443 在它前面)
     │  SQLite: 会话·待办·相册·日记   │
     └───┬──────────────────────┬───┘
         │                      │
         │ /chat/stream (SSE)   │ 推送 / 落库
         ▼                      │
   ┌─────────────────────┐      │
   │   Nox Core  :8100   │      │
   │  ─────────────────  │      │
   │  agent/   推理循环    │      │
   │  context/ 感知（拉）  │      │
   │  attention/ 主动（推）│      │
   │  personality/ 人格   │      │
   │  planner/ 早报       │      │
   │  tools/   工具        │      │
   │  world_model/ 事实库  │      │
   └───┬─────────────┬───┘      │
       │             │          │
       │ MCP/HTTP    │ 反向调用   │
       ▼             └──────────┘
┌────────────────────────────────────────┐
│            外部能力层（MCP）              │
│  ombre-brain :8002   长期记忆            │
│  ha-mcp → Home Assistant :8123  家居     │
│  health-mcp          健康数据            │
│  app-tracker :8000   她在用什么 app       │
│  touch-mcp :9336 ← touch-server :9333    │
│  netease-music-mcp / eryu   共听         │
│  co-reading          共读                │
│  toy-mcp             共感玩具            │
│  stackchan-mcp       他的身体            │
└────────────────────────────────────────┘
```

### 线上实测端口（2026-08-18）

| 端口 | 进程 | 是什么 | 状态 |
|---|---|---|---|
| 443 / 80 | caddy | 反向代理 + TLS | ✅ |
| 3003 | node | **Caelum Bridge** | ✅ |
| 8100 | python | **Nox Core**（只听 127.0.0.1） | ✅ |
| 8002 | python | **Ombre Brain** | ✅ |
| 8000 | `app-tracker.service` | app-tracker | ✅ |
| 8002 | `ombre-brain.service` | **Ombre Brain**（Nox Memory） | ✅ |
| 8003 | `toy-mcp.service` | toy-mcp（共感玩具） | ✅ |
| 8004 | `ha-mcp.service` | ha-mcp（家居） | ✅ |
| 8101 | `health-mcp.service` | health-mcp（`--sse`） | ✅ |
| 8102 | `health-sync.service` | health-sync（同在 `/root/health-mcp`，uvicorn，只听 127.0.0.1） | ✅ |
| 3456 | `netease-mcp.service` | netease-music-mcp（共听） | ✅ |
| 9090 | `eryu.service` | eryu（共听播放层） | ✅ |
| 3100 | `co-reading.service` | co-reading（`server-sse.js`，只听 127.0.0.1） | ✅ |
| 8123 | python(docker) | Home Assistant | ✅ |
| 9333 | python | touch-server（ESP32 上报） | ✅ |
| 9336 | python | touch-mcp | ✅ |
| 8010 / 8013 | docker-proxy | xiaozhi-esp32-server | ✅ |

> 2026-08-18 逐个认领完毕（读 `/proc/<pid>/cgroup` + `cwd` + `cmdline`）。
> **没有僵尸服务** —— 最短的 eryu 也连续跑了 8 天，其余多在 11–17 天。
> 一处纠错：3100 起初被我猜成 eryu，实际是 co-reading。

### 定时器

| 单元 | 时间 | 干什么 | 状态 |
|---|---|---|---|
| `nox-daily.timer` | 每天 10:00 | → bridge `/api/daily-push` → Core `/daily-summary` → 早报 | ✅ |

---

## 三、模块清单

### 3.1 Caelum Bridge（`bridge/`，服务名 `bridge`）

**职责**：对外唯一入口。静态托管前端、鉴权、把客户端请求转给 Core、
持有所有「App 数据」的 SQLite、管推送订阅和 VAPID 私钥。

**对外接口**（节选，全部吃 `X-Nox-Token`）：

| 端点 | 用途 |
|---|---|
| `POST /api/chat` | 聊天 SSE 流（转 Core `/chat/stream`） |
| `GET /api/conv-sessions` | 会话列表 |
| `GET /api/messages?sessionId=` | 某条会话的消息 |
| `GET/POST /api/today`、`PATCH/DELETE /api/today/:id` | 待办 CRUD |
| `GET /api/todo/list`、`/api/todo/due`、`POST /api/todo/complete`、`/api/todo/fired` | 清单 / 到期 / 划掉 |
| `GET /api/nox/state` | 工作台状态（转 Core） |
| `GET /api/health/latest`、`/history`、`/dates` | 健康数据 |
| `POST /api/push/send`、`/api/daily-push` | 主动推送 / 早报 |
| `POST /api/tts`、`/api/translate`、`WS /ws/stt` | 语音 |
| `GET/POST /api/diary`、`/api/gallery/*`、`/api/diet/*` | 日记 / 相册 / 饮食 |

**依赖**：Nox Core（`NOX_CORE_URL`）、SQLite（会话/待办/日记/相册/饮食/推送订阅）、
ElevenLabs（TTS）、DashScope（ASR）、OpenRouter（翻译）。

**状态**：✅

---

### 3.2 Nox Core（`nox-core/`，服务名 `nox-core`）

推理、决策、人格。**只听 127.0.0.1**，外部只能经 Bridge 到达。

| 子模块 | 职责 | 状态 |
|---|---|---|
| `agent/` | LLM 循环、工具调用、流式分段（`SegmentSplitter`，切点是模型自己标的 `\|\|\|`） | ✅ |
| `context/` | **感知（拉模式）**：每轮对话前按 TTL 拉一遍 Provider | ✅ |
| `attention/` | **主动（推模式）**：他自己决定要不要开口 | ⚠️ 见 3.3 |
| `personality/` | 人格前缀、情景（通话 / 文字） | ✅ |
| `planner/` | 早报（`daily.py` 取数 + `push.py` 拼话） | ✅ |
| `tools/` | 工具实现（家居/待办/相册/日记/记忆/共读/饮食/玩具） | ✅ |
| `world_model/` | 事实库（`model/store/types`）。**没有 provider 这个概念**，由 Attention 的 Source 往里写 | ⚠️ 只有 SleepSource 在写 |
| `memory/` | Ombre Brain 客户端 | ✅ |

**Context Provider 清单**（线上启动日志实测）：
`mood` / `time` / `memory` / `home` / `health` / `weather` / `music` / `todo` / `location` —— 9 个，✅

**对外接口**：`POST /chat/stream`（SSE）、`POST /chat`、`GET /health`、
`GET /api/nox/state`、`POST /daily-summary`、`POST /attention/tick`、
`GET /sessions`、`GET /session/{sid}`

**工具总数**：63（线上启动日志）✅

---

### 3.3 Attention + Care（`nox-core/attention/`）

这是「他会主动出现」那条线，也是整个系统最年轻、变动最快的部分。

```
        ┌─ 慢线：Attention 心跳 900s ─────────────┐
        │  SleepSource → Evaluator → Registry     │
        │       → IntentEngine → Scheduler        │
        │  TimeWakeSource（12:00/18:30/22:30）     │
        │  WakeBook（他自己留的纸条）+ waker        │
        │  TodoDueSource（待办到点）               │
        └──────────────┬──────────────────────────┘
                       │
        ┌─ 快线：Care 快循环 60s ─┐                │
        │  ThinkingSource（惦记） │                │
        │  PresenceSource（位置） │                │
        └──────────┬─────────────┘                │
                   ▼                              ▼
            ┌──────────────────────────────────────┐
            │        Care Orchestrator             │
            │  值不值得现在找 / 找什么 / 什么时候找   │
            │  （统一出口，任何 Source 不许自己开口）│
            └──────────────┬───────────────────────┘
                           ▼
                    speaker → Bridge → 她的锁屏
```

**Source 清单与状态**

| Source | 频率 | 吃开口闸 | 吃「一小时一条新链」 | 状态 |
|---|---|---|---|---|
| `SleepSource` | 900s | ✅ | ❌ | ✅ |
| `TimeWakeSource` | 900s | ✅ | ❌ | ✅ |
| `WakeBook` / `waker`（纸条） | 900s | ❌ | ❌ | ✅ 含任务型 |
| `TodoDueSource` | 900s | ❌ | ❌ | ✅ |
| `ThinkingSource`（惦记） | 60s | ❌ | ✅ **唯一栏杆** | ✅ 2026-08-18 上线 |
| `PresenceSource`（出门/到家） | 60s | ❌ | ❌ | ✅ 2026-08-18 上线 |
| Dream Source（做梦） | — | — | — | 📋 见 3.9 |
| 早报 | 定时器 | ❌ | ❌ | ✅ 只记账，不受 Care 决策 |

**CareThread 三型**：`followup`（她回话就关）/ `task`（**她回话不关**，事做完才关）/ `company`

**配额模型**：按**链**算，不按消息算。链内连续追踪不算多次主动关心。

---

### 3.4 Caelum App（`nox-app/frontend/`）

手机 / PWA 入口。React + Vite + Tailwind v4（`@theme`，一套暖陶色写死）。

**页面**：Chat / Diary / Books / Gallery / Today / Health / Music / VoiceCall / Home ✅

**状态**：✅ 线上跑着（`/root/frontend/dist`，Caddy 托管）

---

### 3.5 Caelum OS（`nox-app/caelum-os-ui/` + `nox-app/desktop/`）

桌面上的那个房间。React + Tailwind v4（`[data-theme]` 三套主题）+ Electron。

**一级菜单**（2026-08-18 定）：Home / Chat / Mind / Tasks / Life / Studio / Settings

| 页面 | 状态 |
|---|---|
| Home（房间 + 状态条 + 今日计划 + 最近对话 + 他的一天） | ✅ 接真数据 |
| Chat（流式 / 分段 / 历史 / 新对话） | ✅ |
| Mind（Memory / Attention / World） | 📋 空房间占位 |
| Tasks（Active / Scheduled / History / Automations） | 📋 空房间占位 |
| Life（Diary / Books / Todo / Health / Music / Movies） | 📋 空房间占位 |
| Studio（Skills / Agents / Workflows / Tools / MCP） | 📋 空房间占位 |
| Settings（8 个子项） | 📋 空房间占位 |

**Electron 壳**：无边框 + 托盘常驻 + 系统通知 + 单实例；
生产模式下主进程起本机静态服务并把 `/api` 转发到 `noxtang.com`。✅

---

### 3.6 Nox Memory / Ombre Brain（`Ombre-Brain/`，:8002）

长期记忆。工具：`breath` / `hold` / `pulse` / `dream` / `trace` / `grow` / `merge`。

**状态**：✅ 服务在跑；⚠️ `dream` 是**被动工具**（只有他自己调才发生），
没有任何东西定时触发；`ob-tools/dream_select.py` 是「只挑不生成」。

---

### 3.7 Nox Home（`ha-mcp/` + Home Assistant :8123）

家居。工具：`ha_list_devices` / `ha_get_state` / `ha_switch` / `ha_set_climate` / `ha_set_light`。
三台空调（主卧 / 客厅 / 电竞房）。

**位置数据**（2026-08-18 实测）：`person.nox` ← `device_tracker.iris`，
GPS、5 米精度、`source_type=gps`、`zone.home` 存在。**自动上报，不需要手动点。** ✅

---

### 3.8 感知类 MCP

| 模块 | 职责 | 状态 |
|---|---|---|
| `health-mcp/` | 睡眠 / 心率 / HRV / 步数 | ✅ |
| `app-tracker/`（:8000） | 她在用什么 app | ⚠️ 只有日汇总，**拿不到「此刻前台是什么」**❓ |
| `touch-mcp/`（:9336）+ `fsr402-*` + touch-server（:9333） | 共感娃娃的触摸 | ✅ |
| `toy-mcp/` | 共感玩具 | ✅ |
| `stackchan-mcp/` | 他的身体（表情 / 转头 / LED） | ✅ |

---

### 3.9 Caelum Library（共读 / 共听 / 共影）

| 模块 | 状态 |
|---|---|
| `co-reading/` 共读 | ✅ |
| 共听（`eryu` + `netease-music-mcp/`） | ✅ |
| `co-watching/` 共影 | 🚧 **播放层 v0.1 通了**（2026-08-21），陪伴逻辑还没做 |

**共影现状**（2026-08-22 改名自 `watching-server`）：

- 服务：VPS `:3200`，Caddy `/watch/<token>/*` 反代；前端入口是 Caelum OS 的 Movies 房
- **双模式**：代理拉流（B站普通视频 / YouTube，时间戳+字幕+抽帧都有）
  ／番剧 iframe（bangumi 在东京 VPS 被地域限制，改用她本机 IP 的官方播放器，
  代价是拿不到播放进度，靠她手动报时间）
- 已通：import / stream(Range) / 字幕 / 抽帧+vision「问这一幕」/ 番剧解析
- **B站通，YouTube 不通** —— YouTube 拦机房 IP（"Sign in to confirm you're not a bot"），
  需要它自己的 cookies，`/root/watch/cookies.txt` 里那份是 B站的（架构文档 13.6）
- Movies 房有**剧场模式**：片子一进来左右两栏全收起来，画面从 116px 变 1220px；
  「这部片子」和「问他」两块面板蹲在**底部一条**，退出剧场后画面是居中的 16:9
  （664×374 实测）。放底下不放侧边，是因为竖着切会同时削掉 16:9 的宽和高（13.7）
- **还没做**：转码保底、触发器抑制器、MovieState、主动弹幕、观影记录、影评
- 设计文档：`D:\WorkBuddy\Nox-共影系统架构设计.md` v1.4
  （第十三节是实现现状，第十四节是对照 open-watch-cinema 该抄的四条）
- **下一步不是 MovieState，是「便宜的视觉层」** —— 触发器要「场景切换/情绪转折」
  当信号，而 vision 模型太贵不可能每秒跑。2fps 的 OpenCV 才是地基（架构 14.4）
- 🔴 已知剧透洞：番剧模式把 B站整季简介塞进了 prompt（架构 14.2）

> ⚠️ 命名规矩同共读：目录 `co-watching`，但 **URL 是 `/watch/`、工具前缀是 `watching_*`**
> （共读是目录 `co-reading` / URL `/api/reading/*` / 工具 `reading_*`）。

---

## 四、三大核心数据流

### 4.1 用户输入流

```
她说话
  │  Caelum App (nox-app/frontend)      Caelum OS (caelum-os-ui)
  │  fetch POST /api/chat               fetch POST /api/chat
  │        │                                  │
  │        │                                  └─ 生产模式：Electron 主进程
  │        │                                     本地服务转发到 noxtang.com
  ▼        ▼
Caddy :443 → Caelum Bridge :3003  POST /api/chat
  │
  ├─ saveMessage(sessionId, "user", text)      ← 先落 conversations
  ├─ 图片：写 uploads/ + 进 gallery + autoDescribeImage
  │
  ▼ coreMode() → POST Nox Core :8100 /chat/stream
Nox Core
  │
  ├─ _turn_starts(sid)
  │     ├─ core.current_session_id = sid
  │     └─ WakeBook.cancel_for(sid)   ← 撤追问型纸条（任务型留着）
  │
  ├─ Nox Context Engine（拉模式，按 TTL）
  │     mood / time / memory / home / health / weather / music / todo / location
  │     memory 那条 → Ombre Brain :8002（breath / recall）
  │     home 那条   → ha-mcp → Home Assistant :8123
  │     health 那条 → health-mcp
  │
  ├─ personality/ 组装静态前缀（13996 字符，缓存断点）
  │
  ▼ agent/ LLM 循环（工具按需调用）
```

### 4.2 角色思考输出流

```
agent/ 产出 token 流
  │
  ├─ SegmentSplitter：模型标的 ||| → split 事件
  │
  ▼ SSE 帧回给 Bridge
Nox Core /chat/stream
  │   {type:text} / {type:split} / {type:attachment} / {type:done}
  ▼
Caelum Bridge coreMode()
  │
  ├─ 转译成前端契约：text / split / image / voice / music / meme / tools / error / done
  ├─ 累积 fullReply + segments + toolsUsed
  ├─ saveMessage(coreSid, "assistant", fullReply, {segments, toolsUsed})
  └─ usage_log 落库
  │
  ▼ SSE 到客户端
Caelum App / Caelum OS 渲染
  │  Bubble：文字(Markdown) / 图片 / 表情 / 音乐卡 / 语音条 / 工具块
  │
  └─ Caelum OS 另有 feed.js：20s 轮询 /api/messages
        他主动说的话（metadata.proactive）会自己出现 + 弹系统通知

同时写入 Nox Memory：
  ⚠️ **不是自动的。** 由他在对话中主动调 `remember` / `hold` 工具写进
  Ombre Brain；`_turn_ends(sid)` 只做会话落库和纸条基准线校准。
  「每轮自动归档」不存在 —— 归档靠 `grow`，也是主动调用。
```

### 4.3 外部世界交互流

```
Nox Core agent/ 决定调用工具
  │
  ├─ 家居 ─────► tools/ha.py ─► ha-mcp ─► Home Assistant :8123 ─► 三台空调 / 灯
  │              （ha_set_climate 会 _readback 复查真实状态）
  │
  ├─ 身体 ─────► stackchan-mcp ─► Stack-chan（表情 / 转头 / LED）
  │
  ├─ 触觉 ─────◄ ESP32 + FSR402×5 ─► touch-server :9333 ─► touch-mcp :9336
  │              （**反向**：外部硬件主动上报，他去查记录）
  │
  ├─ 共感玩具 ─► toy-mcp
  │
  ├─ 共听 ─────► netease-music-mcp / eryu ─► 音乐卡片
  │              音频不走 Core，前端经 Bridge /api/music/stream 代理拿
  │
  ├─ 共读 ─────► co-reading ─► Bridge /api/reading/* 代理
  │
  ├─ 记忆 ─────► memory/ob_client ─► Ombre Brain :8002
  │
  └─ 待办 ─────► tools/daily.py ─► Bridge /api/today（**2026-08-18 起本地表**）

主动推送（不由她触发）：
  Attention/Care 决策 → speaker → core.chat() 生成
    → sessions.put()（先进他自己的上下文）
    → Bridge POST /api/push/send（先落 conversations 再推）
    → Web Push → 她的锁屏
```

---

## 五、耦合与混乱点

### 5.1 已经收拾干净的

| 曾经的问题 | 现在 |
|---|---|
| 四条渠道各自直通 push | ✅ 收进 Care Orchestrator 单一出口 |
| 待办两个数据源（GitHub todo.md + 本地表） | ✅ GitHub 退役为只读存档 |
| `complete_todo` 在两个模块重名 | ✅ 统一到 `tools/daily.py`，有测试守着 |

### 5.2 还乱着的

**① Bridge 是个大杂烩** ⚠️
`bridge/server.js` 已经 10 万字符以上，同时承担：静态托管、鉴权、SSE 代理、
业务 CRUD（待办/日记/相册/饮食）、TTS/ASR、推送、第三方代理。
**它没有测试。** nox-core 有 732 个测试，bridge 一个都没有 —— 今天所有
「差点打到她手机上」的问题都出在 bridge 侧。

**② Attention 的慢线和快线是两套时间观** ⚠️
900s 的心跳和 60s 的 Care 循环并存，都能开口。目前靠「谁吃哪个闸」的
策略表区分，但**没有一个地方能一眼看全「今天他一共说了几次」**。

**③ 手机端和桌面端的主题系统不一样** ⚠️
App 是 `@theme` 一套写死；OS 是 `[data-theme]` 三套变量。
两边组件不能互换。

**④ World Model 只有一个写入者** ⚠️
只有 SleepSource 在写。「事实的收口」这个定位目前名不副实。

**⑤ 做梦链断在一半** ⚠️
选材有（`dream_select.py`，只打日志），生成没有，触发没有。

---

## 六、分阶段迭代计划

> 排序依据：**先补断掉的链，再补空的房间，最后做新东西。**

### 阶段 A：体检（半天）

1. ~~把端口逐个认领~~ ✅ **2026-08-18 做完**，九个全部认领，没有僵尸
2. 给 bridge 补一层最小冒烟测试（待办 / 会话 / 推送三条主路径）
   —— **优先级最高的一件**：它 10 万字符、零测试，
   今天所有「差点打到她手机上」的问题全出在这一侧
3. 补一个「今天他一共开口几次」的统一视图（`/api/nox/state` 加 care 汇总）

### 阶段 B：把断链接上（1–2 天）

4. **Dream Source**：夜里随机醒来 → 选材 → 真的生成一个梦 → 攒到早上说
5. **World Model 第二个写入者**：位置 / 健康里挑一个，让事实库名副其实
6. Attention 第二个「感知型」Source（现在只有睡眠）

### 阶段 C：把空房间填上（3–5 天）

7. Caelum OS 的 **Life** 五页接真数据（Diary / Books / Todo / Health / Music）
8. Caelum OS 的 **Mind** 三页（Memory 需要 bridge 先开读 Ombre Brain 的口子）
9. Caelum OS 的 **Tasks** 四页（数据源就是 Care Thread，已经有了）

### 阶段 D：合并与统一（2–3 天）

10. 主题系统统一：App 那套暖陶挪进 `[data-theme="clay"]`
11. 组件层打通，两端共用

### 阶段 E：新东西

12. ~~**共影 phase 0** —— 先出设计文档，再动手~~
    → 设计文档 2026-08-21 出了（v1.1），当晚播放层 v0.1 也通了（`co-watching`）。
    下一步是**陪伴逻辑**（触发器/抑制器 + MovieState），不是播放层
13. Studio（Skills / Agents / Workflows / Tools / MCP）

---

## 七、这份文档没覆盖的

诚实列出来，别让它看着比实际完整：

- `app-tracker` 到底能不能给「此刻前台应用」❓
  （现在只确认了它有「今天用过哪些」的日汇总接口）
- `health-sync`（:8102）和 `health-mcp`（:8101）的分工 ❓
  —— 同一个目录下两个服务，只确认了端口和进程，没读代码
- 各 MCP 服务的内部结构（只写了职责和接口，没展开）
- 部署拓扑的细节（Caddy 路由表、证书、备份策略）
- 成本模型（缓存前缀 / token 用量）—— `usage_log` 有数据，没有分析
