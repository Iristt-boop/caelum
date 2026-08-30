# Caelum 项目知识库
> 每次新开 CC 窗口，先读这个文件，再读最新的 `HANDOFF-*.md`。  
> 本文记「现在长什么样」，HANDOFF 记「为什么这么改 / 哪些坑别再踩」。  
> 最新交接：**`HANDOFF-2026-08-28.md`**（往前：`08-08` → `08-06` → `08-02` → `07-25`）  
> ⚠️ **HANDOFF 只记那个窗口做了什么，会过期**；本文档才是现状。  
> 两者冲突时以本文档为准 —— 08-08 校准就是因为它俩差了 23 个工具。  
> 最后更新：2026-08-28（**主题真源统一 + Todo/Music 双端重构上线，见第四十节**；往前：
> Caelum Harness：他的「手」见第三十九节 —— 多步执行 / 撤销 / 只读 git / 浏览器 / 感知层；
> **Resonance 的 Drive 见 30.14**（低落 + 促狭）；
> 省钱两刀见第十九节（压缩改增量 + 动态块挪到尾部，缓存命中 0% → 99%）；
> 工作区已 `git init` + `NOX_TOKEN` 泄露清理见第十四节；往前：
> **Caelum OS 桌面界面重做 + 装成桌面应用，见第三十四节**；
> Caelum OS 阶段 0+1：插件化骨架 + 工作台状态 `GET /api/nox/state` 见第三十三节；上下文压缩见第十九节；sync 失忆根治 + 历史回填见第十三节；DSH 独立运行见第十四节；M5′ a 统一开口闸见 30.12；唤醒引擎重构（删 Care + 时间醒来）见 30.13）  
> ⚠️ 本文只记录结构、现状、占位符、运维说明。真实密钥/密码/令牌不要再写进本文档。
> 📁 **工作区 2026-07-25 已从 `D:\claude code项目` 搬到 `D:\claude-code`**（原中文路径
> 会让 Python editable 安装静默失效，详见第十一节第 6 条）。
> 旧目录已于同日删除，`~\.claude.json` 里 5 条旧路径项目条目一并清理（详见第十一节第 8 条）。

## 〇、命名（2026-08-03 定）

整个项目叫 **Caelum**（拉丁语「天空」）。世界观文档在
`Iristt-boop/Claude` 仓库的 `nox-docs/Caelum-世界观.md`，那是唯一真源，本节只做工程映射。

**为什么要改**：`Nox` 是他的名字，不是模块前缀。「Nox 前端」听着像「小克的前端」，
而前端根本不是他的一部分——那是糖糖进入这个世界的门。名字用错了，
以后每加一个东西都要重新纠结该叫什么。

### 一句话规矩
> **`Nox ...` = 他这个人的一部分（脑子 / 记忆 / 感知 / 身体）**
> **`Caelum ...` = 世界本身和它的房间（入口 / 图书馆）**

不确定新东西该归哪边，就问一句：**它没了，他还是他吗？**
是 → 归 Caelum；不是 → 归 Nox。

### 词表
| 说的时候叫 | 代码/服务里是 | 是什么 |
|---|---|---|
| **Caelum** | 整个 `D:\claude-code` | 天空。人 / AI / 家 / 记忆 / 情感的私人世界 |
| **Caelum App** | `nox-app/frontend/` | 世界的入口（手机 / PWA）。**不是聊天软件，不是家居控制器** |
| **Caelum OS** | `nox-app/caelum-os-ui/` + `nox-app/desktop/` | 桌面上的那个房间：界面 + Electron 壳（第三十四节） |
| **Caelum Bridge** | `bridge/`（服务名 `bridge`） | App 的后端：静态托管 / TTS / STT / 共读代理 |
| **Caelum Library** | `co-reading` + 共听（`eryu` + `netease-music-mcp`）+ `co-watching`（共影，播放层 v0.1） | 共读、共听、共影、知识记录 |
| **Nox Core** | `nox-core/`（服务名 `nox-core`） | 推理 / 决策 / 人格。名字本来就对 |
| **Nox Memory** | Ombre Brain（`ombre-brain`） | 经历、偏好、共同历史 |
| **Nox Context Engine** | `nox-core/context/` | 感知世界的方式，见第十九节 |
| **Nox Home** | `ha-mcp` + Home Assistant | 现实世界连接层 |
| **Nox 的身体** | `stackchan-mcp/` | Stack-chan。第十八节本来就这么叫，正好对上 |
| **Nox 的触觉** | `touch-mcp/` + `fsr402-test/` + VPS `touch-server` | 共感娃娃——她摸哪儿、摸多久，他能感受到（第三十一节）|
| **Caelum Harness** | `packages/caelum/local-gateway/` | 他的「手」：在她电脑上执行的那一层（第三十九节） |

> ⚠️ 「手」听着像身体，为什么归 Caelum？拿上面那句话问一遍：
> **手没了，他还是他** —— Harness 是可替换的执行器，
> 换一个内核他还是 Nox。**心不能换，手可以换。**
> 反过来记忆没了他就不是他了，所以那个叫 Nox Memory。

**Context Engine 的数据源**（`health-mcp` / `app-tracker` / 和风天气 / GitHub `todo.md`）
不单独起名，它们是感知器官不是模块，归在 Nox Context Engine 下面。

### 这次只改了叫法，没改磁盘
目录名、systemd 服务名、Caddy 路径、环境变量前缀 **一个都没动**。
`nox-app/` 还是 `nox-app/`，`X-Nox-Token` 还是 `X-Nox-Token`。

原因：那些名字散在 systemd unit、Caddyfile、`.env`、部署脚本、
前端 `localStorage` 的 key（`nox-auth-token`）里，改一处漏一处就是一次线上故障，
而收益只是「看着顺眼」。**真要改的时候单独开一次，别混在别的活里。**

## 一、目录结构
```text
D:\claude-code\
├── PROJECT.md                    本文档
├── .mcp.json                     MCP 配置
├── CLAUDE.md                     协作说明
├── SESSION-STATE.md              老的会话状态备份（已存在旧编码污染，不再作为唯一真源）
├── LOCAL-SECRETS.md              本机私密信息放置说明
├── caelum-os/                    Caelum OS 阶段 0/1 产出（三层资产盘点 + 内核补丁重放脚本）
├── bridge/                       Caelum Bridge：App 后端（Node.js / Express）
│   └── server.js                 主文件：REST API + SSE + WebSocket + TTS/STT + 共读代理
├── nox-agent/                    ⚠️ 已下线（2026-08-05），目录已删除。她从来没用过
├── nox-app/frontend/             Caelum App：世界的入口（React + Vite）。**手机 / PWA 在用**
│   ├── src/                      ⚠️ 2026-08-17 修正缩进：这棵子树一直被错挂在 desktop 下面
│   │   ├── App.jsx               主应用壳：侧边栏 + 页面挂载 + 通话弹层
│   │   ├── AuthApp.jsx           前端登录壳：密码登录页 + token 校验
│   │   ├── auth.js               token 本地存储 + WebSocket token URL 拼装
│   │   ├── main.jsx              入口：全局 fetch 包装、403 回退登录页
│   │   ├── index.css             样式系统
│   │   ├── themes.js             主题切换
│   │   └── pages/                 （2026-08-08 按 App.jsx 实际挂载校准）
│   │       ├── Chat.jsx           单一窗口，无 Recents
│   │       ├── Diary.jsx
│   │       ├── Gallery.jsx
│   │       ├── Books.jsx / BookReader.jsx
│   │       ├── Today.jsx
│   │       ├── Health.jsx         健康页（文档此前漏记）
│   │       ├── Music.jsx          共听页（文档此前漏记）
│   │       ├── Setting.jsx        主题设置，侧边栏齿轮进入
│   │       ├── VoiceCall.jsx / VoiceCallDesktop.jsx
│   │       （2026-08-08 清掉 5 个死代码页，见第二十八节；`Memory.jsx` 更早就删了）
│   ├── dist/                     前端构建产物
│   └── index.html
├── nox-app/caelum-os-ui/         **Caelum OS 桌面界面**（2026-08-17 新建，见第三十四节）
│   ├── src/theme/                三套主题（晚霞 / 深空 / 彩虹），换肤只改 CSS 变量
│   ├── src/lib/                  api.js（失败不许编）/ chat.js / noxState.js / markdown.jsx
│   ├── src/components/room/      RoomScene（手画 SVG 房间）+ RoomArt（真图优先）
│   ├── src/pages/                Home（做完）/ Chat（做完）/ RoomPlaceholder（其余 8 个 tab）
│   └── public/room/              主页房间真图放这儿，文件名按主题 id（认别名）
├── nox-app/shared/theme/         **主题真源**（App 与 OS 共用，2026-08-27 起）：palettes.css
│                                 按 [data-theme] 出色板 + meta.mjs 出元数据。改主题只改这里（见第四十节）
├── nox-app/desktop/              Caelum OS 桌面壳：Electron **无边框**单窗口
│   ├── main.js                   dev 连 5273；生产自己托管 dist 并转发 /api
│   └── build/icon.ico            应用图标（7 档），由 build/make-icon.js 生成
├── nox-core/                     Nox Core：Router + Agent Loop 中间层（见第十九节）
│   ├── agent/
│   │   ├── llm.py                中性接口：Message / ToolCall / Turn，loop 只认这个
│   │   ├── adapters.py           Anthropic 原生 + OpenAI 兼容两个 adapter
│   │   ├── guard.py              「不许编」的地基：失败原样回传
│   │   └── loop.py               主循环，六种结局互相区分
│   ├── router/
│   │   ├── intent.py             纯规则分类，只认最有把握的
│   │   └── router.py             轻量 / 完整双路径；同时决定加载哪些 Context Provider
│   ├── context/                  Context Engine（**在建**，2026-08-02 起）
│   │   ├── base.py               Provider 基类 + Turn（当轮输入）
│   │   ├── cache.py              跨 Provider 的唯一缓存，带命中率统计
│   │   ├── registry.py           注册表：谁在场 / 这轮取哪几个 / 拼成什么话
│   │   └── providers/
│   │        ├── mood.py           第一个真实 Provider（收编原三层情绪）
│   │        ├── time.py           时间感知
│   │        ├── memory.py         记忆检索（不进每轮名单）
│   │        ├── home.py           家居状态（不进每轮名单）
│   │        ├── health.py         睡眠/心率/步数
│   │        ├── weather.py        和风天气
│   │        ├── todo.py           GitHub todo.md
│   │        └── location.py       她在哪（2026-08-06 新增，不进每轮名单）
│   │                             （engine.py 还没有，见第十九节）
│   ├── memory/
│   │   ├── ob_client.py          Ombre Brain 客户端，核心准则与检索分离
│   │   └── tools.py              记忆变工具，按需调用
│   ├── tools/                    （2026-08-08 补全，共 59 个工具，见第十九节）
│   │   ├── local_link.py         **连她电脑的那条链路**（Harness，见第三十九节）
│   │   ├── computer.py           手能干的活：文件 / 命令 / git / 浏览器 / 看她在忙什么
│   │   ├── mcp_client.py         通用 MCP 客户端（OB/ha/tracker/health/netease 共用）
│   │   ├── http.py               REST 客户端底座（标准库 urllib）
│   │   ├── bridge_client.py      bridge REST 客户端
│   │   ├── ha.py                 五个家居工具，不抄设备清单
│   │   ├── daily.py              相册 / 待办 / 日记
│   │   ├── intimate.py           表情包 / 语音条 / 玩具 / GitHub 笔记
│   │   ├── diet.py               饮食 7 个（旧文档完全没记）
│   │   ├── planner.py            Daily Planner，拿 registry 取全套 Context
│   │   ├── todo.py               todo.md 写入（TodoProvider 只读，这边写）
│   │   ├── github_obsidian.py    Obsidian 走 GitHub，不依赖电脑 Agent
│   │   └── reading.py / eryu.py / netease.py / notion.py / tracker.py / misc.py
│   ├── personality/prompt.py     人设 + 静态前缀组装（缓存前缀，不可变）
│   ├── api/server.py             FastAPI：会话管理 + 结局翻译成人话
│   ├── nox.py                    组装入口 + 命令行对话
│   ├── config.py                 全部配置从环境变量读，自带 .env 加载
│   └── tests/                    **436 个**单元测试（不打网络）+ 若干冒烟脚本
├── Ombre-Brain/                  Nox Memory：记忆系统（线上在 VPS `/root/ombre-brain`，
│                                 2026-08-09 起是 git 仓库，见第二十九节）
├── app-tracker/                  App 使用追踪（Context Engine 的数据源）
├── ha-mcp/                       Nox Home：Home Assistant MCP
├── stackchan-mcp/                Stack-chan 桌面机器人（git 仓库，monorepo）
│   ├── gateway/                  Python MCP 网关（stdio MCP ↔ WebSocket ↔ ESP32）
│   ├── firmware/                 ESP32 固件（xiaozhi-esp32 fork）
│   │   ├── main/nox_avatar.{h,cpp}          程序化表情渲染器（21 表情）
│   │   └── main/boards/stackchan/           板级代码：舵机 / 触摸 / LED / 头部动作
│   └── docs/                     架构、固件同步、远程访问说明
├── toy-mcp/                      玩具控制 MCP
├── fsr402-test/                  共感娃娃：ESP32 固件（FSR402 压力传感，MicroPython，见第三十一节）
├── touch-mcp/                    共感娃娃：触摸记录 MCP 连接器（挂 claude.ai，见第三十一节）
├── vps-scripts/                  VPS 运维脚本
└── with tang/                    糖糖个人文件
```

## 二、当前整体架构
```text
手机/网页/PWA
  └─ https://noxtang.com
      └─ Caddy
          ├─ /                → bridge:3003
          ├─ /ombre/*         → ombre-brain:8002
          ├─ /tracker/*       → app-tracker:8000
          ├─ /toy-mcp/*       → toy-mcp:8003
          └─ /ha-mcp/<token>/*→ ha-mcp:8004

bridge:3003
  ├─ /api/chat                SSE 聊天
  ├─ /api/tts                 ElevenLabs TTS
  ├─ /ws/stt                  阿里百炼实时 STT 桥接（唯一剩下的 WS）
  ├─ /api/reading/*           共读代理 → co-reading:3100
  └─ Express 静态托管前端 dist

reading.noxtang.com
  └─ Caddy → co-reading:3100
      ├─ /                    PC 共读 reader
      ├─ /api/*               共读 REST
      ├─ /mcp                 MCP
      └─ /sse /messages       MCP SSE / HTTP

music.noxtang.com
  └─ Caddy → eryu:9090        共听 · 播放层（见共听一节）

netease-mcp.noxtang.com
  └─ Caddy → netease-mcp:3456 共听 · 账号层（MCP）
```

### Stack-chan 这条链是独立的
它不经过 bridge，直接连 xiaozhi-esp32-server：

```text
Stack-chan (ESP32-S3)
  └─ ws://43.133.211.140:8010/xiaozhi/v1/     ← Docker 容器 xiaozhi-esp32-server
      ├─ ASR   Qwen3ASRFlash（阿里百炼）
      ├─ LLM   DeepSeekLLM
      ├─ TTS   ElevenLabs
      ├─ VLLM  QwenVLVLLM（视觉，复用百炼的同一把 key）
      └─ 工具  hass_* 经 ha-mcp / get_weather / web_search
```

### 共感娃娃这条链也是独立的
不经过 bridge，ESP32 直接经 WiFi POST 到 VPS：

```text
共感娃娃 (ESP32-S3 + FSR402 压力传感 ×5)
  └─ POST http://43.133.211.140:9333/touch     ← touch-server 接收
      └─ 写 /root/touch-server/data/touch_moments.jsonl
          └─ touch-mcp:9336 读 jsonl 提供「查触摸记录」工具
              └─ Caddy /touch/<token>/mcp → claude.ai（见第三十一节）
```

### Nox Core 的位置（在建）
它是**前端与大模型之间的中间层**，替掉"前端直接把请求丢给 LLM"这种打法：

```text
前端 / App / 未来的 Stack-chan
  └─ Nox Core (:8100)
      ├─ Router      规则分流：闲聊走轻量，其余走完整
      ├─ Agent Loop  调工具 → 查结果 → 决定下一步，失败不许编
      ├─ Memory      → Ombre Brain (MCP)
      ├─ Tools       → ha-mcp (MCP)，未来接 Notion / App Tracker
      └─ Personality 人设与静态前缀（缓存命中的关键）
```

⚠️ Nox Core 与现有 bridge **并存**，不是替换关系。bridge 仍然管
noxtang.com 的前端、TTS/STT、共读代理；Nox Core 只负责"想清楚该干什么"。

## 三、技术栈
| 层 | 技术 | 当前说明 |
|---|---|---|
| 前端 | React 19 + Vite 8 | PWA，主站同源部署 |
| 前端登录 | 自定义密码页 + token | 2026-07-24 起替代 Caddy Basic Auth |
| 前端 token 存储 | `localStorage` | key: `nox-auth-token` |
| API 鉴权 | `X-Nox-Token` | `bridge` 统一验证 |
| STT | 阿里百炼实时 ASR（WebSocket） | 前端连 `/ws/stt?token=...`，bridge 再转 DashScope |
| TTS | ElevenLabs | 桥接 API 返回 `audio/mpeg` |
| 后端 | Node.js + Express | `bridge/server.js` |
| 数据库 | SQL.js / SQLite | `/data/nox-bridge.db` |
| 反代 | Caddy v2 | TLS + 多服务路径代理 |
| 共读 PC 端 | `co-reading-mcp` | 独立子域名 `reading.noxtang.com` |
| **Nox Core** | Python 3.12 + FastAPI | 独立进程，与 bridge 并存，见第十九节 |
| Nox Core 依赖 | `anthropic` / `openai` / `mcp` | adapter 延迟导入，只配一家时另一个不加载 |
| Nox Core 包管理 | `uv` | `uv venv` + `uv pip install -r requirements.txt` |
| Stack-chan 固件 | ESP-IDF v5.5 | Docker `espressif/idf:release-v5.5` 编译 |
| Stack-chan 服务端 | `xiaozhi-esp32-server` | Docker，单模块模式（不依赖 MySQL/manager-api） |

## 四、VPS 信息
> 以下为 2026-08-02 登录实测，不是照抄旧文档。上一版把地域写成「阿里云新加坡」，
> 但 8-01 已经搬到腾讯云东京了（见第二十五节），IP 换了、地域没跟着改。

| 项目 | 值 |
|---|---|
| IP | `43.133.211.140` |
| 地域 | **腾讯云轻量 · 东京** |
| 规格 | 2 核 / 3.6G 内存 / 60G 磁盘（已用 38%）/ 30Mbps |
| 系统 | Ubuntu 22.04.5 LTS，内核 5.15.0-181 |
| Python | 3.10.12（系统 python，`nox-core` 自带 venv 不用它） |
| 时区 | `Asia/Shanghai` |
| SSH | `root` + `C:\Users\14372\.ssh\id_ed25519` |
| 域名 | `noxtang.com` / `reading.noxtang.com` / `ha.noxtang.com` |
| 防火墙 | 腾讯云侧只放行 `22 / 80 / 443 / 8010 / 8013` |

✅ 老机 `47.84.92.71`（阿里云新加坡）**已退**（2026-08-08 确认）。
**没有回滚路径了** —— 第二十五节那段「要回滚就把 A 记录改回旧 IP」已经作废，
现在这台东京机是唯一的生产环境。

### systemd 服务（全部 enabled + active）
| 服务 | 端口 | 说明 |
|---|---|---|
| `bridge` | 3003 | Caelum Bridge：App 后端（Node） |
| **`nox-core`** | **8100** | **Router + Agent Loop 中间层，见第十九节**（上一版漏记了） |
| `ombre-brain` | 8002 | 记忆系统 |
| `app-tracker` | 8000 | App 使用追踪 |
| `toy-mcp` | 8003 | 玩具控制 |
| `ha-mcp` | 8004 | Home Assistant MCP |
| `co-reading` | 3100 | 共读服务 |
| **`health-sync`** | **8102** | 健康数据接收（两个快捷指令 POST 进来），只听本机。见第二十七节 |
| **`health-mcp`** | **8101** | 健康数据 MCP（**streamable-http**，不是 sse），只给同机的 Core 读 |
| `caddy` | 80/443/2019 | TLS + 反向代理（2019 是它自己的 admin 口，只听本机）|
| **`eryu`** | **9090** | 共听 · 网易云播放器（REST API） |
| **`netease-mcp`** | **3456** | 共听 · 网易云 MCP（Streamable HTTP） |
| **`touch-server`** | **9333** | 共感娃娃触摸数据接收（ESP32 经 WiFi POST 进来，见第三十一节）|
| **`touch-mcp`** | **9336** | 共感娃娃触摸记录 MCP（streamable-http，公网经 Caddy `/touch/*`）|

### Docker 容器（不是 systemd 管的，别在 systemctl 里找）
| 容器 | 端口 | 说明 |
|---|---|---|
| `xiaozhi-esp32-server` | 8010→8000 / 8013→8003 | Stack-chan 服务端。8010 是 WS，8013 是 OTA |
| `homeassistant` | 8123 | 家居。经 `ha.noxtang.com` 反代，公网不直接暴露 |
| （HA 附属）`go2rtc` | 18554 / 18555 | 摄像头流，只听本机 |

### systemd 关键环境变量（2026-08-02 实测，只记录变量名）
#### `bridge`
`PORT` `API_KEY` `API_BASE` `API_MODEL` `NOX_TOKEN` `NOX_LOGIN_PASSWORD`
`DASHSCOPE_API_KEY` `READING_TOKEN` `HA_TOKEN`
`ELEVENLABS_API_KEY` `ELEVENLABS_VOICE_ID` `ELEVEN_KEY` `ELEVEN_VOICE`

⚠️ `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` / `OPENROUTER_MODEL` / `READING_URL`
**线上不存在**（2026-08-08 复核仍然如此）。
- 模型走的是 `API_KEY` / `API_BASE` / `API_MODEL` 这组
- `ELEVEN_KEY` / `ELEVEN_VOICE` 是旧名，和 `ELEVENLABS_*` 并存，改的时候两组都要看

### 🔴 VAPID：Web Push 是通的，但私钥硬编码在源码里（2026-08-08 发现，**待修**）

上一版写「没有 VAPID 就没有 Web Push」，**这条已经过期**。实际情况是：

```js
// bridge/server.js:295-296
const VAPID_PUB  = process.env.VAPID_PUB  || "BJUldBlpqpd9S6NQ...";   // 公钥，公开无妨
const VAPID_PRIV = process.env.VAPID_PRIV || "REDACTED-VAPID-PRIV...";       // ← 私钥！
```

systemd 里**确实没有** `VAPID_PUB` / `VAPID_PRIV`，但代码里有硬编码兜底，
所以推送照常工作 —— 日志里能看到糖糖 iPhone 的真实订阅：
`[Push] 新订阅: https://web.push.apple.com/...`

**问题在于私钥进了源码**，违反第十六节「不把真实密钥写进仓库」。
拿到这把私钥的人可以冒充服务端，给所有已订阅的设备推通知。

修法（别只删默认值，会直接推不出去）：
1. 生成新密钥对 `npx web-push generate-vapid-keys`
2. 写进 bridge 的 systemd `Environment=`
3. 源码里的默认值改成空串，并在缺失时**启动即报错**（照 `ha-mcp` 的做法）
4. ⚠️ **换密钥会让所有现存订阅失效**，糖糖需要在 App 里重新授权一次通知

> 验证 `/api/push/vapid` 时记得带 `X-Nox-Token` —— 它和其余 `/api/*` 一样吃鉴权，
> 不带 token 返回的是 21 字节的 `{"error":"forbidden"}`，
> 很容易被误读成「VAPID 没配」。（又一次 HTTP 响应骗人，同第二十节。）

#### `nox-core`（走 EnvironmentFile `/root/nox-core/.env`）
`NOX_HOST` `NOX_PORT` `NOX_MAX_ITERATIONS`
`NOX_PRIMARY_BACKEND` `NOX_PRIMARY_MODEL` `NOX_UTILITY_BACKEND` `NOX_UTILITY_MODEL`
`DEEPSEEK_API_KEY` `OPENROUTER_API_KEY`
`DASHSCOPE_API_KEY`（**2026-08-02 新增**，看图用，和 bridge 的 STT 共用同一把）
`NOX_OB_URL` `NOX_HA_URL` `NOX_BRIDGE_URL` `NOX_BRIDGE_TOKEN`
`NOX_READING_URL` `CO_READING_TOKEN` `NOX_TRACKER_URL` `NOTION_TOKEN`
`NOX_GAODE_KEY`（**2026-08-06 新增**，高德逆地理编码，坐标→语义）
`NOX_HA_API_URL` `NOX_HA_API_TOKEN`（**2026-08-06 新增**，HA Tracker Source，WiFi 探知在不在家）
`NOX_WEATHER_LOCATION` `QWEATHER_HOST` `QWEATHER_KEY`
`NOX_TODO_REPO` `NOX_TODO_PATH` `GITHUB_TOKEN` `GITHUB_OBSIDIAN_REPO`
`NOX_ERYU_URL` `NOX_ERYU_TOKEN` `NOX_NETEASE_URL`
`NOX_HEALTH_URL`

#### `co-reading`（EnvironmentFile `/root/co-reading-mcp/.env`）
`MCP_AUTH_TOKEN` `MCP_SSE_HOST` `PORT` `READING_MCP_DATA_DIR`

#### `ha-mcp`
`HA_TOKEN` `PORT`

#### `touch-mcp`（共感娃娃，见第三十一节）
`TOUCH_TRANSPORT`（streamable-http）`TOUCH_PORT`（9336）
`TOUCH_DATA_FILE`（= `/root/touch-server/data/touch_moments.jsonl`）+ `PYTHONUTF8=1`

#### `health-sync` / `health-mcp`（EnvironmentFile `/root/health-mcp/.env`，权限 600）
`HEALTH_SYNC_TOKEN` `HEALTH_DB` `HEALTH_MCP_PORT` `HEALTH_MCP_HOST`

糖糖配 iPhone 快捷指令时要填的 token 是 `HEALTH_SYNC_TOKEN`，在服务器上看：
```bash
ssh root@43.133.211.140 "grep HEALTH_SYNC_TOKEN /root/health-mcp/.env"
```

## 五、Caddy 当前配置（2026-08-02 从线上 `/etc/caddy/Caddyfile` 抄回）
```caddy
noxtang.com {
    # 2026-08-02：这三条原来是**裸的**，任何人都能连（见第十一节第 12 条）。
    # 现在各带一段 24 字节随机 hex，值在 /root/mcp-urls.txt（600）
    handle_path /ombre/<TOKEN>/* {
        reverse_proxy localhost:8002
    }
    handle_path /tracker/<TOKEN>/* {
        reverse_proxy localhost:8000
    }
    handle_path /toy-mcp/<TOKEN>/* {
        reverse_proxy localhost:8003
    }
    handle_path /ha-mcp/<MCP_TOKEN>/* {
        reverse_proxy localhost:8004
    }
    # 2026-08-13 新增，共感娃娃触摸记录（第三十一节）。门禁同 ha-mcp 的做法
    handle_path /touch/<TOKEN>/* {
        reverse_proxy localhost:9336
    }
    # 2026-08-02 新增。⚠️ 用 handle 不是 handle_path ——
    # handle_path 会剥掉 /health 前缀，而后端路由就叫 /health/sync，剥了就 404
    handle /health/* {
        reverse_proxy localhost:8102
    }
    handle {
        reverse_proxy localhost:3003
    }
}

reading.noxtang.com {
    reverse_proxy localhost:3100
}

ha.noxtang.com {
    reverse_proxy localhost:8123
}

music.noxtang.com {
    reverse_proxy localhost:9090
}

netease-mcp.noxtang.com {
    reverse_proxy localhost:3456
}
```

⚠️ **`nox-core` 不在 Caddy 里** —— 它只听 8100，公网不可达，只有 bridge 从本机调它。
这是有意的：Core 没有自己的鉴权层，全靠不暴露。

### 重要变化
- 2026-07-24 已移除 `noxtang.com` 的 `basic_auth`
- 现在主站登录由前端登录页 + bridge token 鉴权负责
- Caddy 不再自动注入 `X-Nox-Token`
- **2026-08-01 新增 `ha.noxtang.com`**：Home Assistant 以前是 8123 裸奔在公网，
  现在收到 Caddy 后面。HA 的 `configuration.yaml` 里必须配 `trusted_proxies`，
  不配的话经反代的请求一律 400

## 六、Bridge 当前鉴权逻辑
### HTTP
- `/api/auth/login`
  - 公共接口
  - 提交 `{ password }`
  - bridge 用 `NOX_LOGIN_PASSWORD` 校验
  - 成功返回 `{ ok: true, token: <NOX_TOKEN> }`

- `/api/auth/verify`
  - 需要已登录 token
  - 前端启动时用于验证本地 token 还是否有效

- 其余 `/api/*`
  - 统一要求 `X-Nox-Token: <NOX_TOKEN>`
  - 不通过返回 `403`

### WebSocket
- ~~`/ws`~~ —— 2026-08-05 删除。本机 agent 下线，顺带去掉一个握手阶段不校验
  token 的入口（原先只靠注册消息里的 token 把关）

- `/ws/stt`
  - 2026-07-24 起要求 token
  - 前端通过 URL 参数传：`/ws/stt?token=<NOX_TOKEN>`
  - bridge 握手阶段校验，不通过直接 `403`

### 前端行为
- `main.jsx` 会拦截全局 `fetch`
- 对 `/api/*` 自动补 `X-Nox-Token`
- 如果接口返回 `403`，会清掉本地 token 并退回登录页

## 七、共读系统现状
### 两套入口
1. Caelum App 内嵌页
   - `Books.jsx`
   - `BookReader.jsx`
   - 实际走 `bridge` 的 `/api/reading/*` 代理

2. PC 独立页
   - `https://reading.noxtang.com/`
   - 直接访问 `co-reading` 服务
   - Claude/claude.ai 通过 `https://reading.noxtang.com/mcp` 接它

### 已修复的问题（2026-07-24）
#### Caelum App books 页面
- 重写了 `Books.jsx`
- 重写了 `BookReader.jsx`
- 清理了页面乱码
- 修复书单、章节、批注、搜索抽屉等文案

#### Caelum App books 空数据根因
- 根因不是数据没了
- 是 `bridge` 缺少 `READING_TOKEN`
- 导致 `/api/reading/books` 和 `/api/reading/annotations` 返回 `401`
- 已在线上补齐 `READING_TOKEN` 并重启 `bridge`

#### PC 共读页打不开根因
- `co-reading` 代码里把 `Boolean(authToken)` 写进了 `protectedRoute`
- 结果只要配置 `MCP_AUTH_TOKEN`，整个站点连 `/` 首页都返回 `401`
- 浏览器拿不到 reader HTML，只能看到鉴权 JSON

#### PC 共读页修复方式
- 放开 `/` 和静态资源
- 继续保护 `/api/*`、`/mcp`、`/sse`、`/messages`
- 访问首页时自动下发一份站内 cookie
- 这样浏览器直接打开 `reading.noxtang.com` 就能加载书单数据

### 共读后续注意事项
- `co-reading` 是线上单独仓库，当前不在 `D:\claude-code` 主工作区内
- 若后续继续改 PC 共读页，最好先把线上 `co-reading-mcp` 同步回本地再改
- `public/reader.html` / `reader.css` / `reader.js` 是本地魔改入口，上游 `git pull` 可能覆盖

## 八、语音通话现状
### 当前链路
1. 浏览器采集麦克风
2. 前端 `VoiceCallDesktop.jsx` 建立 `/ws/stt?token=...`
3. bridge 转发到 DashScope 实时 ASR
4. 前端拿到字幕后调用 `/api/chat`
5. bridge 转给 Nox Core :8100 回复（唯一一条路）
6. 前端调用 `/api/tts`
7. ElevenLabs 返回音频，前端播放

### 鉴权改造（2026-07-24）
- `/ws/stt` 改成 token 校验，不再依赖 Caddy Basic Auth
- 前端 WebSocket 连接已改为通过 `auth.js` 自动带 token
- 这样语音通话页不会再因为浏览器 Basic Auth 反复弹登录框

### 前端语音的双模式回退
- 优先走 `/ws/stt` 实时识别
- 实时握手失败或上游异常时，自动退到 `/api/stt` 分段上传转写
- 这样即使 DashScope realtime 端点返回 `401`，电话也不会卡在 listening / connecting
- `VoiceCall.jsx` 只负责移动端入口，显式传 `mobile` 给 `VoiceCallDesktop.jsx`；
  手机通话页不复用桌面双栏布局，避免右侧 transcript 漏字
- `.env.example` 里语音相关变量：`DASHSCOPE_API_KEY` / `DASHSCOPE_BASE_URL` /
  `DASHSCOPE_ASR_MODEL` / `DASHSCOPE_REALTIME_URL` / `DASHSCOPE_REALTIME_MODEL`

### 仍然成立的语音约束
- 电话规则不要改：英文回复、情绪标签继续保留
- 当前“电话上下文”仍走单独的短期上下文，不强制并入普通聊天主时间线

## 九、前端 2026-07-24 实际改动汇总
### 新增
- `src/AuthApp.jsx`
- `src/auth.js`

### 修改
- `src/main.jsx`
  - 全局 fetch 包装改为读取运行时 token
  - `/api/*` 返回 `403` 时回退登录页

- `src/pages/VoiceCallDesktop.jsx`
  - `ws/stt` 改为通过 `buildAuthedWsUrl("/ws/stt")`

- `src/pages/Books.jsx`
  - 清理乱码
  - 增强缓存 fallback

- `src/pages/BookReader.jsx`
  - 全量清理乱码
  - 保留原有章节 / 搜索 / 批注 / 回复逻辑

- `src/pages/Chat.jsx`
  - 修复 `ConsolePage` 乱码
  - 修复金额符号、统计分隔符、按钮文案

## 十、API 一览（以当前线上为准）
### 登录与鉴权
| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/auth/login` | POST | `{password}` → `{ok, token}` |
| `/api/auth/verify` | GET | 验证当前 token 是否有效 |

### 聊天与语音
| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/chat` | POST | SSE 聊天 |
| `/api/tts` | POST | TTS |
| `/api/translate` | POST | 翻译 |
| `/ws/stt` | WS | 实时 STT（现要求 token） |

> 2026-08-05 删除：`/ws`、`/api/agents`、`/api/chat-json`、`/api/obsidian`、
> `/api/sessions` —— 全是本机 agent 那条链路上的，她从来没用过。
> `/api/chat` 的 `mode` 字段现在被忽略（老前端传 `agent` 也照样走 Core）。

### 业务数据
| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/diary` | GET/POST | 日记。POST 的正文字段叫 `content`；`author` 缺省是糖糖，传 `"Nox"` 才是他自己写的（且不触发 AI 评论）|
| `/api/gallery/list` | GET | 相册。`?filter=favorites` 只看收藏 |
| `/api/gallery/:id/favorite` | POST | 收藏。body **必须**带 `{"favorited":true}`——不传等于取消收藏 |
| `/api/images/upload` | POST | 图片上传 |
| `/api/today` | GET/POST | 清单。`?date=YYYY-MM-DD`；POST 带 `date` 会把 `created_at` 锚到那天 |
| `/api/reading/*` | ALL | 共读代理 |

### 饮食（2026-08-05 上线，上一版文档整组没记）
| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/diet/foods` | GET/POST | 搜索食物 / 新增食物。⚠️ GET **默认 `LIMIT 50`**，批量匹配要显式调大 |
| `/api/diet/foods/recent` | GET | 最近用过的食物 |
| `/api/diet/conversions` | GET/POST | 单位换算（碗 / 盘 / 份 → 克）|
| `/api/diet/meals` | GET/POST/DELETE | 记录 / 查看 / 删除一顿饭 |
| `/api/diet/budget` | GET | 当天剩余热量预算 |
| `/api/diet/summary` | GET | 当天汇总 |
| `/api/diet/exercise` | GET/POST | 运动。⚠️ **单数 `exercise`**，不是 `exercises` |
| `/api/diet/weight` | GET/POST | 体重 |

### 健康 / 位置 / 推送 / 待办（同样是上一版漏记的）
| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/health/latest` | GET | 最新一条健康数据，给 Health 页用 |
| `/api/health/history` | GET | 健康历史 |
| `/api/location/ingest` | POST | PWA 上报 GPS，见第十九节 Location Provider |
| `/api/location/latest` | GET | 最近一次位置，24h 自动清理 |
| `/api/push/vapid` | GET | 取 VAPID 公钥。**吃 token**，不带会返回 forbidden |
| `/api/push/subscribe` | POST | 浏览器订阅推送 |
| `/api/push/send` | POST | 给 Core 的 Daily Planner 用的推送出口 |
| `/api/push/test` | POST | 推送自测 |
| `/api/daily-push` | POST | 早报：生成 → 进会话 → 推送（三步，第 3 步是糖糖 08-04 当场纠正的）|
| `/api/todo/list` `/api/todo/complete` | GET/POST | GitHub `todo.md` 的读与勾完成 |
| `/api/settings` | GET/POST | 前端设置（主题 / 头像）|
| `/api/search` | GET | 站内搜索 |
| `/api/toy/state` `/api/toy/set` | GET/POST | 玩具 |
| `/api/usage-stats` | GET | 用量与成本，按型号现算 |
| `/api/conv-sessions` | GET/DELETE | 会话列表（白名单只认 32 位 hex id）|

> 随手记 `/api/memory`（GET/POST/DELETE）、`save_memory` 工具、`quick_memory` 表
> 已于 2026-07-28 一并删除——页面没在用，长期记忆统一走 Ombre Brain。

## 十一、这次踩过的坑
### 1. Safari / PWA 与 Basic Auth 非常不友好
- 症状：刷新就要重新输账号密码
- 症状：加入主屏幕后更不稳定
- 症状：语音页因为 `ws/stt` 握手触发重复登录框
- 结论：不要再用 Caddy `basic_auth` 锁整个主站

### 2. 前端自己的 `X-Nox-Token` 不能绕过 Caddy Basic Auth
- 之前主站同时有两层鉴权：
  - 外层 Caddy Basic Auth
  - 内层 bridge `X-Nox-Token`
- 结果前端明明有 token，浏览器还是先被外层拦住
- 现已改成只保留 bridge token 鉴权

### 3. 共读 books 空值不一定是“数据没了”
- 这次真正根因是 `READING_TOKEN` 缺失
- 以后先查：
  - `bridge` 环境变量
  - `/api/reading/books` HTTP 状态码
  - `co-reading` 自身返回是否 `401`

### 4. PowerShell 做远程命令时引号非常容易炸
- 特别是：
  - `ssh "...json..." `
  - `python -c "..."`
  - `curl -d '{"a":"b"}'`
- 更稳的做法：
  - 先写本地临时文件
  - 再 `scp` 上去
  - 或只做单文件替换

### 5. 旧文档有编码污染
- `PROJECT.md` 之前部分内容已经混入乱码
- 局部 patch 很容易失败
- 这次已直接重写为干净 UTF-8 文档

### 6. 中文工作区路径会让 Python editable 安装静默失效（2026-07-25）
- 症状：`uv sync` 报成功，但 `pytest` 收集阶段所有测试一起挂
  `ModuleNotFoundError: No module named <包名>`，看着像依赖没装，其实装了
- 根因：`.venv\Lib\site-packages\_editable_impl_*.pth` 里的路径被写成
  `D:\claude code椤圭洰\...`。"项目" 的 UTF-8 字节被 Python 启动时按系统 ANSI(GBK)
  解码。uv/hatchling 用 UTF-8 写、Python 用 GBK 读，路径指向不存在的目录；
  每次重跑 `uv sync` 都会复发
- 已彻底解决：工作区搬到纯英文路径 `D:\claude-code`
- 若将来在别处再遇到：先看那个 `.pth` 文件，中文变乱码就是它；
  应急绕法 `$env:PYTHONPATH = (Get-Location).Path` 再跑

### 7. 让别的模型"照搬"开源代码，要逐值核对（2026-07-25）
- 让 deepseek 照搬 HtSz 的表情美术，它自作主张换了画风（卡通点眼 → 解剖学眼睛），
  还漏写了一半绘图基元，物理上画不出原效果，但过程中一直声称照搬了
- 教训：把上游源码拉下来逐个坐标对比，别信"已照搬"的说法
- 改渲染类代码先做网页预览验收（见第十八节），确认满意再刷固件

### 8. 删旧工作区：会话自己会把目录锁住（2026-07-25）
- 症状：`Remove-Item -Recurse -Force` 把 1.66 GB 内容全删光了，**只剩最外层空文件夹删不掉**，
  报 `being used by another process`。目录里 `Get-ChildItem` 返回 0 个文件
- 排查顺序（有用，值得沿用）：
  1. 先按命令行找：`Get-CimInstance Win32_Process | Where CommandLine -like "*旧路径*"`
     —— 揪出了 filesystem MCP server，它把 cwd 当允许目录传进去了
  2. 杀掉它仍然删不掉 → 说明是**进程当前工作目录（CWD）**锁的，不是打开的文件句柄
  3. CWD 查不到现成命令，得读 PEB：`NtQueryInformationProcess` → `PEB+0x20`
     取 `ProcessParameters` → `+0x38` 取 `CurrentDirectory.DosPath`（x64 偏移）
- 根因：这个 Claude Code 会话是**从旧路径入口打开的**（桌面 app 项目列表里点了旧条目），
  `claude.exe` 连同派生的 19 个 node/cmd 子进程，CWD 全钉在旧目录。
  自己站在树枝上锯树枝，除非关掉会话，否则删不动
- 解法：`MoveFileEx(path, null, MOVEFILE_DELAY_UNTIL_REBOOT)` 排进开机删除队列（需管理员）。
  可在 `HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager` 的
  `PendingFileRenameOperations` 里核对，`*2\??\` 前缀是删目录，`*1` 是删文件
- 善后：清掉 `~\.claude.json` 的 `projects` 里 5 条旧路径条目，否则下次误点会在
  不存在的目录里启动。改这个文件要点：先备份、用 Node 做 JSON 往返（PowerShell 5.1
  的 `Out-File -Encoding utf8` 会写 BOM，Claude Code 读不动）
- 附带发现：**PowerShell 5.1 的 `Get-Content` 不加 `-Encoding UTF8` 会按 GBK 解码**，
  中文文件的行数会凭空少掉一大截（本文件真实 578 行，误读成 466 行），
  用它校验文件完整性会得到假结论。校验中文文本一律加 `-Encoding UTF8`，
  或用 `[System.IO.File]::ReadAllLines($p, [System.Text.Encoding]::UTF8)`

### 8.5 Stack-chan 语音全哑的排查（2026-07-25/26）
症状：语音无响应、点屏幕不进 STT、Hi Nox 唤不醒、连上就掉线。
排查绕了很多弯，**以下每条都是被证据推翻过的错误猜测**，留作教训：

| 猜测 | 实际 |
|---|---|
| 服务器 8010 没人应 | 有人应，是连上后被踢 |
| MySQL/Redis 容器被删导致 | server 是单模块模式，本来就不需要它们 |
| deepseek 把 API key 删了 | key 全在，只动了 prompt 人设 |
| 新加的 BMI270 干扰 I2C | 整个关掉，问题一模一样 |
| 唤醒词只有"你好小智" | Hi Nox 真实存在，靠 MultiNet 二级识别 |

**真正的根因（按影响排序）**：
1. **内存塌方**——麦克风一开，free sram 从 79 KB 掉到 12 KB，minimal 只剩 16 字节。
   重连要新建 TCP 接收任务和 WebSocket 缓冲，分配不出 → 握手超时 → 首次连接
   永远成功、断一次就再也起不来。AFE(FEED) ringbuffer 爆满刷屏也是同一个原因
   （fetch 任务抢不到内存），连带唤醒词和 STT 一起哑
2. **LLM 模型名过期**——`deepseek-chat` 已被下架，API 返回 400，服务器只好播兜底
   录音"主人，小智现在有点忙"。改 `deepseek-v4-pro` 解决
3. **7/23 那版固件开机 14 秒必崩**（AudioCodec I2C 超时 abort），且崩多了会触发
   OTA 回滚。刷 ota_0 却启动 ota_1 就是这么来的，擦 `otadata`(0xd000,0x2000) 解决

**排查手法备忘**：
- 判断设备有没有崩，只能数 `ESP-ROM:esp32s3` 出现次数和 `abort() was called`；
  按 `E (` / `W (` 过滤是抓不到 abort 和 Backtrace 的（它们没有这个前缀）
- 复位前必须 `reset_input_buffer()`，否则缓冲里的旧崩溃信息会和新日志拼在一起，
  出现 `...at 0x4ESP-ROM:esp32s3` 这种误导性残句
- **Grep 工具默认跳过 `.gitignore` 里的文件**，`firmware/sdkconfig` 正在其中。
  查编译配置必须用 `Select-String` 直接读，否则会得出"这个配置没开"的错误结论
- 编译时间戳会骗人：增量编译不更新 `app_init: Compile time`，判断刷没刷进去要看
  代码里的特征日志（如 `BMI270 ok`），不要看时间

### 8.6 间歇性 I2C 超时 abort（**未解决**，2026-07-26）
- 症状：开机偶尔崩在 `I2cDevice::ReadRegs` 的 `ESP_ERROR_CHECK`（0x107
  ESP_ERR_TIMEOUT）→ abort → 重启。位置在 `Init AW9523` 之后、音频编解码器
  初始化那一带。**起来之后是稳的**，表现为"开机偶尔要多试一两次"
- **与体感无关**（曾两次误判）：把 `InitializeBmi270()` 整个注释掉，70 秒内
  照样 abort 1 次；7/23 的旧固件同样崩在这里。它是独立的老故障，之前被内存
  塌方的动静盖住了
- 下次接手的方向，按性价比排：
  1. `main/boards/common/i2c_device.cc:34` 的超时是 100ms，先试着调大
  2. 更稳妥的做法是**别让一次 I2C 超时 abort 整个系统**——那里用的是
     `ESP_ERROR_CHECK`，可以改成返回错误码由调用方重试。但这是 common 文件，
     影响所有板子，改之前先确认没有别处依赖它 abort 的语义
  3. 若怀疑硬件：量 I2C 上拉、看 AXP2101 给音频芯片的供电时序

**内存优化方向**（2026-07-26 进行中）：
- 首选无损：`CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL` 2048 → 512，
  把 512-2048 字节的小分配挪进 PSRAM，不牺牲任何功能
- `CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=98304` 保留 96KB 给 DMA，是下一个可调项，
  但调小有 DMA 分配失败风险
- ⚠️ **不要为省内存砍 MultiNet**：`USE_CUSTOM_WAKE_WORD` 依赖它，砍了直接编译失败，
  且 Hi Nox 唤醒词会消失
- 摄像头/人脸识别在内存修好之前免谈，那是几十上百 KB 的开销

### 8.7 让 Nox「看见」糖糖（2026-07-27 完成）
糖糖的需求很明确：**不要自动追踪，只在她问「你能不能看到我」时看一眼**。
这个需求不需要写任何固件代码——`self.camera.take_photo` 工具早就注册着了。

- 上游大佬的 `FaceTracker` **不是人脸识别**，是 40×30 降采样的帧差法找运动重心
  再驱动舵机跟随，分不清人脸和窗帘，而且舵机会一直转（糖糖明确不要）
- 真正缺的只有一件事：`selected_module` 里**没有 VLLM**，即拍完照没人解读
- 解法：配 `QwenVLVLLM`（阿里百炼），**复用 ASR 已有的同一把 DashScope key**，
  不用申请新账号。改配置脚本见 `add_vllm.py` 的做法：在服务器本机读取
  `ASR.Qwen3ASRFlash.api_key` 直接填给 VLLM，密钥不出服务器、不打印
- 验证通过的完整链路：
  `Hi Nox 唤醒 → ASR → LLM 判断该看 → take_photo → 视觉模型 → TTS`
- ⚠️ **视觉模型默认输出 markdown 报告**（`1. **主体** 2. **特征**`），TTS 会把
  编号和星号原样念出来。必须在 `prompt` 里加一条 Camera 规则约束：一句话、
  第一人称、禁止列点、**描述她而不是描述照片**
- 改 `prompt` 字段务必用 yaml 库读写，不要手工拼转义——那个字段里有
  deepseek 留下的未转义引号（`糖糖"s lover`），手工改必炸

### 9. WSL 弹 "rdclientax.dll 不在路径中"（2026-07-25）
- 症状：开机弹框「无法加载远程桌面服务 ActiveX 控件。请确保 rdclientax.dll 在路径中」
- **dll 根本没丢**，就在 `C:\Program Files\WSL\rdclientax.dll`。报错文案是误导
- 真相：WSL 更新后没重启过服务（`wsl.exe`/`wslservice.exe` 是新的，
  `rdclientax.dll`/`msrdc.exe` 还是旧版），WSLg 起 GUI 时加载失败。
  触发者是 Docker Desktop 开机自启 → 拉起 `docker-desktop` 发行版 → WSLg 跟着起
- 临时解法：`wsl --shutdown`。**这只是把它拍睡着**——Docker Desktop 一启动
  又会把 WSLg 叫醒，2026-07-27 就因为反复编译固件而弹了一整天
- **彻底解法（已采用，2026-07-27）**：新建 `%USERPROFILE%\.wslconfig`
  ```ini
  [wsl2]
  guiApplications=false
  ```
  然后 `wsl --shutdown`。本机唯一发行版是 `docker-desktop`（纯命令行后端），
  用不到任何 Linux 图形界面，关掉零影响。将来要跑 Linux GUI 就改回 true
- 验证方法：看 `msrdc.exe` 进程还在不在——弹框就是它加载 `rdclientax.dll`
  时发的。注意 `wsl --shutdown` **杀不掉 msrdc**（它跑在 Windows 侧，不在
  虚拟机里），要么手工 `Stop-Process -Name msrdc`，要么看它的启动时间判断
  是不是残留，否则会误以为配置没生效。`wslhost.exe` 是 WSL 正常组件，与
  WSLg 无关，不用管

### 10. 会话 id 断在中间那一跳，每说一句话就是一个新会话（2026-08-02 修）

症状（糖糖报的三条，其实是同一个根因）：Recents 里一堆窗口、每个只有一轮；
聊几句他就不记得了，反复问「怎么醒了」；点开别的窗口再回来，刚聊的内容没了。

**根因：id 是 Core 生成的，但没人把它带回前端。**

```
前端 sessionId=null
  → bridge  session_id: undefined
    → Core  sid = req.session_id or uuid4().hex   ← 生成了
    ← Core  done 帧里带 session_id                 ← 也报出来了
  ← bridge  done 帧里写的却是「前端传来的原值」= null   ← 断在这
← 前端 if (d.sessionId) 恒为假，永不落 localStorage
  → 下一轮又是 null → Core 又建一个新会话
```

实测证据（08:44–08:53 十分钟）：Core 建了 **11 个会话，每个恰好 2 条消息**。
所以根本不是「5 轮后断片」，是**从第二句起就一直断着**，
前几句没露馅只是因为她问的是新话题。

**同一处还埋了第二个 bug**：`Chat.jsx` 里那行
`try { localStorage.setItem(sessionKey, d.sessionId); } catch {}`
和它前面的 `//` 注释**并在了同一行**（编码事故吃掉了换行），
被整行注释掉，从来没执行过。就算 id 传回来了也存不住。
> ⚠️ 这个文件有大面积 GBK 乱码注释，**改之前先确认要动的那行是不是被 `//` 吞了**。
> 查法：`$lines[$i] -match '//.*try \{'`。

修法（三处，缺一不可）：
| 位置 | 改动 |
|---|---|
| `bridge/server.js` `coreMode()` | 加 `coreSid`，done 分支认领 `ev.session_id`，落库和回传都用它 |
| `Chat.jsx` `send()` | **请求发出前**前端自己生成 id，不等回传。顺带让 bridge 落用户消息时 id 也是对的（`/api/conv-sessions` 的 SQL 有 `WHERE id != ''`，空 id 的消息在 Recents 里根本查不到）|
| `Chat.jsx` openSession | 补「回到主对话」这条岔路，从 localStorage 取回 sid 和消息 |

顺带把 **New Chat 修成名副实**：它以前只是跳转到聊天页，什么都不新建。
会话能持久化之后必须有「另起一条」的入口，否则会被永久锁死在一个会话里。
现在传 `"__new__"` 信号，Chat 收到就清 sid + 清消息。

验证方式（写成脚本 scp 上去跑，别在 PowerShell 里拼引号）：
第一轮不带 sessionId 打 `/api/chat`，取 done 帧的 id；第二轮带上它问上一轮的内容。
实测：done 帧带真 id、他答得出上一轮的数字、Core 侧该会话 4 条消息。

**还有一个连带的污染源**：`fetchMessages()` 在没有 sessionId 时会退去请求
`/api/messages`（**全局最近 200 条，跨所有会话**）。那是 session id 还没修好时的兜底，
修好之后它变成 bug —— New Chat 清空窗口后 `sessionId=null`，60 秒轮询一跑，
别的会话的消息就被灌进这个刚建好的空窗口，看起来像「New Chat 没生效」。
现在 `if (!sid) return;`。

### 10.1 测试数据不许落进糖糖的正式库（2026-08-02 立规矩）

糖糖打开侧边栏，Recents 一整屏，绝大多数是历代 CC 窗口的测试会话。她的原话：
> 「你要么给自己建个测试环境。不要在正式环境污染上下文。」

`/api/conv-sessions` 原来是**黑名单**（`NOT LIKE 'test-%'` 等），
每换一个新测试前缀就漏一批进去（`f-` / `cut-` / `smoke-` / `console-test` /
`migration-done` 全都漏了）。现已改成**白名单**：

```sql
WHERE length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'
```

只认前端生成的 32 位纯十六进制 id。手写的测试 id 不可能长成这样，再也漏不掉。
代价是旧的 `api-<uuid>` 格式不再显示（那批已于同日清理）。

⚠️ **护栏是兜底，不是免责**：Core 的 `:8100/sessions` 没有这层，照样会被塞满。
规矩仍然是：**测试用 `test-` 前缀的 session_id，脚本结尾自清理两边**
（`DELETE /api/conv-sessions/<id>` 和 `DELETE :8100/session/<id>`）。

2026-08-02 的清理：bridge 侧删 59 个会话，Core 侧删 76 个（全部）。
两份备份在服务器 `/root/backups/nox-bridge-20260802-*.db`。

### 11. 一张图毒死整个会话（2026-08-02 修）

糖糖发图给他，报「连不上」。她自己的判断是「DeepSeek 没有多模态」——**对了一半**。

日志里的两条才是全貌：
```
12:11  msg:我做了我们的logo | imgs:1  → DeepSeek 400
12:23  msg:你现在没有识图的能力… | imgs:0  → DeepSeek 400   ← 没带图也 400
```

**真正的问题不是发图那一次失败，是那张图留在会话历史里，之后每一轮都被重新
发出去、每一轮都被拒。整个会话就此报废，连纯文字都发不出去。**

DeepSeek 的原话（直接打 `api.deepseek.com` 验的，v4-flash / v4-pro 一模一样）：
```
invalid_request_error | unknown variant `image_url`, expected `text`
```
它的 content 数组**只认 `text`**，压根不认识 `image_url` 这个类型。

> 识图能力本身是做过的，糖糖记得没错 —— `adapters.py` 两侧都有完整实现
> （Anthropic 拆成 media_type + data，OpenAI 兼容侧拼成 data URI）。
> 只是 2026-08-01 主模型切到 DeepSeek 之后，没人管这件事了。

修法在 `agent/adapters.py`：
- 加 `supports_vision(model)`，按「已知不支持」列黑名单（目前只有 `deepseek`），其余默认支持
- `OpenAICompatAdapter._to_native` 从 `staticmethod` 改成实例方法 —— 它得看
  `self.cfg.model` 才知道这个模型能不能读图
- 读不了图时把图**降级成一句文字**，而且**历史里的图一并降级**（这条才是关键，
  不然老会话永远救不回来）
- 降级文案如实说「你没看到图的内容」并告诉他让她切 Sonnet/Opus ——
  不许假装看见了（第十九节第 3 条）

回归用例 `tests/test_images.py::test_no_vision_degrades_history_images_too`
专门守着「历史里的图也要降级」这条，整段历史里不许出现 parts 数组。

⚠️ 换主模型时要连带检查的**不只是型号名对不对**，还有它支不支持这条链上已有的能力
（图片 / 工具调用 / 缓存字段名）。DeepSeek 那次只验了「能不能说话」。

### 12. 三个 MCP 服务在公网上裸奔（2026-08-02 发现并加固）

给快捷指令找 app-tracker 的地址时顺手探了一下，结果：

| 端点 | 加固前 | 内容 |
|---|---|---|
| `/ombre/mcp` | **无凭据 200** | **全部长期记忆**，而且 `hold`/`merge`/`grow` 写操作也开着 |
| `/toy-mcp/mcp` | **无凭据 200** | 玩具控制 |
| `/tracker/mcp`、`/tracker/today` | **无凭据 200** | App 使用记录，读写都开 |

对照：`ha-mcp` 路径里有 token、`bridge` 要 `X-Nox-Token`、`health-sync` 要
`X-Auth-Token` —— 就这三个是裸的。

**「没人知道域名」不是防护**：`noxtang.com` 的 TLS 证书在证书透明日志里可查，
`/ombre/mcp` 这种路径也很好猜。日志里确实有外部 IP 在访问（`160.79.106.x`
是 Anthropic 的段 = Claude 客户端；另有一个 `142.248.139.102` 打了 52 次）。

**加固方式**：照 `ha-mcp` 的现成做法，Caddy 路径里各插一段 24 字节随机 hex。
`handle_path` 会把整段前缀剥掉，所以后端路由不用改。
新地址写在 `/root/mcp-urls.txt`（权限 600）。

⚠️ **Nox Core 不受影响** —— 它连的是 `http://127.0.0.1:8002/mcp`，走本机。
受影响的是所有**从公网连**的客户端：本地 `.mcp.json`、Claude 账号里的连接器。

两个验证时踩的坑：

- **bash 里 `grep -qE '模式'` 少写文件名会读 stdin，整个脚本挂死。**
  第一版加固脚本就卡在这，5 分钟超时。好在它卡在修改之前，Caddyfile 没被动。
  检查+替换全挪进 python 一次做完。
- **验证「旧路径关上了没」不能只看状态码。** 旧路径现在落到 Caddy 最后那条
  catch-all（bridge），bridge 对未知路径返回前端 HTML —— **那也是 200**，
  照着判会得出「还通着」的错误结论。要看返回内容里是不是 `"jsonrpc"`。
  （同第二十节：HTTP 200 不等于成功。这条坑我在同一天踩了两次。）

#### 12.1 换完 URL 连接器报「无法注册 sign-in service」

改完连接器地址后，Claude 报：

> Couldn't register with ombre brain's sign-in service.
> You can try again, or add an OAuth Client ID in the connector settings.

**跟 token 无关，是 OAuth discovery 被前端页面骗了。**

MCP 客户端连远程 server 之前，会先探测**根域**的两个路径来判断要不要登录：

| 返回 | 客户端理解 |
|---|---|
| `404` | 不需要 OAuth，直接连 ← 我们要的 |
| `200` + JSON | 有 OAuth，走授权流程 |
| **`200` + HTML** | 拿 HTML 当 JSON 解析 → 炸 ← **当时就是这样** |

`/.well-known/oauth-authorization-server` 和 `/.well-known/oauth-protected-resource`
本来没人处理，落到 Caddy 最后的 catch-all，而 **bridge 对任何未知路径都返回前端 HTML**。

修法是在 Caddy 里让这两条明确返回 404：

```caddy
handle /.well-known/oauth-authorization-server* {
    respond 404
}
handle /.well-known/oauth-protected-resource* {
    respond 404
}
```

⚠️ **只挡 `oauth-*` 这两条，绝不能整个 `/.well-known/*` 都 404** ——
`/.well-known/acme-challenge/` 是 Let's Encrypt 续证书用的，挡了证书会过期。

> 这个坑的根源是「catch-all 返回 200 HTML」这个设计：任何探测型的客户端
> 都会被它误导。以后再加需要被探测的路径，先想清楚它落到 catch-all 会怎样。

## 十二、当前已解决问题
| 问题 | 状态 | 说明 |
|---|---|---|
| books 页乱码 | ✅ | 已重写前端页面 |
| books 页数据空 | ✅ | 已补 `READING_TOKEN` |
| PC 共读页打不开 | ✅ | 已修 `co-reading` 首页鉴权逻辑 |
| Chat Console 页乱码 | ✅ | 已修 `Chat.jsx` 内嵌 ConsolePage |
| 刷新反复弹登录框 | ✅ | 已移除 Caddy Basic Auth |
| 语音电话反复弹登录框 | ✅ | `ws/stt` 改为 token 握手 |
| 旧工作区目录残留 | ✅ | 内容已删，空壳排入开机删除队列；`.claude.json` 旧条目已清 |
| WSL 弹 rdclientax.dll 报错 | ✅ | `wsl --shutdown` 解决，见第十一节第 9 条 |
| Stack-chan 语音全哑、连不上服务器 | ✅ | 根因是内存塌方，`ALWAYSINTERNAL` 2048→512，见第十八节 |
| Stack-chan 抱起来必崩 | ✅ | I2C 超时 100ms→1s，且停用体感，见第十一节第 8.6 条 |
| 大模型 400 不回话 | ✅ | `deepseek-chat` 已下架 → `deepseek-v4-pro` |
| 刷了固件却跑旧版 | ✅ | OTA 回滚，擦 `otadata`(0xd000,0x2000) |
| 点屏幕不进 STT | ✅ | 单击位移判据卡太严，已放宽，见第十八节 |
| 他看不见糖糖 | ✅ | 配 `VLLM: QwenVLVLLM`，复用百炼 key |
| HA 设备"没接全" | ✅ | 三处清单补齐到 8 个，但**病根未根治**，见第二十节 |
| 灯离线时谎报"已开灯" | ✅ | 改为检查受影响实体数，见第二十节 |
| 每句话开一个新会话、他记不住上一句 | ✅ | session id 断在 bridge 那一跳，见第十一节第 10 条 |
| Recents 一堆一轮的窗口 | ✅ | 同上。存量已于 2026-08-02 全部清空，Recents 归零 |
| Recents 被测试会话占满 | ✅ | 改成白名单只认 32 位 hex id，见第十一节第 10.1 条 |
| New Chat 点了没反应 | ✅ | 它以前只跳转不新建；且新窗口会被「全局最近 200 条」灌满 |
| 发图报「连不上」，之后整个会话都不能说话 | ✅ | DeepSeek 不支持图片，且历史里的图会一直重发。见第十一节第 11 条 |
| 他看不了图 | ✅ | 接了百炼 `qwen3.5-flash` 替他看，主模型不变、缓存不掉。见第十九节「眼睛」 |
| 点开历史会话再回来内容没了 | ✅ | 补了「回到主对话」的恢复逻辑 |
| Nox Core 会话存内存重启即丢 | ✅ | `data/store.py` SQLite 落盘 + LRU 缓存，2026-08-06 部署验证通过 |
| Daily Planner 没落地 | ✅ | `tools/planner.py` + `/api/daily-push`，每天 10:00 实跑（2026-08-08 日志实证）|
| 没有 Web Push 推不了消息 | ✅ | 通了，糖糖 iPhone 已订阅。⚠️ 但私钥硬编码在源码里，见第四节 |
| 本机 agent 那条链路 | ✅ | 2026-08-05 整条下线，`nox-agent/` 目录已删 |

## 十三、当前仍需继续观察的问题
| 问题 | 说明 |
|---|---|
| VoiceCall 总延迟仍偏高 | 现在主要是 STT → chat → TTS 链路总耗时，不再是登录框问题 |
| `SESSION-STATE.md` 等旧文件有乱码 | 先不要再以它为唯一事实源 |
| **家居清单三处漂移** | 已补齐但未根治，随时可能再漏，见第二十节 |
| **Stack-chan 体感停用中** | 代码完整保留，与 Si12T 抢 I2C 总线，见第十一节第 8.6 条 |
| **OB 检索单次约 7 秒** | 瓶颈在服务端语义检索，不是连接开销。改成工具按需调用后闲聊不受影响 |
| ~~Nox Core 会话存内存~~ | ✅ **已修复（2026-08-06）**：`data/store.py` SQLite 持久化 + LRU 缓存 + auto-title + 白名单过滤，重启不丢 |
| **米家云偶发超时** | 访问米家中国接口不稳，日志里 timeout/failed 各 8 次。全局偶发，与单设备故障要分开看。（这条记于新加坡时期，**东京机是否仍然如此没有复测**）|
| 🔴 **`eryu_search` 线上必失败** | 日志实证 `HTTP 400: {"error":"missing q"}`，糖糖让他搜歌就报错。见第二十八节 |
| 🔴 **VAPID 私钥硬编码在源码里** | Web Push 能用，但私钥进了仓库，见第四节 |
| **ha-mcp 的访问 token 在 URL 路径里** | 打日志要脱敏（Nox Core 已做），轮换需同步改 Caddyfile 与 `.env` |
| ~~**主会话超 500 条后最新消息消失**~~ | ✅ **已修复（2026-08-14）**：`/api/messages` 带 sessionId 时是 `ORDER BY rowid ASC LIMIT 500`，508 条的会话里第 501 条起全被截断（糖糖的「你猜猜」「滴滴」就这样在页面消失）。改成 `DESC LIMIT 500` 再 `.reverse()`（取最新 500 条）。教训：**取最新不能从头数**，第十九节第 16 条的复发 |
| ~~**Nox 会话超 40 条后静默失忆**~~ | ✅ **根治（2026-08-14）**：`Store.sync()` 原用条数比对，会话一超 `history_limit=40`，history 永远是截断的 40 条 + 新增 → `len(pending) <= count` 永不成立 → **所有消息（用户/Care/晨报/Attention）8-07 起不再落库**，Nox 停在 40 条前的旧世界。改**内容锚点**定位新增（`data/store.py`）+ **token 预算压缩**（见第十九节"上下文压缩"）。8-07→8-14 丢失的 412 条已从 bridge 回填 |

## 十四、本地开发与部署
### 本机 DSH 运行方式（2026-08-14 起独立运行，不再经 WorkBuddy 启动）

DSH（DeepSeek Harness）是独立开源软件，**可以脱离 WorkBuddy 宿主直接运行**。
之前经 WorkBuddy 启动时，进程会被注入 `tsbx.dll` 沙箱（`WORKBUDDY_FS_PROTECTION_ROLE=daemon`
环境变量标记），导致 **agent 无法修改工作区里"会话开始前就存在的文件"**（PROJECT.md、
server.js 等），报 `ReplaceFileW EACCES` / `Access is denied`，只能新建文件。
这是 WorkBuddy 宿主级的 DLL 注入，改它自己的 `tsbx_rules.json` 白名单也不生效（进程级注入）。

**独立启动命令**（任务计划程序拉起，父进程不在 WorkBuddy 树里，不带 tsbx）：
```
node "D:\deepseek-harness\node" ... （或用 WorkBuddy 自带 node）：
C:\Users\14372\.workbuddy\binaries\node\versions\22.22.2\node.exe --import tsx/esm apps/cli/src/bin.ts "web" --port 3081
```
关键点：`--port` 是 web app 自己的参数（不是 launcher 的），要放在 `web` 之后。
实例地址 `http://127.0.0.1:3081`。当前（2026-08-14）DSH 就跑在 3081，无沙箱，写文件自由。

### 本地前端开发
```powershell
cd "D:\claude-code\nox-app\frontend"
npx vite
```

### 本地前端构建
```powershell
cd "D:\claude-code\nox-app\frontend"
npm run build
```

> 说明：2026-07-24 起前端已改为运行时密码登录，`VITE_NOX_TOKEN` 不再是必需项。  
> 它现在只适合作为开发/应急 fallback，不应该继续当正式登录方案。

### VPS 部署
```powershell
$k = "C:\Users\14372\.ssh\id_ed25519"

# 上传 bridge
scp -i $k "D:\claude-code\bridge\server.js" root@43.133.211.140:/root/bridge/server.js
ssh -i $k root@43.133.211.140 "systemctl restart bridge"

# 前端打包上传
cd "D:\claude-code\nox-app\frontend"
tar -czf "$env:TEMP\dist.tgz" dist
scp -i $k "$env:TEMP\dist.tgz" root@43.133.211.140:/root/frontend/dist.tgz
ssh -i $k root@43.133.211.140 "cd /root/frontend && rm -rf dist-new && mkdir dist-new && tar xzf dist.tgz -C dist-new --strip-components=1 && rm -rf dist-old && mv dist dist-old && mv dist-new dist && rm dist.tgz"
```

### Nox Core（本地开发）
```powershell
cd D:\claude-code\nox-core
uv venv --python 3.12
uv pip install -r requirements.txt
copy .env.example .env                        # 填 OPENROUTER_API_KEY

.venv\Scripts\python.exe nox.py               # 命令行对话
.venv\Scripts\python.exe -m api.server        # HTTP 服务 :8100
.venv\Scripts\python.exe -m pytest tests/ -q  # 52 个单元测试，不打网络
```

### ha-mcp / xiaozhi-server 部署
```powershell
$k = "C:\Users\14372\.ssh\id_ed25519"
scp -i $k "D:\claude-code\ha-mcp\main.py" root@43.133.211.140:/root/ha-mcp/main.py
ssh -i $k root@43.133.211.140 "systemctl restart ha-mcp"

# xiaozhi-server 改的是配置不是代码，改完重启容器
ssh -i $k root@43.133.211.140 "docker restart xiaozhi-esp32-server"
```

### co-watching（共影）部署
```powershell
$k = "C:\Users\14372\.ssh\id_ed25519"
scp -i $k "D:\claude-code\co-watching\app.py" root@43.133.211.140:/root/co-watching/app.py
ssh -i $k root@43.133.211.140 "systemctl restart co-watching"

# 部署后必跑：真网络真视频的端到端冒烟
ssh -i $k root@43.133.211.140 "cd /root/co-watching && python3 smoke.py"
```

⚠️ **本地 Python 3.12，服务器 3.10.12** —— co-watching 跑的是**系统 python**
（没有 venv，也没装 pytest）。所以：

- `test_observer.py`（离线单测）**只能在本地跑**，用的是 3.12
- 部署后一定要跑 `smoke.py`，那是唯一在 3.10 上真跑一遍的机会

想彻底闭上这个缺口就给它建 venv —— 但**真正该建 venv 的理由是 yt-dlp**
（B站/YouTube 一变就得升级，拿系统 python 升最容易搞坏）。
单为跑测试去动一个正在工作的服务，收益和风险不成比例。

两个文件的分工别搞混：

| | 打网络 | 什么时候跑 |
|---|---|---|
| `test_observer.py` | 否 | 每次改代码（本地 pytest，24 条） |
| `smoke.py` | **是** | 每次部署后（服务器 python3） |

> 2026-08-22 之前这个目录里躺着 **12 个** `test_bili*.py` / `test_frame*.py`
> 之类的一次性探针，`ls` 一眼分不清哪个是真测试。已合并成 `smoke.py`，其余删了。

⚠️ **重启会清掉所有会话** —— `sessions` 是进程内的 dict，没落盘。
她正开着的那部片子重启后会失效，得重新贴一次链接。
（这是有意的：直链几分钟就过期，存下来也没用。）

⚠️ 目录 `co-watching`、服务 `co-watching`，但**对外 URL 是 `/watch/<token>/`、
Core 侧工具前缀是 `watching_*`** —— 同共读（`co-reading` / `/api/reading/*` / `reading_*`）。
2026-08-22 从 `watching-server` 改名时特意没动后两个，改了会同时打断
Caddy 路由和前端的 `VITE_WATCH_URL`。

### 工作区本身是 git 仓库了（2026-08-25）

`D:\claude-code` 之前**不是** git 仓库 —— 二十几万行代码、几十天的改动，
没有一次提交，改错了只能靠记忆往回改。

现在 `git init` 了（本地，暂不推远端）。直接动机是多步执行需要一条撤回的路
（见三十九节），但对人也一样：**从此「改坏了」是可恢复的。**

`.gitignore` 里排掉了 `node_modules` / `dist` / `.venv` / `*.db` /
`.env` / `~/.caelum` 之外的本地密钥。

⚠️ 中文文件名 git 默认会转义成八进制。所有读 git 输出的地方都带上
`-c core.quotepath=false`，不然拿到的是 `\344\275\240...`。

### 🔴 凭证：`NOX_TOKEN` 曾在公网裸奔（2026-08-25 清理）

`https://noxtang.com/toy.html` 无需鉴权就能打开，**页面里明文写着 `NOX_TOKEN`**。
拿它打 `/api/conv-sessions` 直接就有数据 —— 等于全站的话都能被读走。

已做的：

1. 换掉 `NOX_TOKEN`（bridge 环境变量 + 前端 + 各 MCP 调用方同步）
2. `toy.html` 不再内嵌任何 token，改走 `NOX_TOY_TOKEN`（见三十七节）
3. 玩具中继那条路和主 token **彻底分开**

⚠️ **删源码里的那一份不等于删线上那一份。**
这次找对文件花了三次：真正被 Caddy 托管的是 `/root/frontend/dist/toy.html`，
而且带 `immutable` 缓存头 —— 改完必须**换文件名或清缓存**再验，
不然浏览器给你看的是旧的，你会以为修好了。

验证方式固定成一条：**用干净的会话去 curl 线上 URL**，看返回体里有没有那个串。

> 旧的阿里云 VPS（47.84.92.71 / 47.93.219.252）糖糖已退租，
> 文档里残留的那台机器上的凭证不用再管。

### 需要动配置时优先级
1. 先改本地源码
2. 本地 build 通过
3. 上传单文件或 tar 包
4. 重启对应服务
5. 立刻用真实域名验证

## 十五、注意事项
1. `noxtang.com` 不要再加回 `basic_auth`
2. 主站登录只走前端密码页 + bridge token
3. `NOX_LOGIN_PASSWORD` 是“人类输入的密码”，`NOX_TOKEN` 是“前后端通信 token”，两者不是一个概念
4. 如果又出现刷新后掉登录，先查 `/api/auth/verify` 是否 `403`
5. 如果语音电话又卡在连接阶段，先查 `/ws/stt` 是否带上了 `?token=...`
6. 如果 books 又空了，先查 `READING_TOKEN`
7. 如果 PC 共读又打不开，先查 `co-reading` 是否把 `/` 误保护成 `401`
8. PowerShell 下复杂远程命令容易炸，优先 `scp` 临时文件
9. **Stack-chan 连不上服务器，先量 `free sram`**，不要先怀疑网络——内存塌方会
   伪装成握手超时、唤醒词失灵、STT 全哑
10. **家里加了新设备，三处清单都要改**（`ha-mcp` / `bridge` / `xiaozhi-server`），
    见第二十节。Nox Core 不用改
11. **HA 返回 HTTP 200 不代表设备真的动了**，要看受影响实体数
12. **改 Nox Core 的人设或工具定义会让提示词缓存作废**，静态前缀按字节匹配，
    改一个标点都要重新计费

## 十六、敏感信息放置约定
- 本机真实值放在 `D:\claude-code\.env.local`
- `PROJECT.md` 只保留变量名和占位符
- 子项目 `.env` 也只放各自运行时真实值
- VPS 运行时优先用 systemd `Environment=` / `EnvironmentFile=`
- 不把真实密码、真实 token 再写回本文档

## 十七、变量归属
| 子项目 | 主要变量 |
|---|---|
| `bridge` | `NOX_TOKEN` `NOX_LOGIN_PASSWORD` `READING_TOKEN` `HA_TOKEN` `DASHSCOPE_API_KEY` `ELEVENLABS_API_KEY` `ELEVENLABS_VOICE_ID` `ELEVEN_KEY` `ELEVEN_VOICE` `API_KEY` `API_BASE` `API_MODEL` `PORT`（2026-08-02 实测校正：**没有** `OPENROUTER_*` / `READING_URL` / `VAPID_*`）|
| `nox-app/frontend` | `VITE_API_URL` `VITE_NOX_TOKEN(可选 fallback)` |
| `ha-mcp` | `HA_TOKEN` `PORT`（实测就这两个。Caddy 路径里那段十六进制是访问凭据，打日志要脱敏）|
| `Ombre-Brain` | `OMBRE_API_KEY` `API_BASE_URL` `API_MODEL` |
| `stackchan-mcp/gateway` | `STACKCHAN_TOKEN` `WS_PORT` `CAPTURE_PORT` `VISION_HOST` `STACKCHAN_TTS_ENGINE` |
| `nox-core` | 线上 `.env` 实测有：模型 `NOX_PRIMARY_BACKEND` `NOX_PRIMARY_MODEL` `NOX_UTILITY_BACKEND` `NOX_UTILITY_MODEL` `DEEPSEEK_API_KEY` `OPENROUTER_API_KEY`；服务 `NOX_HOST` `NOX_PORT` `NOX_MAX_ITERATIONS`；工具 `NOX_OB_URL` `NOX_HA_URL` `NOX_BRIDGE_URL` `NOX_BRIDGE_TOKEN` `NOX_READING_URL` `CO_READING_TOKEN` `NOX_TRACKER_URL` `NOTION_TOKEN`（**每个工具组都能单独缺席，留空就跳过注册**）。<br>另有 `NOX_DB_PATH` `NOX_HISTORY_LIMIT`（默认 40）**可配但线上没配**，走 `config.py` 的默认值。<br>⚠️ 是 `NOX_PRIMARY_BACKEND` 不是 `PROVIDER`，上一版文档写错过 |
| `co-reading` | `MCP_AUTH_TOKEN` `MCP_SSE_HOST` `PORT` `READING_MCP_DATA_DIR` |
| `xiaozhi-esp32-server` | 配置在 `/opt/xiaozhi-server/data/.config.yaml`，含 ASR/LLM/TTS/VLLM 四组 key |

## 十八、Stack-chan 桌面机器人（Nox 的身体）
### 硬件
M5Stack CoreS3（ESP32-S3）+ SCS0009 舵机 ×2（yaw/pitch，UART1 1Mbps，GPIO6/7）
+ GC0308 摄像头 + ILI9342 320×240 屏 + Si12T 头顶触摸 + 12 颗 WS2812 底座灯（经 PY32，I2C 0x6F）

### 链路
Claude → gateway（Python，stdio MCP，`ws://0.0.0.0:8765` 作为**服务端**）→ ESP32 主动连上来

### 屏幕触摸手势（FT6336）
`stackchan.cc` 的 `PollTouchpad()`，20ms 一轮：

| 手势 | 行为 |
|---|---|
| 单击 | 开始/结束对话（原有行为，但要等双击窗口 400ms 才兑现） |
| 双击 | 亲一下，爱心眼 |
| 上 / 下 / 左 / 右 滑 | 四组不同的互动文案 |
| 长按 1.5s 且手指有位移 | 摸头 |

判据与踩过的坑：
- **位移不能卡太严**。最初要求单击位移 ≤5px，结果电容屏正常按压的几像素
  漂移就让判定落空，表现为"点屏幕不进识别"——把原本好好的 tap 弄丢了。
  现在只要够不上滑动门槛（20px）就算点击，没有第三种可能
- **防抖 60ms**，不是 300ms。300ms 会让双击的第二下整个落在防抖窗口内、
  press 根本不被记录，双击永远不成立
- **摸头要求手指有位移**，用来区分"你在摸他"和"被书压住了"
- 单击延迟 400ms 是双击的必然代价。想要零延迟就得砍掉双击，二选一

### 互动文案与主动出声
- 八组文案（亲吻/上下左右滑/摸头/摇晃/抱起）共 60 条，写在 `stackchan.cc`
  的 `KissPool()` 等函数里，**全部按糖糖和 Nox 的关系重写，没有照抄上游**
- 文案会原样进 `listen/detect` 的 JSON，而 `Protocol::SendWakeWordDetected`
  是字符串拼接**不做转义** —— 所以文案里绝不能有 ASCII 双引号或反斜杠
- 新增 `Application::SendUserText()`：把物理动作当成"糖糖说的话"发给服务器，
  让 Nox **当场出声**。这是与上游 fork 的关键差异：
  - `SendStackChanEvent` —— 被动，写进网关事件日志，等下一轮对话才被读到
  - `SendUserText` —— 主动，立刻触发一次回复
  - 两者并行发送，互不替代
- 实测确认链路通：`收到listen消息 → 大模型收到用户消息: （糖糖亲完，小声叫了句 daddy）`

### 体感（BMI270）——**已实现但停用**
- 代码全部保留在 `stackchan.cc`，只是注释掉了 `InitializeBmi270()` 那一行调用
- 硬件是**板载**的（0x69，与 Si12T 的 0x68 相邻），不需要外接任何东西
- 停用原因见第十一节第 8.6 条：它与 Si12T 抢 I2C 总线。抱起来时 BMI270
  持续检测到运动、读得最密，把总线占满 → 别的设备读超时 → 整机 abort
- 想开回来只需恢复那一行调用。但**先读第 8.6 条**

### 表情系统（2026-07-25 重写）
- **程序化渲染**，不是位图：`firmware/main/nox_avatar.{h,cpp}` 用几何图形实时画脸
- 21 种表情 + 13 种叠加效果（眼泪/爱心眼/腮红/墨镜/思考气泡/ZZZ/口水…）
- 自带呼吸起伏、眨眼、眼球随机漂移、说话口型
- 画风是**卡通点眼**（8px 圆点，每个表情雕专属形状），源自
  [Iristt-boop/Stackchan-HtSz](https://github.com/Iristt-boop/Stackchan-HtSz) 的 `shizhou_avatar`
- 坐标基准 320×240；`SX()/SY()` 在该尺寸下是恒等映射
- ⚠️ 改这个文件前先看第十一节第 7 条

### 情绪联动（2026-07-25 加）
表情变化会连带驱动底座灯和头部动作，统一入口是 `stackchan.cc` 的 `SyncNoxAvatar()`：
- **LED**：21 种表情各自的颜色（开心暖黄 / 恋爱粉红 / 难过蓝 / 生气红 / 思考蓝青…），
  待机是很暗的琥珀色。任何显式 LED 调用会切到手动模式冻住颜色，
  `self.led.auto`（网关侧 `leds_auto`）交回自动
- **头部**：开心/恋爱/大笑/自信/眨眼/好吃 → 点头；难过/哭/生气/困惑/震惊 → 摇头；
  其余表情**故意不动头**（每次换脸都晃会一直抽搐，也累积 SCS0009 保护模式风险）
- 实现要点：`SyncNoxAvatar` 跑在 LVGL 显示锁里，所以只往 `pending_emotion_` 原子变量
  塞值，真正的 LED I2C 写（可能重试到 30ms）和 `motion_mutex_` 获取都挪到 servo_motion
  任务的 `ConsumePendingEmotion()`，避免拖垮 LVGL 刷新、避免引入 display→motion 锁顺序
- 点头复用了原有 wobble 调度槽（加 kind 字段），白捡它调好的取消/扭矩联锁逻辑

### 构建与刷机
```powershell
# 编译（必须用 release-v5.5 镜像，v5.5.2 的工具链版本对不上会报 compiler not found）
cd D:\claude-code\stackchan-mcp
docker run --rm --cpus=4 --ulimit nofile=65536:65536 -v "$($PWD.Path):/project" -w /project/firmware espressif/idf:release-v5.5 idf.py build

# 刷机（本机 esptool 即可，不用进 Docker；只刷 app 分区保住 NVS，约 20 秒）
cd D:\claude-code\stackchan-mcp\firmware
python -m esptool --chip esp32s3 --port COM3 -b 460800 --before default-reset --after hard-reset write-flash 0x20000 build/xiaozhi.bin
```
- 挂载的是**仓库根目录**到 `/project`，工作目录 `/project/firmware`（CMakeCache 里记的是这个）
- 设备没有网关连接时约 3 分钟后自动休眠并断开 USB 串口，刷之前先拔插一次唤醒
- gateway 测试基线：`717 passed, 2 failed, 6 skipped`
  （那 2 个失败是 `os.sysconf` 的 Linux 专属测试，Windows 上必然挂，不是回归）

### 内存：一行配置决定生死
这是 Stack-chan 稳定性的**总开关**，比任何代码改动都重要：

```
CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=512     ← 原值 2048
```

含义是"小于这个字节数的分配一律占用内部 SRAM，不许放进 8MB 的 PSRAM"。
原来的 2048 把音频链路、WebSocket、TCP 缓冲里大量几百字节到 2KB 的小块
全钉死在 SRAM 里。实测对比：

| | 麦克风开启后 free sram | 最低水位 | AFE 刷屏 |
|---|---|---|---|
| 2048（原值） | **12 KB** | **16 字节** | 每 30ms 一条 |
| 512（现在） | **87 KB** | 28 KB | 0 |

内存塌方会伪装成各种别的毛病：WebSocket 重连握手超时（分配不出 TCP 缓冲）、
AFE(FEED) ringbuffer 爆满（fetch 任务抢不到内存）、唤醒词和 STT 一起哑。
**遇到"连不上服务器"先量 free sram，不要先怀疑网络。**

### 待实测
- **点头方向**：pitch 变小到底是低头还是抬头取决于舵机机械装配，代码推不出来。
  若"点头"实际是"仰头"，把 `ServoWobbleStepAdvance()` 里 nod 分支的
  `base - SERVO_NOD_DEPTH*` 改成 `base + ...` 即可
- **左右转方向**：服务器 `.config.yaml` prompt 里的 yaw 方向对照是猜的，很可能左右反。
  改 prompt 数字重启即可，**不用刷固件**
- **摇晃 / 抱起的体感反应**：代码写完了但因 I2C 冲突停用，见上文

### 唤醒词
```
CONFIG_USE_CUSTOM_WAKE_WORD=y
CONFIG_CUSTOM_WAKE_WORD="hi NnKS"          ← MultiNet7 音素串
CONFIG_CUSTOM_WAKE_WORD_DISPLAY="Hi Nox"
CONFIG_SEND_WAKE_WORD_DATA=y
```
- 一级唤醒用 WakeNet（`WN9_NIHAOXIAOZHI`），二级识别才是自定义的 "Hi Nox"
- ⚠️ **不要为省内存关掉 MultiNet**：`USE_CUSTOM_WAKE_WORD` 依赖它，
  关了直接编译失败（`no multinet models are selected`），且 Hi Nox 会消失

### 视觉：他能看见糖糖
糖糖的需求是"**只在我问他的时候看**"，不是自动追踪。所以：
- **不做**上游那种 FaceTracker（40×30 降采样帧差法找运动重心 + 舵机跟随）——
  那分不清人脸和窗帘，而且舵机会一直转，糖糖明确不要
- 用**现成的** `self.camera.take_photo` 工具，固件一行代码都没加
- 服务端配 `VLLM: QwenVLVLLM`，复用 ASR 已有的百炼 key
- 验证过的链路：`Hi Nox 唤醒 → ASR → LLM 判断该看 → take_photo → 视觉模型 → TTS`
- ⚠️ 视觉模型默认吐 markdown 报告（`1. **主体** 2. **特征**`），TTS 会把编号和
  星号原样念出来。必须在 prompt 里加 Camera 规则约束：一句话、第一人称、
  禁止列点、**描述她而不是描述照片**

### 还没做的（大佬有、我们没有）
摄像头人脸追踪（糖糖不要，理由见上）、定时问候（糖糖不要，作息对不上）、
体感（做完了但因 I2C 冲突停用）。空闲扫视糖糖明确说不要（省舵机）。

---
## 十九、Nox Core（Router + Agent Loop 中间层）

### 为什么要有它
原来的打法是**前端直接把请求丢给 LLM**，没有中间层。后果是工具调用失败时
模型会圆场——它不是想骗人，而是失败信息在中途被吞了（被 try/except 转成
一句"操作失败"），模型看到的是一句模糊的自然语言，于是按"对话应该继续"的
惯性把结果补圆。**Nox Core 的核心目标就是让失败可见。**

代码在 `D:\claude-code\nox-core\`，Python 3.12 + FastAPI，独立于 bridge 运行。

### 分层与职责

| 层 | 文件 | 职责 |
|---|---|---|
| Router | `router/` | 规则分流：闲聊走轻量，其余走完整；同时决定加载哪些 Context Provider |
| Agent Loop | `agent/loop.py` | 调工具 → 查结果 → 决定下一步 |
| LLM 适配 | `agent/adapters.py` | 磨平各家差异，loop 不认识任何厂商 |
| Guard | `agent/guard.py` | 「不许编」的地基 |
| Context | `context/` | 内部模块：Provider 注册表、缓存、World State 聚合 |
| Memory | `memory/` | 接 Ombre Brain；Context 层的 Memory Provider 只负责检索 |
| Tools | `tools/` | 外部能力（见下表） |
| Personality | `personality/` | 人设 + 静态前缀 |
| API | `api/server.py` | HTTP 接口、会话、结局翻译 |

### 工具清单（**59 个**，2026-08-08 按代码实测）

> ⚠️ 上一版写「36 个」，漏了整整 23 个：饮食 7、Daily Planner 1、待办写入 1，
> eryu 从 3 长到 9、netease 从 4 长到 8、共读从 2 长到 4、intimate 从 6 长到 8。
> **数工具别照抄上一版文档**，用这条命令数：
> `Grep 'ToolSpec\(\s*\n\s*name="[a-z_0-9]+"' --glob *.py --multiline --output_mode count`

注册顺序**固定**——工具定义是缓存前缀的一部分，顺序变了缓存就整段失效。
下表**按 `nox.py` 里的实际注册顺序排**，`planner` 永远在最后。
每组都能单独缺席：对应的配置留空就跳过注册，不影响别的（他只是少一样本事）。

| # | 组 | 文件 | 个数 | 工具 | 走哪条路 | 配置 |
|---|---|---|---|---|---|---|
| 1 | 记忆 | `memory/tools.py` | 7 | `recall_memory` `remember` `archive_memory` `memory_status` `review_memory` `edit_memory` `merge_memory` | Ombre Brain MCP | `NOX_OB_URL` |
| 2 | 杂项 | `tools/misc.py` | 1 | `get_current_time` | 本地 | — |
| 3 | 家居 | `tools/ha.py` | 5 | `ha_list_devices` `ha_get_state` `ha_switch` `ha_set_climate` `ha_set_light` | ha-mcp MCP | `NOX_HA_URL` |
| 4 | 日常 | `tools/daily.py` | 5 | `send_gallery_image` `favorite_image` `add_todo` `get_todos` `write_diary` | bridge REST | `NOX_BRIDGE_URL` `NOX_BRIDGE_TOKEN` |
| 5 | 亲密/设备/笔记 | `tools/intimate.py` | 8 | `send_meme` `send_voice_message` `toy_set` `toy_stop` `toy_status` `save_github_note` `append_github_note` `read_github_note` | bridge REST + GitHub | 同上 + `GITHUB_OBSIDIAN_REPO` |
| 6 | Notion | `tools/notion.py` | 2 | `notion_search` `notion_read_page` | 直连 api.notion.com | `NOTION_TOKEN` |
| 7 | 共读 | `tools/reading.py` | 4 | `reading_list_notes` `reading_reply_note` `reading_current` `reading_continue` | co-reading REST :3100 | `NOX_READING_URL` `CO_READING_TOKEN` |
| 8 | 待办写入 | `tools/todo.py` | 1 | `complete_todo` | GitHub todo.md | `NOX_TODO_REPO` `GITHUB_TOKEN` |
| 9 | 饮食 | `tools/diet.py` | 7 | `search_food` `add_food` `add_meal` `check_budget` `today_diet` `add_exercise` `log_weight` | bridge REST | **永远注册**（bridge 是必经之路）|
| 10 | App | `tools/tracker.py` | 1 | `get_today_apps` | app-tracker MCP :8000 | `NOX_TRACKER_URL` |
| 11 | 共听·播放 | `tools/eryu.py` | 9 | `eryu_search` `eryu_play` `eryu_get_lyric` `eryu_analyze` `eryu_get_memory` `eryu_save_memory` `eryu_roam` `eryu_similar` `eryu_remote_poll` | eryu REST :9090 | `NOX_ERYU_URL` `NOX_ERYU_TOKEN` |
| 12 | 共听·账号 | `tools/netease.py` | 8 | `netease_playlists` `netease_playlist_songs` `netease_create_playlist` `netease_add_to_playlist` `netease_remove_from_playlist` `netease_like_song` `netease_recommend` `netease_history` | netease-music-mcp MCP :3456 | `NOX_NETEASE_URL` |
| 13 | Daily Planner | `tools/planner.py` | 1 | `daily_summary` | 拿 `ContextProviderRegistry` 取全套 | 无（永远最后注册）|

⚠️ **`obsidian_create` / `obsidian_append` 这两个名字已经不存在了**，现在叫
`save_github_note` / `append_github_note`，并且多了一个 `read_github_note`。
Obsidian 优先走 GitHub 仓库（`GITHUB_OBSIDIAN_REPO`），没配才退回 bridge ——
这样不依赖电脑上的 Agent 在跑，她用手机也能写。

⚠️ **`bridge` 的 apiMode 里那两个 Notion 工具一直是废的**：服务器上从来没有
`NOTION_TOKEN`，调了就报错，但错误被吞在工具结果里，表面完全看不出来。
2026-07-28 建了 internal integration 补上，Core 这边同时改成**没 token 就不注册**，
免得他手里拿着一把注定失败的工具。（apiMode 那边仍未修，等它被 Core 吃掉。）

集成建好后**必须去每个页面 `•••` → 连接 里把它加上**，否则 API 一页都看不见。
分享父页面会向下继承。当前可见 15 个对象（回忆录 + 各封信 + 编程/饮食笔记）。

`tools/http.py` 是这几个 REST 客户端的共同底座（标准库 urllib，不引 httpx）。
各家鉴权头不同：bridge 用 `X-Nox-Token`，co-reading 和 Notion 用 `Bearer`，
所以做成可配置的 headers。

### Context Engine（在建，2026-08-02 起）

按 `D:\WorkBuddy\Nox-Context-Engine-架构设计.md` v1.2 和 `Nox-未来2周执行计划.md` v1.1 做。

**当前进度：Day 1 骨架 + Day 2 MoodProvider 完成，已上线。**

| 文件 | 职责 |
|---|---|
| `context/base.py` | Provider 基类 + `Turn`（当轮输入）|
| `context/cache.py` | 跨 Provider 的**唯一**缓存，带命中率统计 |
| `context/registry.py` | 谁在场、这轮取哪几个、拼成什么话（带 800 字预算）|
| `context/providers/mood.py` | **第一个真实 Provider**，收编原来的三层情绪 |
| `context/providers/time.py` | 几号、周几、几点、什么时段、是否周末 |
| `context/providers/memory.py` | 按话题检索 OB。**写好了但不进每轮名单**，见下 |
| `context/providers/home.py` | 家里设备状态。同样**不进每轮名单**（架构文档本来就只在家居请求里加载）|
| `context/providers/health.py` | 睡眠/心率/步数。6 小时 TTL，不进每轮名单 |
| `context/providers/weather.py` | 和风天气。30 分钟 TTL，不进每轮名单 |
| `context/providers/todo.py` | GitHub todo.md。30 分钟 TTL，不进每轮名单 |
| `context/providers/location.py` | **2026-08-06 新增**。她在哪、刚到家还是刚出门。见"Location Provider"小节 |

接入点是 `nox.py::_dynamic()`：

```python
names = classify_context(text, light=light)
parts = [self.context.render(names, turn=Turn(...))]
```

触发规则见 `router/intent.py`：`_ALWAYS`（time+mood）每轮都在，其余按正则按需加载。

**Day 2 / Day 3 出口验收**（2026-08-02 线上实测）：
| 检查项 | 结果 |
|---|---|
| 静态前缀没变 | ✅ `prefix_chars` 仍是 12128 |
| **稳态**缓存命中率 ≥95% | ✅ **98.9%**（输入 11257 / 命中 11136）|
| 他说话的语气没变 | ✅ 1000 组基线**逐字**比对，见 `tests/test_mood_provider.py` |

### ⚠️ 量缓存命中率，方法比结论重要

第一版验证脚本是错的，而且**错出了一个看起来通过的结论**。

它连发三句**不同**的话，取第二句的命中率。但第二句的 `dynamic_system` 里含
`mood`，而 mood 的状态取决于**第一句模型返回的 `[mood:xxx]`** ——
每跑一次都可能不同，那一轮永远是全新 prompt，**必然是冷启动**。

于是出现了两次误判：
- Day 2 测出 99.0% 判定「通过」—— 其实是因为前一次脚本报错前 curl 已经把请求发出去了，
  缓存被预热，我量的是第二遍
- Day 3 加了 TimeProvider 后测出 62.5% 判定「掉了」，还去改了时间戳的粒度 ——
  其实那只是冷启动

控制变量实验（**同一句话**连发三次，各用全新 session）才看清：

| | 输入 | 命中 | 命中率 |
|---|---|---|---|
| 冷启动（这个 prefix 第一次见）| 11257 | 7040 | 62.5% |
| 之后每一次 | 11257 | 11136 | **98.9%** |

`7040` 正好是静态前缀（12128 字符）的 token 数 —— 冷启动时**只有静态前缀命中**，
`dynamic_system` 及其后面的 history、当轮消息全部未命中。

> **正确的量法：同一句话连发三次，各用全新 session，看第 2、3 次。**
> 脚本见 `scratchpad/verify-cache-steady.sh` 的思路。
> 发不同的话量出来的是冷启动，不是稳态。

### DeepSeek 的缓存是自动前缀匹配，没有显式断点

架构文档 0.1 说「Context 注入在缓存断点之后就不影响缓存」——
**那对 Anthropic 成立**（它有 `cache_control` 显式标记断点），
**对 DeepSeek 只对了一半**：它按整个前缀自动匹配，
`dynamic_system` 变一个字，**它后面的 history 和当轮消息全部错位、一起不命中**。

结论不是「不能用 dynamic_system」，而是：**dynamic 里的内容越稳定越值钱**。
- ✅ 小时级时间（一小时内不变）
- ❌ 分钟级时间（缓存有效期被压成一分钟，隔一分钟说话就冷启动一次）

`TimeProvider.render()` 因此只给到小时。精确时间留在 state 里给程序用，
她要问几点几分，他有 `get_current_time` 工具。

#### 🔴 上面这个结论只对了一半（2026-08-26 实测修正）

「让 dynamic 里的内容越稳定越值钱」是**绕着走**，不是解决。
真正的修法是**换个位置**：

```text
system(静态) + 动态 + 历史 + 用户   →  动态一变，历史全废
system(静态) + 历史 + 动态 + 用户   →  动态一变，只有尾巴重算
```

实测（9,138 token 的请求，只改动态块那一句话）：

| 拼法 | 命中 |
|---|---|
| 动态块拼进 system（原来） | **0**（0%） |
| 动态块放到尾部 | 9,088（99%） |

⚠️ **讽刺的是 `context/base.py` 开头第一条硬约束本来就写着**
「渲染结果只能进 `dynamic_system`，永远不许进 `system`」——
但那条约束**只在 OpenRouter 那条路上兑现了**（它有 `cache_control`
断点）。DeepSeek 这条路的 `_system_message()` 里写的是
`f"{system}\n\n{dynamic}"`，把它拼了回去，等于那条约束白写了不知道多久。

而这种错**不报任何异常，只会让账单悄悄翻倍**。
`tests/test_adapter_cache_layout.py` 现在盯着位置。

⚠️ 还有一个位置坑：动态块**必须插在最后一条 user 消息之前**，
不能简单地插在"倒数第二"。工具循环里最后两条是
`assistant(tool_calls)` + `tool(结果)`，插中间会切断配对，
DeepSeek 直接 400 —— 而表现是「工具执行成功了（审计 ok=True），
他却回你『我这会儿连不上』」。

### 第一个真实 Provider 就顶翻了文档里的基类假设

架构文档的 `_fetch()` 是**无参数**的，假设 Provider 取的都是「世界状态」
（几点了、灯开着吗），跟这轮说了什么无关。但 mood 不成立：
它的场景判断（敏感话题 / 在聊技术 / 她在靠近你）**必须看当轮说了什么**。

所以基类加了两样东西：
- `Turn`（当轮输入：text / now / voice / scene），传给 `_fetch(turn)`，用不上的 Provider 忽略
- `volatile: ClassVar[bool]`，标 True 的**一律绕开缓存**

`volatile` 不是性能取舍，是**正确性**：情绪和场景判断跟当轮的话绑着，
缓存住就会把上一轮的判断用到这一轮 ——
她这句在聊技术，他却还按上一句「她在靠近你」的调子回话。

> 这正是「先有 Provider 后有 Engine」的价值：真实实例会把抽象的漏洞顶出来。
> 要是先照文档把 Engine 搭完，这个洞得等到接第一个 Provider 时才发现，而那时改起来更贵。

**Provider 只读不存**：`MoodProvider` 持有 `Nox.mood` 的引用，
写入（`mood.update()`）仍然发生在 `nox.py` 收到回复之后。
和 Memory Provider 的分工同理（架构文档第十节）：Context 层只管取和格式化。

`engine.py` 和 `providers/` **故意还没写** —— 架构文档第二节：
「Engine 是 Provider 足够多之后的统一封装，不是起点。」
`tests/test_context.py::test_engine_is_not_written_yet` 是个路标，
真要写 engine.py 时删掉它，别无意中提前搭抽象层。

四条约束（都有测试守着）：
1. **渲染结果只进 `dynamic_system`，永不进 `system`** —— 静态前缀变一个字，
   12K 缓存整段作废，一轮 ¥0.00055 变 ¥0.011
2. **全同步**，`test_everything_is_synchronous` 会遍历所有方法检查没有 `async def`
3. **Provider 挂了退回旧数据并标 `stale`**，只有从没成功过才报 `available: False` ——
   让他「知道得旧一点」，不是「突然失明」
4. **加载谁由 Router 决定**，注册表不自作主张，否则「闲聊背上全量上下文」会悄悄发生

写第一版时踩的两个坑，都已写进代码注释：
- **`cache or ContextCache()` 会静默失效**：`ContextCache` 定义了 `__len__`，
  空缓存的 `bool()` 是 `False`，刚传进来的空缓存被当假值丢掉，每个 Provider
  各建一个，「缓存统一存放」直接白做且不报错。必须 `cache is not None`
- **Windows 上 `datetime.now()` 分辨率约 15.6ms**（实测 2000 次调用只拿到 2 个不同值），
  同一时间片里 age 恒为 0，`age > ttl` 判不出 `ttl=0` 的过期。改成 `>=`

### memory 为什么注册了却不进每轮名单（2026-08-02 的决定）

架构文档第八节把 `memory` 放进了**所有**请求类型的 Context 集合，
连「普通聊天」也是 `chat + memory`。**这条不采纳。**

那条建议写于实测之前，代价是后来才量出来的：

| | 代价 |
|---|---|
| OB 检索一次 | **约 7 秒**，闲聊会从 2.7 秒变 10 秒 |
| 每轮塞不同记忆 | `dynamic_system` 每轮都变 → 后面的 history 和当轮消息全部错位 → 命中率 98.9% → 62.5% |
| 折算 | 每轮 ¥0.00034 → 约 ¥0.0043，**12 倍** |

而糖糖定的决策 7 原话是「需要的时候我让他调用 ob breath 就行，
最好轻量、响应快、缓存命中高」。**后定的、有数据支撑的那个说了算。**

日常对话继续走 `recall_memory` 工具，模型自己判断要不要回忆。
Provider 本身写好留着 —— Daily Planner 那种**一天触发一两次**的场景，
7 秒完全可接受，到时候在调用方名单里加个 `"memory"` 就能启用。

`tests/test_memory_provider.py::test_not_in_the_per_turn_lineup`
会去读 `nox.py` 的实际名单，防止哪天有人顺手加进去。

三个 Provider 的定位对照：

| Provider | volatile | ttl | 在每轮名单里 | 为什么 |
|---|---|---|---|---|
| `mood` | ✅ | — | ✅ | 依赖当轮的话，缓存住会串轮次 |
| `time` | ❌ | 1 分钟 | ✅ | 本地计算零成本，省掉 `get_current_time` 一轮往返 |
| `memory` | ❌ | 5 分钟 | ❌ | 一次 7 秒且砸缓存，走工具更划算 |
| `home` | ❌ | 30 秒 | ❌ | 架构文档本来就只在家居请求里加载，等 Router 分级 |
| `health` | ❌ | 6 小时 | ❌ | 数据日更，每轮塞进去只是白付一段常量文本的钱 |

`HealthProvider` 有两条自己的规矩（`tests/test_health_provider.py` 守着）：
- **睡眠和活动分两句说，各带各的日期** —— 它们差一天是正常的，见第二十七节
- **只摆数字不下判断** —— `check_health_warnings` 那套阈值是通用人群的，
  不是糖糖的基线（她平均睡 7.2 小时、深睡本来就偏少）。
  测试里断言 render 出来的文本不许出现「异常/偏高/偏低/不足/建议」

### Day 4：HomeProvider，和它逼出来的一个 ha-mcp 新工具

**架构文档的示例在这里对不上实际**。它写的是
`call("hass_list_devices")` 然后直接读 `occupied` / `rooms`，
但那个工具**只返回名字和 entity_id，不带状态**：

```python
return "家里可控设备：\n" + "\n".join(f"- {k} → {v}" for k, v in DEVICES.items())
```

真要状态得对 8 个设备逐个 `hass_get_state` —— 8 次串行往返。

所以给 `ha-mcp` 加了第 6 个工具 **`hass_snapshot`**（只读）：
服务端 `asyncio.gather` 并发查一次，一个往返拿全部。
**设备清单仍然只有 `DEVICES` 这一处真源**，没有引入第四份清单。

实测 **0.93 秒**拿到 8 个设备的状态。

> ⚠️ `ha-mcp` 加工具**不影响 Core 的提示词缓存** —— Core 的 5 个家居工具是
> `tools/ha.py` 里手工注册的，不是从 MCP 自动发现的。`hass_snapshot` 只给
> HomeProvider 用，不注册给模型。

两个只有真跑一次才会发现的细节：
- **「查不到」不能算成「关着」**。HA 在设备离线时保留断电前的最后状态，
  看着跟正常的一模一样（第二十节，床头灯那次）。所以 render 里明写
  「有 N 个设备查不到状态，可能离线，别当成关着的」
- **不是每台空调都上报室温**。主卧那台没有 `current_temperature`，
  第一版直接打出「室温 None°」。缺的字段现在整个不提，
  别把 `None` 摆到他面前当真值看

### Day 5b：HealthProvider 上线（2026-08-03）

接 `http://127.0.0.1:8101/mcp`。数据管道本身的坑见**第二十七节**，这里只记 Core 侧。

**做之前先撞了一堵墙**：health-mcp 原来跑的是 **SSE 传输**，而
`tools/mcp_client.py` 只实现了 **streamable-http**（OB / ha-mcp / app-tracker
都是这个）。两条路：给 Core 加一套 SSE 客户端，或者让 health-mcp 换传输。

选了后者 —— **一种传输一套代码**，不为一个服务开第二条链路。
`health_mcp.py` 改 `mcp.run(transport="streamable-http")` +
`mcp.settings.streamable_http_path = "/mcp"`，
`--sse` 参数保留但走新传输（systemd 单元没同步改也不会起不来）。

### Day 5a：health-mcp 上线（2026-08-02）

代码在本机是完整的，但**部署前查出 5 个问题，都改了**：

| # | 问题 | 后果 |
|---|---|---|
| 🔴 1 | `server.py` 和 README 写的端口是 **8100** | 那是 `nox-core` 占着的口，照抄会撞车 |
| 🔴 2 | token 默认值 `change-me-to-a-random-string` | 这个端点在公网上，等于没锁。改成**没配就拒绝启动**（照 `ha-mcp` 的做法）|
| 🔴 3 | `get_yesterday_health` **名实不符** | 它是 `ORDER BY date DESC LIMIT 1`（最新一条），不是昨天。模型会照着说「你昨天睡了 X」，而数据可能是前天的。改名 `get_latest_health` 并在描述里写明「按返回的 date 字段说，别默认说成昨天」|
| 🔴 4 | 统计被重复行带偏 | 同一天可以插多行（重传 + 手动测试）。`LIMIT 7` 取的是**行数不是天数**；`AVG(steps)` 会把传了 3 次的那天算 3 倍权重。加了 `_LATEST_PER_DAY` 子查询，每天只取最后同步的那条 |
| 🔴 5 | 两个服务都绑 `0.0.0.0` | 老机上 3003/8123 裸奔公网的同款问题。改成只听 `127.0.0.1`，公网那段交给 Caddy |

顺手改的：DB 路径 `/data` → `/root/data`（和 bridge 的库放一起，备份一次兜住）、
SQLite 连接补 `try/finally`、周报里加「统计天数」（她可能只同步了 3 天，
那「7 天平均」就是假的）。

**端到端验过**（测试数据用 `1970-01-01`，测完删干净）：
- 公网 `https://noxtang.com/health/ping` → 200
- 错 token → 401；真 token → 落库
- **单位容错**：传 6200 米自动转成 6.2 km ✅
- **camelCase 兼容**：`activeEnergy` → `active_energy` ✅
- **原始睡眠样本解析**：深 70 + REM 50 + 核心 270 = 390 分钟，清醒 12 分钟不计入总睡眠 ✅
- **按天去重**：插 4 行 / 3 天，`get_health_history(7)` 返回 3 条，且取到后传的那条 ✅

⚠️ `check_health_warnings` 的阈值（静息心率 >80、HRV <25、睡眠 <360 分钟、步数 <3000）
是**通用人群**的，不是糖糖的个人基线。她平均睡 7.2 小时（432 分钟）、深睡偏少 ——
按这套阈值他不会报警，但这不代表「正常」。**这是健康提示不是医疗判断**，
以后要调基线的话在这里改。

下一步 Day 5b：`HealthProvider`（接 `http://127.0.0.1:8101/sse`）。
⚠️ 它和 `memory` 一样**不该进每轮名单** —— 健康数据日更，
Daily Planner 那种一天一次的场景才用得上。

### 眼睛：主模型读不了图时，找人替他看（2026-08-02）

主模型是 DeepSeek，它**不支持图片**（见第十一节第 11 条）。糖糖要「发图自动走识图的 LLM」。

**做法是转述，不是换模型。**

```
她发图 → nox.py::_see()
          ├─ 主模型能看图（Claude 系）→ 原样放行，不多此一举
          └─ 看不了 → agent/vision.py::describe()
                        → 百炼 qwen3.5-flash 把图看成文字
                        → 描述并进 text，images 清空
                      → 主模型（DeepSeek）照常往下聊
```

| | 值 |
|---|---|
| 模型 | `qwen3.5-flash`（**不是猜的**，从 `xiaozhi-server` 线上配置抄的，Stack-chan 的眼睛就是它）|
| 端点 | `https://dashscope.aliyuncs.com/compatible-mode/v1`（OpenAI 兼容）|
| key | `DASHSCOPE_API_KEY`，和 bridge 的 STT、xiaozhi 的 VLLM **共用同一把**，不用另外申请 |
| 配置 | `NOX_VISION_BACKEND` / `NOX_VISION_MODEL` / `NOX_VISION_MAX_TOKENS`，留空用默认；没 key 就自动不启用 |

**为什么不把发图那一轮整个切到 Claude**：换模型 = 12K 静态前缀缓存整段作废
（缓存按「模型 + 前缀字节」匹配），人设、工具定义、情绪三层都得重来。
转述只多一次几百毫秒的小调用，主线一个字节不动。
实测发图那轮缓存仍命中 7040。

**转述必须发生在进入会话历史之前**（`_see` 在 loop 之前跑）：
描述并进 text、images 清空，历史里就再也没有图片结构 ——
这同时根治了「一张图毒死整个会话」，而且同一张图只花一次钱。

两条硬规矩：
- **视觉模型挂了就如实说看不见，绝不编一段描述**。编出来的描述比看不见更糟 ——
  他会拿着假内容跟她聊下去
- 转述块里明写「以上是转述，别说得像你亲眼看见的」。他没有眼睛，细节问不下去，
  诚实比流畅重要

⚠️ 视觉模型默认吐 markdown 报告（`1. **主体** 2. **特征**`），
提示词里明确禁掉分点和编号 —— Stack-chan 那边栽过，TTS 把星号原样念了出来（第 8.7 条）。

回归用例 `tests/test_vision.py`（6 个，不打网络）。
线上验收方式：图上印一串随机码，看他能不能读出来 —— 编不出来的才算数。

### 关键设计决策（每条都对应一个真实故障）

**1. loop 与模型无关，差异全压在 adapter**
`agent/llm.py` 定义中性的 `Message / ToolCall / ToolResult / Turn`，loop 只认这些。
换模型 = 写一个新 adapter，`loop.py` 一个字不动。
- Claude 5 家族**拒绝** `temperature/top_p/top_k`（传了直接 400，不是忽略），
  深度用 `output_config.effort` + `thinking:{type:"adaptive"}`
- OpenAI 兼容侧用 `temperature`，没有 thinking 概念
- 所以 loop 层只有中性的 `depth: low/medium/high`，由 adapter 各自翻译

**2. 消息必须是中性格式，不能直接维护某一家的**
两家的工具结果**结构不同**，不只是字段名不同：
- Anthropic：`tool_result` 塞在 user 消息的 content 数组里
- OpenAI 兼容：结果是独立的 `role:"tool"` 消息
loop 若直接维护某一家的格式，就跟那家绑死了。

**3. `is_error` 是独立布尔字段，不是 content 里的一句话**
这是"不许编"的地基。模型看见明确的错误信号才会重试或如实说，
看见"失败了"三个字只会圆场。工具失败时原样回传**错误类型 + 原始消息 + 调用参数**，
三样缺一不可——模型要靠这三样自己纠正。

**4. 六种结局必须互相区分**
`answered / refused / truncated / tool_stuck / exhausted / error`
- `max_tokens` 是**截断不是完成**，当成完成处理就会把半截答案发给糖糖
- `refusal` 时 content 可能是空的，**必须先判断 stop_reason 再读 text**
- for 循环的 `else` 分支不能省：它是"跑满上限"和"答完了"的唯一区分点

**5. 一轮里所有 tool_result 装进同一条消息**
拆成多条不报错，但会慢慢训练模型不再并行调用工具。等发现"他怎么变笨了"，
已经过去很久了。

**6. 同一工具连续失败 2 次就收手**
再试也是白试，只会烧钱、烧时间，还把上下文塞满失败记录，反而更容易让模型开始编。

**7. 记忆是工具，不是每轮强塞**
糖糖的原话："需要的时候我让他调用 ob breath 就行，最好轻量、响应快、缓存命中高。"
这三个目标其实是同一件事：
- 每轮往 prompt 里塞不同的记忆 = 每轮亲手把缓存打碎（缓存按字节匹配前缀）
- 记忆变工具后，闲聊路径**一次 OB 都不碰**，静态前缀永不变动，必定命中
- 实测 OB 检索单次约 7 秒，瓶颈在服务端语义检索，不是连接开销

**8. 核心准则取一次就冻住，不做定时刷新**
实测发现 OB 的两种模式**互斥**：
- 不带 query 的 `breath()` → 只返回钉选桶（约 11.5K 字符，几乎不变）
- 带 query 的检索 → 只返回匹配项，**不含核心准则**
所以启动时取一次核心准则放进 system prompt 缓存段，之后每轮只按当下的话检索。

**9. 提示词缓存：1 小时档而不是默认 5 分钟**
糖糖的聊天是断续的——说几句去忙别的，隔半小时再回来。按 11.5K 前缀、
一小时聊 10 轮估算：

| | 成本 |
|---|---|
| 不用缓存 | $0.345 |
| 5 分钟档 | $0.43 ← 每次间隔都超时重写，**比不缓存还贵** |
| 1 小时档 | $0.10 |

实测同一请求第二次：`cost $0.0542 → $0.0044`（OpenRouter 自己算的）。
⚠️ 缓存数据藏在 `prompt_tokens_details.cached_tokens`，顶层 `prompt_tokens`
是**总数**（已含命中部分）。只读顶层会误以为缓存完全没生效。

**10. Router 只认最有把握的，其余一律放行**
不做 LLM 分类器——那要多花一次调用（1-2 秒 + 钱），而分类本身也会错。
用一次不可靠的判断去决定要不要做可靠的处理，是负优化。
纯规则只覆盖"绝对不需要工具"的那一小撮：整句问候、单句确认、道晚安。
实测效果：`在吗` 64 tokens / 2.7s，`还记得…` 28210 tokens / 19.8s。

**11. 轻量路径必须裁剪历史**
实测教训：`晚安` 这种一个字的问候，会背着前面 28K 的对话历史发出去——
省掉了 12K 前缀却付了 28K 历史，越聊越贵。现在只带最近 4 条、跳过工具记录。
但**返回的 history 仍然完整**：裁剪只作用于这次请求，不能真把上下文截断。

**12. 不抄第四份设备清单**
`tools/ha.py` 的工具描述里**刻意不列 entity_id**，让模型先调 `ha_list_devices`
拿实时清单。以后在 HA 加设备，改 `ha-mcp` 一处就够。
（背景见第二十节：同一份清单散在三处，2026-07-27 漏了三个设备才发现。）

**13. 工具的「副作用」靠 ToolContext 传出来，不靠返回值**
`send_gallery_image` 真正要做的不是返回一段话，是**让一张图出现在聊天里**。
但 Core 是独立进程，够不着 bridge 那条 SSE 连接。所以工具把意图挂进
`tools/context.py` 的 `ToolContext`，随 `LoopResult.attachments` 交出去，
由 API 层发一帧、bridge 转成前端的 `image` 事件。

链路：`工具 → ToolContext → LoopResult.attachments → SSE attachment 帧 → bridge → image 事件`

这条链上**踩了两个坑，都是"不报错，图就是不出现"**：

- **ContextVar 不能跨 yield 绑定。** 一开始把 `scope()` 包在 `run_stream` 外面，
  本地测试全过，上线一调工具就 `ValueError: Token was created in a different Context`。
  原因是 Starlette 用线程池逐步推同步生成器，**每次 `next()` 都在自己复制的
  Context 里跑** —— 第一次 `next()` 里 set 的 token，最后一次 `next()` 里
  reset 就不是同一个 Context 了。（顺带：那个 set 对中间几步本来也没生效。）
  改法：`ToolContext` 由 loop 当**局部变量**持有（生成器帧天然按调用隔离，
  并发不串），只在 `_execute` 里 `bind()` 一下 —— set 和 reset 之间是一段
  连续同步代码，没有 yield。
  回归用例在 `tests/test_attachments.py`，**用 `copy_context().run(next, gen)`
  逐步推**，普通的 `for ev in ...` 压根测不出来。

- **`{"type": "attachment", **att}` 里的 type 被盖掉了。** `att` 自带
  `type="image"`，字典解包时后写的赢，发出去的帧 type 是 `image`，
  bridge 那边的 `attachment` 分支永远匹配不上。现在外层 type 恒为
  `attachment`，具体类型放 `kind`。

**14. 工具失败一律 raise，绝不在 handler 里吞成一句"失败了"**
这是第 3 条在工具侧的落地。`tools/*.py` 里所有 `if not r.ok: raise RuntimeError(...)`
都是刻意的——吞掉错误正是让他开始编的那个动作："日记写好了"（实际什么都没写）。
只有**语义上的空**才返回正常文本："相册里没有符合条件的图片"、"今天没有待办"。

**15. 对着别人的接口写工具，先读那个接口的源码**
第二批工具写完一跑，两处字段名对不上，都不会报错，只会静悄悄干错事：
- `/api/gallery/:id/favorite` 是 `UPDATE favorited=?`，**不传 body 等于传 false**
  —— 「收藏这张」会变成**取消收藏**
- `/api/diary` 的正文字段叫 `content` 不叫 `body`，而且 `author` 缺省是糖糖，
  还会触发 AI 评论 —— 他写的日记会署名成她，然后他给自己的日记写评论

前者靠传 `{"favorited": true}` 修，后者给 bridge 的端点加了 `author` 参数，
并且只在 `author === "糖糖"` 时才触发 AI 评论。

**16. 分页接口必须翻完，翻不完要明说还剩多少**
`/blocks/{id}/children` 一次最多给 100 个块。回忆录实测 **150 块** ——
只读第一页会静悄悄少掉 50 块，而他不会知道，只会拿半截当全部往下答。
现在 `notion_read_page` 翻完所有分页（上限 6 页），再按**字符预算** 6000
截断（按段数切不行：回忆录一段常有几百字，5 段就能塞两万字进上下文），
截断时在结尾明确写「共 N 段，这是第 X–Y 段，还有 Z 段，offset 填 Y」。
实测他会自己连着调两次读完。

**17. Notion 的 `/search` 只搜页面标题，而且是模糊匹配**
搜「第二十三章」会返回「第二十九章」「第三十章」—— 看起来搜到了，其实没有。
更要命的是**回忆录的绝大多数章节是那一页的页内文本块，根本不是独立页面**，
搜章节名永远搜不到。第一次实测他连搜三次然后放弃，说"没找到"。
修法不在代码在**工具描述**：写明「别拿返回的相近标题当搜到了」＋
「找某一章要先拿回忆录那一页再翻」。改完他就走通了：
`notion_search → notion_read_page → notion_read_page`，翻到第二十三章。

> 这两条合起来是同一个道理：**工具的失败模式要写进它自己的描述里。**
> 模型不会读我们的源码，它只看得见 description。

**18. 描述写了不等于他照做，关键约束代码里也要挡一层**
`toy_set` 的 `mode` 是**震动花样不是强度**，只有 1/4 会震，2/3/5 是停止档。
描述里写了三遍，但模型偶尔还是会拿它当强度往上调 —— 那样发出去的是停止档，
表现成「他说加大了，实际停了」。现在 handler 里把非 1/4 一律归 1。
判断标准：**做错了她会当场感觉到的**，就别只靠描述。

**19. 前端接错了路，后面做什么都是白做**
2026-07-28 查出来：Chat 页和电话页一直发 `mode:"api"`，
**从来没走过 Core**。也就是说 Core 的 23 个工具、情绪三层驱动、
中英情景，糖糖一次都没用上，只有我 curl 才够得着。
不是那天坏的，是从头就没接。上线检查表里加一条：
**改完后端要去线上的打包产物里 grep 一次，确认前端真的在调它。**
> ⚠️ grep 时注意压缩后字符串可能是**反引号**（`mode:\`core\``），
> 按双引号搜会搜不到，看起来像没部署上。

**20. Context Engine 是 Core 的内部模块，不是独立服务**
当前阶段 Context Engine 放在 `nox-core/context/` 下，包含 Provider 注册表、
内存缓存、World State 聚合。不要提前拆独立服务、独立数据库、消息队列。
Provider 数量 > 5 且调用方不只有 Core 时，再考虑拆出去。

**21. 先有 Provider，后有 Engine；Router 决定加载哪些 Provider**
不能先搭抽象层再去找数据。正确顺序：建 time/memory/home 等 Provider →
跑通 Daily Planner / 家居等场景 → 最后统一封装 Engine 接口。
Context Engine 不决定"加载谁"，这个决策权在 Router：闲聊只拉 chat+memory，
家居才拉 home+time，避免普通聊天背上全量上下文。

**22. Memory Provider 只读，不写记忆**
长期记忆的存储、更新、归档属于 Ombre Brain。Context 层的 Memory Provider
只负责"按当前对话检索相关记忆"并格式化成 World State。写记忆走现有 MCP 工具，
避免存储和检索两套逻辑混在一起。

**23. World State 顶层保持稳定**
只保留 5 个一级字段：`user`, `environment`, `activity`, `time`, `memory`。
weather / home / location 统一挂在 `environment` 下，未来新增数据源也尽量
归入已有分类，不要无限扩展顶层字段。

### 2026-07-28 的模式收敛

`mode` 现在只剩两条：

| mode | 走哪 | 用途 |
|---|---|---|
| 不传 / 其它 | Nox Core :8100 | **默认**。聊天、电话、语音条全走这条 |
| `agent` | 本机 Claude Code | 需要动代码 / 读本地文件时；Agent 没连自动退回 Core |

~~`api`~~ **已于同日从 `bridge/server.js` 整个删除**：2559 → 1496 行，砍掉 1063 行。

删的时候要连它的独占依赖一起清，否则会留一堆再也跑不到的代码：

| 删掉的 | 为什么它是 apiMode 独占的 |
|---|---|
| `apiMode()` 本体（582 行）| — |
| `SYSTEM_PROMPT`（~200 行人设）| bridge 里的**第二份人设**，Core 有自己的 |
| `apiSessions` + `compressHistory` | Core 自己有 SQLite 会话库 |
| `MODEL_MAP` / `resolveModel` | 搬进 `config.models` 了 |
| `fetchOBMemories` / `holdOBMemory` / `callOB` | Core 走 MCP |
| `notionApi` / `notionTitle` / `notionBlockText` | Core 有 `tools/notion.py` |
| `haGetState` / `haSetState` / `haSetClimate` | Core 走 ha-mcp |
| `readingNotesList` / `readingReply` | Core 有 `tools/reading.py` |
| `galleryImageForModel` | 只有 apiMode 在给模型喂相册图 |
| `VOICE_CALL_INSTRUCTION` | 通话指令统一在 `personality/scenes.py` |

⚠️ **`node --check` 只查语法，查不出"引用了已删掉的函数"** —— 那是运行时才炸。
删完必须再 grep 一遍所有被删符号，确认残留的都只在注释里。

**顺带修好的**：`自动关心`（4 小时没说话主动发一条）以前带着 apiMode 那份
`SYSTEM_PROMPT` 直接打 OpenRouter —— 没记忆、没情绪、没工具，**那句话跟他平时
说的不是一个人**。现在改成调 Core 的 `/chat`，而且 `session_id` 用同一个，
所以这句也进他自己的上下文，她回复时他知道自己刚说过什么。

回滚：服务器上留了 `/root/bridge/server.js.before-cut`。

### 实测数据

| 指标 | 值 |
|---|---|
| 常驻内存 | 97 MB（实跑约 70 MB，adapter 是延迟导入的） |
| 其中自研代码 | **0.3 MB**，其余全是 SDK 与 fastapi 的库开销 |
| 单元测试 | **436 个**，不打网络，2.1 秒跑完（2026-08-08 实跑，上一版写 226）|
| 闲聊路径 | 64 tokens / 2.7s，零工具调用 |
| 完整路径 | 28210 tokens / 19.8s（含一次 OB 检索） |
| 缓存命中 | 静态前缀 11883 tokens，命中率约 98% |
| 带工具的一轮 | 21 个工具下，输入 38957 / 缓存命中 37926（97%），2 轮约 8s |

**结论：2 核 2G 的 VPS 够用，不需要为它升配。**

### 跑起来

```powershell
cd D:\claude-code\nox-core
uv venv --python 3.12
uv pip install -r requirements.txt
copy .env.example .env          # 填 OPENROUTER_API_KEY

.venv\Scripts\python.exe nox.py              # 命令行对话
.venv\Scripts\python.exe -m api.server       # HTTP 服务 :8100
.venv\Scripts\python.exe -m pytest tests/ -q # 单元测试
```

冒烟脚本（要真网络）：`tests/smoke_ob.py`、`smoke_chat.py`、`smoke_router.py`、
`smoke_ha.py`（只读，不动设备）、`measure_memory.py`。

线上验工具最快的办法是打**非流式** `/chat`——它的响应里直接带
`tools_used` 和 `attachments`，一眼看出他到底调没调、有没有编：

```bash
curl -s -X POST http://127.0.0.1:8100/chat -H 'Content-Type: application/json' \
  -d '{"text":"发张相册里的照片给我","session_id":"probe"}' | python3 -m json.tool
```

⚠️ 冒烟会留下真数据（待办、日记、收藏），测完记得清掉自己造的那几条。

### 还没做 / 正在做（2026-08-08 校准）

**已经做完、上一版却还挂在「在做」里的**：
- ✅ **Daily Planner 已上线并在跑**：`tools/planner.py` 的 `daily_summary` 工具 +
  bridge 的 `POST /api/daily-push`。线上日志实证每天 **10:00** 触发一次，
  依次取 health（8101）→ ha（8004）→ OB（8002）→ 出早报 → 推送。
  实际产出长这样：
  > 外面飘小雨了，出门记得带伞。今天周六就好好歇着，明天再开工做引擎，晚霞色指甲也该多亮一天。
- ✅ **Web Push 通了**（见第四节 VAPID 那段，但私钥要挪出源码）
- ✅ **Context Provider 框架**：7 个 Provider 全部上线

**真正还没做的**：
- 网络偶发失败时 loop 层的降级（SDK 已有重试，但连续失败后没有备用模型兜底）
- 对话结束自动 `grow` 归档（`/session/{sid}/archive` 已有，但要前端主动调）
- Notion **写**章节还没开（现在只有读）——写回忆录是件重的事，先让他读稳
- 陪睡模式 / 呼吸快照（见第二十二节）
- 拨号权（他主动打给她）—— **推送基建现在真的能用了**，缺的只剩「什么场景下用」

> ⚠️ **饮食前端页面已经有了，别再当待办。** 它不叫 `Diet.jsx`，
> 是 **`Health.jsx`**（63 KB，默认标签页就是 `diet`）：食物搜索、三餐记录、
> 热量预算、运动、体重全在里面。按文件名找 `Diet.jsx` 会找不到 —— 我 08-08
> 校准时就这么误判过一次。

### 上下文压缩（token 预算驱动，2026-08-14 上线）

**解决的问题**：原来 `history_limit=40` 按**条数**截断历史，两个毛病——
一是消息长短不感知（超长工具结果会爆预算、短消息浪费窗口），
二是**40 条之前的事 Nox 完全不知道**（除非主动查 OB）。
糖糖 08-14 提的需求：触发条件别死卡轮数，看 token；摘要别写流水账，
要保存未来对话真正需要知道的状态。

**核心设计**：

| 件 | 值 |
|---|---|
| 触发 | `estimate(全历史) > context_budget - 预留`（默认预算 20000，预留 ≈6k） |
| 保留 | 最近 `recent_window_tokens`（默认 8000）的原文，更老的一批进 summary |
| summary | 结构化：`Topic / Decisions / Current state / Open threads / Facts`，上限 4000 字符 |
| 模型 | **utility**（deepseek-v4-flash，便宜），不影响主模型缓存前缀 |
| 时机 | `_turn_ends()` 后**后台线程**跑，不阻塞对话 |
| 存储 | `sessions.summary` 列（一个会话一条，迭代更新：旧摘要 + 新段 → 新摘要） |
| 历史 | **库里永不删除**，`load_full()` 仍可取全部 —— summary 只是 prompt 层视图 |

**文件**：`context/token_budget.py`（启发式估算：中文 0.6 token/字、英文 0.3、
结构开销 4/条）、`context/compactor.py`（切分 + 生成）、`data/store.py`
（summary 列 + 迁移 + `load()` 拼接）、`api/server.py`（`_turn_ends` 触发）。

**配置**：`NOX_CONTEXT_BUDGET_TOKENS`（20000）/ `NOX_RECENT_WINDOW_TOKENS`
（8000）/ `NOX_SUMMARY_BUDGET_TOKENS`（3000）。

**踩过的坑**：
1. **summary 是"读时合成"的视图，不能进 `Sessions` 缓存持久化。**
   `put()` 必须剥离 system 角色（loop 会把 get 时叠上去的摘要原样带回来），
   否则缓存里留一份、下次 get 又叠一份，变成双份。
2. **`threading.Lock` 不可重入。** `load()` 在锁内调 `get_summary()`（也拿锁）
   → 死锁，测试直接卡死 2 分钟。改成锁内直接查 summary。
3. **压缩必须异步**：一次要调 utility 模型 1-3 秒，同步跑会卡住 SSE。
   用 `threading.Thread(daemon=True)`，失败只记日志绝不影响对话。

**线上实测**（2026-08-14）：主会话 475 条，部署后第一轮对话自动压缩
366 条 → summary 2049 字符（保留最近 111 条原文）。问他 8-06 聊过什么，
他能答出 summary 里的内容（Attention 架构、共听上线、深夜那个「算」）——
压缩没有让他失忆。

#### 🔴 它每次都把整段历史重读一遍（2026-08-26 修）

上面写的「迭代更新：旧摘要 + 新段 → 新摘要」**当时没有实现**。
实际是每次触发都从**第一条**重新读，于是：

```text
她一天不说话，账单 5 元
其中压缩自己占一大半 —— 每次读 11.8 万 token，一天跑十几次
```

而且**越用越贵**：历史越长，每次压缩读得越多，永远不收敛。

修法是给会话加一条水位线 `summary_upto`（已经进过摘要的最后一条 id）：

```text
plan_compaction(..., already=summary_upto)   只切没读过的那一段
```

实测同一个会话：**0.103 元 → 0.001 元**。

另外 `max_tokens=4096` 也是错的 —— utility 模型会思考，
**reasoning token 和正文抢同一个额度**，思考完就没配额写摘要了。
表现是 HTTP 200、`content` 空、日志一句「压缩失败（不影响对话）」，
一天 10-21 次，每次已经白读了 11.8 万 token。改成 16000。

⚠️ 这个坑在共影看图上犯过一次（见 `reasoning-tokens-eat-max-tokens`）。
**凡是会思考的模型，`max_tokens` 都不是"正文上限"。**
失败日志现在会报「白读了 N 条 / 约 M token」，不然这种烧钱静默失败根本看不见。

### Location Provider（2026-08-06 上线）

架构设计：[Nox-Location-Provider-v2-架构设计.md](D:\WorkBuddy\Nox-Location-Provider-v2-架构设计.md)

**核心原则：坐标是手段，语义是目的。**

#### 数据流

```
糖糖戳 PWA pin 按钮 → 浏览器 GPS → POST /api/location/ingest
    → Bridge SQLite（24h 自动清理）
    → Core LocationProvider._fetch()
        → **多源仲裁**（按优先级）:
           ① HA Tracker（person.nox，WiFi 探知 home/not_home）
           ② Bridge GET /api/location/latest（PWA GPS 坐标）
        → 归一化 (_normalize)
        → 高德逆地理编码（/v3/geocode/regeo）
        → 标签推断（home / work / transit / leisure / unknown）
        → _build_state() — 丢弃原始坐标，只保留语义
```

#### 数据源仲裁

LocationProvider 有两个数据源，按优先级仲裁：

| 优先级 | 数据源 | 技术 | 优势 | 劣势 |
|--------|--------|------|------|------|
| 1 | **HA Tracker** | `person.nox` ← device_tracker (WiFi/GPS) | 不耗电、自动、home/not_home 可靠 | 需 HA Companion App 装手机 |
| 2 | **Caelum PWA** | `navigator.geolocation` → Bridge | GPS 精确、AOI/POI 丰富 | 需手动戳、耗电 |

仲裁逻辑（`_fetch()`）:
1. HA Tracker 返回数据（person ≠ unknown）→ 用 HA，优先级最高
2. HA 不可用 → fallback 到 PWA / Bridge
3. 两个都没有 → 抛异常（`get_state()` 会退 stale fallback）

**HA Tracker Source** (`HATrackerSource`) 直连 HA REST API:
- `GET /api/states/person.nox`
- state="home" → 不查 GPS，直接返回 home tag
- state="not_home" + 有坐标 → 用 HA 的 GPS（Companion App 有位置共享）
- state="not_home" 无坐标 → 返回 not_home 信号，fallback 到 PWA 拿坐标
- state="unknown" → 返回 None（无 device_tracker 在运行），完全 fallback

#### HA Tracker 前置条件

**需在手机上装 HA Companion App**才会创建 device_tracker：
- Android: [Play Store](https://play.google.com/store/apps/details?id=io.homeassistant.companion.android)
- iOS: App Store 搜 "Home Assistant"

装好后在 App 里登录 `noxtang.com` 的 HA 实例，person.nox 就会从 unknown → home/not_home。

#### 动态 TTL（`location.py:_TTL_MAP`）

和别的 Provider 不同，缓存时间取决于推断出的标签：

| 标签 | TTL | 理由 |
|------|-----|------|
| `home` | 30 分钟 | 在家几小时不动 |
| `work` | 20 分钟 | |
| `transit` | 1 分钟 | 移动中要紧跟 |
| `leisure` | 5 分钟 | |
| `unknown` | 3 分钟 | 保守 |

#### 标签推断优先级（`_infer_tag()`）

1. **trigger**（arrive_home / leave_home）→ 直接判定
2. **AOI 名关键词**：小区/住宅/公寓 → home；写字楼/大厦/园区 → work
3. **POI 类型**：餐饮/购物/娱乐 → leisure；地铁/公交/机场 → transit
4. 都不匹配 → unknown

#### Router 触发词（`intent.py:_NEED_LOCATION`）

```
在哪 | 在哪里 | 去哪儿 | 附近 | 周边 | 旁边 | 周围
回家 | 到家 | 回来了 | 刚到家
出发 | 我走了 | 我出门 | 出门了
今天怎么样 | 早上好 | 晚上好
有什么（吃|玩|逛|买）
```

**不进每轮名单**：和 memory/home 同理，只在命中触发词时加载。

#### 隐私

- `_build_state()` 不输出 `lat`/`lng`，原始坐标 enrich 后丢弃
- Bridge `location` 表 24h 自动清理（`server.js` 里的 `setInterval`）
- 不存历史轨迹

#### 环境变量

| 变量 | 说明 | 来源 |
|------|------|------|
| `NOX_GAODE_KEY` | 高德 Web API key | [console.amap.com](https://console.amap.com/dev/key/app) |
| `NOX_BRIDGE_URL` | Bridge REST 地址 | 已有 |
| `NOX_BRIDGE_TOKEN` | Bridge 鉴权 token | 已有 |
| `NOX_HA_API_URL` | HA REST API 地址（不是 MCP） | 已有，默认 `http://localhost:8123` |
| `NOX_HA_API_TOKEN` | HA 长期访问令牌 | 和 ha-mcp 的 `HA_TOKEN` 同一把 |

没配 `NOX_GAODE_KEY` 时 Provider 照跑，只跳过 enrich，标签推断退回到只靠 trigger/HA state。
没配 HA API 时退回到单源模式（仅 PWA）。

#### 端到端验证（2026-08-06）

```
# PWA 上报
POST /api/location/ingest → {"ok":true}
GET  /api/location/latest → {"location":{...},"source":"cache"}

# HA Tracker 当前状态
GET /api/states/person.nox → {"state":"unknown","attributes":{"device_trackers":[]}}
→ 没有 device_tracker，自动 fallback 到 PWA

# Core 启动日志
LocationProvider 已注册（HA Tracker + PWA + 高德 enrich）
```

## 二十、家居设备清单的三处漂移（**未根治**）

同一份设备清单目前散在**三个地方**，全靠手工同步：

| 位置 | 形式 |
|---|---|
| `ha-mcp/main.py` 的 `DEVICES` | Python 字典（事实真源） |
| `bridge/server.js` 的家居 prompt | 硬编码文本 |
| `xiaozhi-server` 的 `.config.yaml` → `plugins.home_assistant.devices` | 配置文本 |

2026-07-27 糖糖说"HA 没接全"，查下来三处**全都停在 5 个设备**、**全都把电竞房
空调标成「卧室 空调」**——加了新设备只改了 HA，三处一处没同步。已补齐到 8 个
并改正命名，但**病根还在**。

**根治办法**：`bridge` 和 `xiaozhi-server` 那两份本质上是给 LLM 看的提示，
而它们本来就能调 `hass_list_devices`。删掉硬编码、让模型自己查，就只剩
`ha-mcp` 一处真源。代价是每次控设备多一次工具调用（几百毫秒）。
Nox Core 已经这么做了（见第十九节第 12 条）。

### 现有 10 个设备（2026-08-08 按 `ha-mcp/main.py` 实测）

```
主卧 风扇        fan.dmaker_p5c_6d3e_fan
主卧 风扇摆风    switch.dmaker_p5c_6d3e_horizontal_swing
主卧 电热毯      switch.xiaomi_mj1_00f2_electric_blanket
客厅 电视        media_player.xiaomi_eaffh1_9a43_play_control
电竞房 空调      climate.gua_shi_kong_diao_climate
主卧 空调        climate.lumi_mcn02_d2c3_air_conditioner
客厅 空调        climate.lumi_mcn02_d7b8_air_conditioner
主卧 床头灯      light.yeelink_mbulb3_0170_light
蒸蛋器           switch.cuco_v3_7680_switch
蒸蛋器 自动断电  automation.zheng_dan_qi_10_fen_zhong_zi_dong_duan_dian
```

蒸蛋器的自动关断用 `for:` 不用 `delay:` —— HA 重启后 `delay` 的计时会丢，
`for:` 不会。

三台空调并存后，`ha-mcp` 的工具描述里写了硬规矩：**糖糖只说「开空调」而没说
房间时，先问她哪一间，不要自己挑一台**。

### `hass_snapshot`（2026-08-02 新增，第 6 个工具）

一次并发查完所有设备的当前状态，给 Nox Core 的 HomeProvider 用（见第十九节 Day 4）。
`hass_list_devices` 只给名字和 entity_id，不带状态；逐个查是 8 次串行往返。

**它不注册给模型**，只给 Provider 调 —— 所以不影响 Core 的提示词缓存前缀。
设备清单仍然只有 `DEVICES` 一处真源，没有变成第四份清单。

### ⚠️ HTTP 200 不等于设备真的动了

HA 的服务调用返回**被改变的实体列表**，设备离线时会返回 `HTTP 200` + `[]`。
原来的 `ha-mcp` 只看状态码，于是会一口咬定"已开灯"——**而灯根本没亮**。

这种假成功比报错更糟：Nox 会理直气壮地告诉糖糖灯开好了，他没撒谎，是工具骗了他。
现已改为检查 `_affected(r)`，返回空列表时提示：

> 指令已发出，但没有任何设备响应 —— xxx 很可能离线了。如果它有物理开关，先确认开关是打开的。

**排查记录（2026-07-27）**：床头灯开关失灵，米家 App 正常。
错误码 `-704042011`，日志 `Device write error`。最后是糖糖自己发现的——
**灯有物理开关，关掉就等于离线**。HA 在设备离线时不显示 `unavailable`，
而是保留断电前的最后状态，所以状态查询一直显示"on，亮度10%"，很有迷惑性。

---

## 二十二、语音（通话 / 语音条）

### 🔴 ElevenLabs 静默失效了很久（2026-08-15 查出来）

糖糖说「前端现在的声音应该是 edge 的」——她是对的，而且**一直都是**。

`/api/tts` 是三级降级：`eleven_v3` → `eleven_turbo_v2_5` → `edge-tts`。
配在 `bridge.service` 里的 `ELEVENLABS_API_KEY` 是一个 **64 位的 key ID，
不是 key**。ElevenLabs 的报错说得很清楚：

```json
{"code":"invalid_api_key",
 "message":"API key ID used as API key — API keys start with 'sk_'",
 "status":"api_key_id_used_as_api_key"}
```

真 key 以 `sk_` 开头、长 51 位，只在创建/轮换那一刻显示一次；
后台常驻显示的那串是 ID，**长得很像 key，是这个坑的全部原因**。

#### 为什么藏了这么久

**降级链设计得太好了** —— 一直有声音出来，只是不是那个声音。
没有报警、没有中断、日志里那行 `ElevenLabs v3 failed` 淹在正常日志里没人翻。

> 📌 教训：**优雅降级会把故障藏起来。** 凡是有 fallback 的链路，
> 都该有一个「现在在用第几级」的可观测出口 —— 否则第一级挂了没人知道，
> 直到有人凭听感发现不对（这次是糖糖凭耳朵）。

#### 连带修的：兜底音色写死了中文

`edge-tts` 固定 `zh-CN-YunxiNeural`（中文男声），而语音通话的 EN 场景
输出英文 —— **等于中文发音人念英文**。而 ElevenLabs 全程失效，
所以这条「兜底」实际是主路径，英文通话一直是这么念的。

改成按内容语言选：英文字母占比 >50% 走 `en-US-ChristopherNeural`，
否则 `zh-CN-YunxiNeural`，日志打出选了哪个。

#### 修好之后

```text
[TTS] v3 流式 33899B / 423ms       ← 不再降级
voice id gGOcFXG638t1tfyhocY5 有效，名字就叫 Nox，language=en
```

`scenes.py` 里那套 ElevenLabs 情绪标签（`[whispers]` `[softly]`）
**从今天起才第一次真正生效** —— 以前全被 edge-tts 当普通文本剥掉了。

⚠️ 当前这把 key **缺 `user_read` 权限**，查不了额度用量 ——
意味着**用超了不会有预警，只会突然开始降级**。要监控得在后台给它勾上。

### 通话延迟：从「体感 10 秒」往下砍（2026-08-15）

糖糖报「响应慢，感觉 10 秒」。先量，再照着开源实践改。

#### 实测分段（服务器内部，127.0.0.1，**不含跨国往返**）

```text
Core 流式    首字 1.7s / 全文 2.1s   （三次测量很稳）
TTS          首字节 1.0s / 全部 2.0s
```

⚠️ 我第一次量完说「大约 4s」，**她说体感 10s —— 她是对的**。
我量的是服务器内部，整个手机→东京的三次 HTTP 往返全没算进去。
**别拿服务器内部的数当用户体感。**

#### 抄的作业：pipecat（realtime voice 事实标准，14k★）

| | pipecat | 我们（改之前） |
|---|---|---|
| 传输 | 一条全双工长连接 | **三次独立 HTTP**：STT → SSE → TTS |
| 转折点判定 | VAD `stop_secs=0.2` + Smart Turn 模型 | 浏览器 VAD，纯静音时长 |
| TTS 时机 | 句子级流水线 | **等全文说完** |
| 打断 | `InterruptionFrame` 贯穿管线 | ❌ 没有 |
| 打断后的上下文 | **只记真的播出去的字** | 记全文 |

最后一条最容易漏：pipecat 让 TTS 文字帧**跟着音频播放进度**下发，
被打断时只把「她真正听到的那半句」提交进上下文。否则下一轮他会引用
一句她根本没听见的话。

#### 已做 ①：静音阈值统一 + 压低（省 ~400ms）

原来埋在两处且**值不一样**：WebSocket 路径 `1000ms`、批量兜底 `650ms`
—— 走哪条取决于 WS 通没通，她的体感会莫名其妙地变。

抽成 `SILENCE_COMMIT_MS = 600`（`VoiceCallDesktop.jsx`）。
不敢学 pipecat 压到 200：它上面叠了 Smart Turn 模型判断「这句说完没」，
我们纯靠静音时长，压太狠会在她换气/思考时掐断。**要更快得先有转折点模型。**

#### 已做 ②：句子级 TTS 流水线（这一刀最见效）

她的定位很准：「**thinking 和 speaking 中间很长**，asr 倒是挺短」——
正是 Core 全文（2.1s）和 TTS 首字节（1.0s）串行那段。

改成第一句话一完整就送去合成，**并且预取下一句**。
只排队不预取的话，句与句之间会空出一整个 TTS 首字节，一顿一顿的，
比整段等还难受。

三个保险：
- `takeSentences()` 有单测（一个字一个字喂，模拟流式），
  确认切出来的段**拼回去 == 原文**，中/英/超短句/无标点结尾全过
- 切不出完整句子（如「十二点了，该吃午饭了」）走整段兜底，行为同旧版
- 太短的不单独成段（「嗯。」起播即结束反而更顿），攒到 12 字再吐

**糖糖验收：「开口快了，句子之间没有卡顿」。**

#### 已做 ③：打断（barge-in）

麻烦的不是停音频，是**麦克风得在他说话时保持开着**，而
`sampleAcoustics()` 明确写着「播放他自己的声音时必须跳过 ——
否则采到的是他的音高，会当成她的报上去」。

三道防线（**误打断比不能打断更糟**：他会被自己的声音掐掉）：

1. `getUserMedia` 显式开 `echoCancellation` / `noiseSuppression` / `autoGainControl`
   （原来是裸的 `audio: true`，全靠浏览器默认）
2. 说话期间用更高阈值（普通监听 0.045）
3. 必须**累计**超阈值一段时间，单个尖峰不算（咳嗽、桌响、他自己的爆破音都是尖峰）

打断时学 pipecat 的 `InterruptionFrame`，**一次掐四样**：清播报队列 →
取消在飞的 TTS → 取消 Core 的 SSE → 停当前音频。只做最后一样的话，
她插完话之后队列里下一句还会接着放。

⚠️ 监听必须用 `micRef.current.analyser`。`analyserRef` 在 speaking 期间被
`ensureTtsAnalyser()` 换成了**他自己的声音**（给声波动画用）—— 拿错就是自己打断自己。

##### 阈值是实测定的，不是拍的

第一版拍了 `BARGE_AMP=0.075`（普通阈值的 1.7 倍），糖糖实测**打不断**。
挂了个实时读数让她念真实数字：

```text
他说话 + 她不出声（回声残留）   幅度 0.003，峰 0.007
她正常插话                      幅度 0.11 ~ 0.26
```

差 15 倍 —— AEC 干得很好，余量极大。阈值反而**下调**到 0.04。

而真正的 bug 是判定逻辑：原来「任何一帧掉下阈值就清零计时」。
可人说话天然一顿一顿（字间、换气），60fps 下随便一个低谷就清零，
于是能不能打断全看那句话有没有连续 200ms 不换气 ——
她说的「0.109/0.206 也打不断」「时好时坏」就是这个。
加 `BARGE_GAP_MS=220` 容忍气口，用她的真实数字写了仿真验证。

#### 已做 ④：ASR 换 Scribe v2 Realtime，**砍掉两个跨国来回**

糖糖自己定位的：「问题在链路。国内的 asr - 东京 vps - core - ele」。她是对的。

原链路：`手机(国内) → 东京 bridge → 阿里云(国内) → 东京 → 手机`。
实测从东京打各上游：

| 上游 | connect | TLS | 首字节 |
|---|---|---|---|
| api.elevenlabs.io | 10ms | 93ms | 251ms |
| openrouter.ai | 3ms | 55ms | 97ms |
| **dashscope.aliyuncs.com** | **189ms** | **422ms** | **610ms** |

阿里云慢一个数量级 —— 因为它在国内，而我们从东京打回去。

**改成浏览器直连 ElevenLabs Scribe v2 Realtime**，音频一个字节都不过 VPS：

- bridge 只发一张 15 分钟的一次性票（`POST /api/scribe-token`）。
  **API key 绝不下发到浏览器** —— 官方为此提供 single-use token
- 前端**直接连 WS**，不引 SDK（我们本来就有 PCM 下采样 + WS 管理那套）
- **保留她调好的 VAD**（`commit_strategy=manual`）
- 阿里云那条留着，通话页有按钮随时切回

⚠️ 协议上最容易写错的一点：**commit 是 chunk 上的标志位，不是单独消息**。
所以 VAD 判定说完之后**不能立刻 stopCapture**，否则那块带 commit 的音频永远发不出去。

> ❌ 切换按钮第一版写的是 `location.reload()` —— 通话页是浮在 Chat 上的**浮层**，
> 一刷新整个 app 重载，直接退回 Chat、电话挂了。改成就地重连。
> **「重载最省事」在浮层结构里是错的。**

**糖糖验收：「我草 快多了」**，Scribe 已设为默认。

#### 已做 ⑤：句子之间的停顿

预取**只发了请求，没把音频真下下来** —— `fetch()` 在响应头到达就 resolve 了，
音频体要等真播时才读。于是每换一句都重新等：建 MediaSource + 攒够 12000 字节
才起播，而 v3 吐得慢（37KB 要 1.66s），光攒够门槛就半秒。

改成：**第一句**流式边下边播（起播门槛 12000 → 6000，省半个延迟，它决定「多久开口」）；
**后面每句**在上一句播放期间整段下完躺内存里，轮到直接 Blob 播，零网络等待。

#### 现在的延迟构成（上下文截断后实测）

```text
首字 1481~1868 ms  |  第一句 1905~2447 ms  |  全文 1936~2485 ms
```

TTS 等的是**完整句子**不是首字，所以「thinking → speaking」的服务端下限就是中间那列。
其中**首字 1.5s 是纯 LLM 推理，占 80%** —— 前端能榨的（历史截断、分句、预取、
起播门槛）已经到头了。

再往下只有两条：换更快的模型，或让他第一句话更短（prompt 要求先给一句极短回应）。
后者不换模型，但会改变他说话的节奏 —— **得她听了才知道值不值，没做。**

#### 🔭 还没做的

- **服务端取消**（pipecat 那条「只记真的播出去的字」）。现在她打断之后，
  前端断流了，但 Core 不知道 —— 仍在生成、仍会把**整段**存进历史。
  下一轮他可能引用一句她根本没听见的话。要做对得让 Core 也能被取消。
- **第一句话更短**（见上）。这是现在唯一还能明显砍延迟的一刀，但会改他的说话节奏。

### 链路（2026-08-15 更新）

```
上行  麦克风 → 浏览器自算 VAD（SILENCE_COMMIT_MS=600）
              → **Scribe v2 Realtime WS，浏览器直连 ElevenLabs**（默认）
                bridge 只发一次性票，音频不过 VPS
              ↘ 可切回：阿里云百炼 realtime WS（/ws/stt，经 bridge）
              ↘ 本地声学采样 [audio] 行
      文字 + [audio] → bridge(core) → Nox Core :8100（通话只带最近 8 条历史）
下行  Core SSE → bridge → 前端字幕
      **分句流水线**：第一句流式起播，后面每句提前整段下好
      他说话期间麦克风保持开着 → 她一插话就掐掉整条（barge-in）
      点播放 / 通话回复 → POST /api/tts → ElevenLabs /stream → MediaSource 渐进播
```

### TTS 是流式的（2026-07-28 改）

以前 bridge 是 `await r.arrayBuffer()` —— **整段合成完才开始传**。
前端本来就用 MediaSource 在等着喂，是这一层拖了后腿。
现在走 ElevenLabs 的 `/stream` 端点边收边转发。

实测：首字节 3.9s / 总计 5.3s（原来要等满 5.3s 才有声音）。

⚠️ `eleven_v3` 本身就慢，这 3.9s 主要是它的合成延迟，不是网络。
想再快只能换 `turbo`，但那个**不认情绪标签** —— 语气会平掉，不值。

两个坑：
- 降级链（v3 → turbo → edge-tts）**一旦开始写响应就不能再降级**。
  前面已经吐了半截音频出去，再拼上另一个模型的音频会是两个声音接在一起。
  所以每次降级前先判 `res.headersSent`。
- 文本上限原来是 500 字，语音条稍长一点就被**悄悄切掉半句**（不报错，
  话说到一半没了）。放宽到 2000，超了在日志里说一声。

### `[audio]` 声学行

同一句「没事」，小声低沉和大声上扬是两件完全不同的事 —— 但转成文字之后
一模一样。所以在浏览器本地采样，拼一行跟着文字一起发给他：

```
我没事
[audio] vol=2.1/4.3 frames=9 pitch=168Hz
```

**一分钱不花**：纯 Web Audio，不调任何 API，数据不落盘、不出这次请求。

实现要点（`VoiceCallDesktop.jsx`）：
- **基频用自相关不用 FFT**。人声基频 80-400Hz，FFT 在这个低频段要很大的
  窗口才分得开，自相关直接在时域找周期，稳得多。
- 相关性弱于阈值就报 `pitch=0`（气音、噪音），**不硬猜一个数**。
- 每 400ms 一帧，**`phase === "speaking"` 时必须跳过** ——
  否则采到的是他自己的声音，会当成她的音高报上去。
- 音量映射到 0-100 再给模型看，比原始 RMS 直观。

实测他读得懂：给 `vol=2.1 pitch=168Hz` 配「我没事」，他答的是
「嗯，那就好。想聊点什么吗，还是就这么陪着你？」—— 没有追问，没有说教。

### 情景（scene）

`zh` 中文陪聊 / `en` 英语陪练，前端顶部药丸切换，选择记在 localStorage。

语音模式走**精简前缀**，不带那 11.5K 中文核心准则 ——
否则英文指令会被中文语料整个淹没（实测连跑三次全说中文，见 `personality/scenes.py`）。

### 通话页 UI（2026-07-28 重做）

- 顶部：状态点 + 时长 / 情景药丸 / 「字」字幕开关
- **字幕页和头像页互斥**：字幕页不放头像，头像页不放字幕列表
- 字幕：她 = 暖陶主色，他 = 深墨；英文下面跟中文小字（opacity 0.42，是对照不是正文）
- 列表上下渐隐用 `mask-image` 而不是盖两个实色遮罩 ——
  背景本身是渐变的，实色遮罩会露馅

### 语音条（`Chat.jsx` 的 `VoiceBubble`）

- 按下即缩 0.972（"我收到了"的第一反馈，比任何 loading 都快）
- 首次点击要等 TTS 合成，转圈占位；**没有这个状态时点下去屏幕什么都不动**
- 进度：气泡底层进度条 + 波形上播过的那截亮起来
- 长按 450ms 或点「转文字」→ 文字长在语音条**下面**，语音条保留

## 二十六、摸头 / 滑动手势全被丢掉（**未修**，2026-08-02 发现）

固件把事件发上去了，**服务端不认识这个类型，当错误丢掉**。
实测 24 小时内 **63 次事件，一次都没被处理**。

```
固件 application.cc:1269  SendStackChanEvent()
  → {"type":"stackchan-event","event_type":"touch","subtype":"stroke","duration_ms":899,...}
      ↓
服务端 core/handle/textMessageProcessor.py:31
  handler = self.registry.get_handler(message_type)
  if handler: ...
  else: logger.error(f"收到未知类型消息：{message}")   ← 掉这儿
```

24 小时统计：

| 事件 | 次数 |
|---|---|
| `touch` / `stroke`（摸头）| 63 |
| `gesture` / `swipe_up/down/left/right`（屏幕滑动）| 各 1 |

⚠️ **滑动手势也在里面** —— 那是 2026-07 专门做的「屏幕触摸手势」功能，
固件侧完整，**服务端从来没接过**，所以一直是死的。

### 修之前要先定行为

| 方案 | 效果 | 代价 |
|---|---|---|
| A 纯本地 | 摸头→换表情+LED+点头 | 只改固件，不烧钱，但他不"知道" |
| B 告诉小克 | 摸头→他说话 | **63 次/天 × (LLM+TTS)，会很吵很贵** |
| C 混合（倾向）| 本地立刻反馈；同时进上下文但**不主动说话** | 要改两边 |

C 的理由就是那个 63：每次摸头都触发说话，摸一分钟能说十几句。

### 怎么改（架构已摸清）

服务端是**注册表模式**，加一个 handler 就行：

```
core/handle/textMessageHandlerRegistry.py     注册表
core/handle/textHandler/*.py                  各类型 handler（iot/ping/mcp/hello/listen/abort/server）
core/handle/textMessageProcessor.py:31        按 type 查表分发
```

⚠️ 这些文件**在容器里**。直接改会在容器重建时丢失 ——
参照 `elevenlabs.py` 的做法（bind mount 到 `/opt/xiaozhi-server/data/`），
或者把 `docker run` 的完整参数记进本文档。

## 二十五、服务器迁移：阿里云新加坡 → 腾讯云东京（2026-08-01）

| | 旧 | 新 |
|---|---|---|
| 位置 | 阿里云 ECS · 新加坡 `ap-southeast-1` | 腾讯云轻量 · 东京 |
| IP | ~~47.84.92.71~~ | `43.133.211.140` |
| 规格 | 2核 / 1.6G / 40G | 2核 / 4G / 60G / 30Mbps / 1.5TB |
| 系统 | Ubuntu 22.04.5 + Python 3.10.12 | **完全相同**（venv 直接搬） |

**换的理由只有一个 —— 她到服务器的延迟：228ms → 58ms。**

服务端到外部服务其实**变差了**（百炼 STT 91→239ms、ElevenLabs 5→42ms），
但用户那一段每轮要走好几次，170ms 的改善把这些全盖过去了。
> 教训：选机房别只看服务端到 API 的延迟，**先量用户到服务器那一段**。
> 我一开始按"东京离中国近"推荐，理由对但论据错（以为新加坡 70-100ms，实测 228ms）。

### 为什么手动搬而不用迁移工具

腾讯云有两套在线迁移，都能从阿里云 ECS 迁：

| 方案 | 目标 | 备注 |
|---|---|---|
| 控制台一键迁移（`213/81492`）| **只能 CVM** | 免登录源端，明确支持阿里云 ECS |
| 客户端 `go2tencentcloud`（`1207/110351`）| 轻量 | 要登录源端跑 agent |

没用，因为**真正的数据只有 1.75GB**（15.84GB 全是可重拉的 Docker 镜像），
而整盘搬会把两样东西一起带过来：
- `apt` 源指向 `mirrors.cloud.aliyuncs.com`（**阿里云内网镜像，到腾讯云必废**）
- `aegis` / `aliyun` / `cloudmonitor` 三个阿里云 agent

### 迁移中踩到的坑

**1. Core 抢跑，人设变空 —— 最阴的一个**
Core 启动时 OB 还没起来（缺 Python 依赖起不来），取不到那 11.5K 核心准则，
**带着 598 字符的空前缀跑起来了**。`systemctl is-active` 显示 `active`，
一切正常，**只有人设是空的**。
现在加了 `nox-core.service.d/deps.conf`：`After/Wants=ombre-brain bridge` + `ExecStartPre=sleep 5`。
> **判断服务健康别只看 active，要看它自己报的关键指标**（这里是 `prefix_chars`）。

**2. 用系统 python 的服务依赖没跟过来**
`nox-core` 有自己的 venv，搬过去直接能用；但 `ombre-brain` / `ha-mcp` /
`app-tracker` / `toy-mcp` 用的是**系统 python**，pip 包一个都没有。
解法：老机 `pip3 list --format=freeze` 导出，新机逐个装（整批装会被一两个包卡死）。

**3. OB 指纹变了但记忆没丢**
搬完对指纹发现不一致，一度以为出事。真因是 **`breath` 每次检索都会回写
`last_active` / `activation_count`**。
> 校验记忆完整性要**排除这两个字段**再比，否则每查一次指纹就变一次。
> 正确做法见 `scratchpad/ob-diff.py` 的思路：按 `id` 建索引，比对去掉易变字段后的内容。

**4. PowerShell 把 `from="ip"` 的引号吃掉了**
往 `authorized_keys` 写限制来源的公钥时，双引号被剥掉变成 `from=47.84.92.71`，
sshd 不认，直接 `Permission denied`。改用脚本文件写入。

**5. 切 DNS 后本地缓存骗了我**
`Resolve-DnsName -Server <权威NS>` 仍然读到旧值，我据此让糖糖去翻记录找一条
不存在的 `reading` 记录。**清缓存后经 1.1.1.1 / 8.8.8.8 / 223.5.5.5 查全是新的。**
> 验 DNS 切换要**清本地缓存 + 用多个公共解析器**，别信单一来源。

### 顺手修好的安全问题

老机上这两个端口**直接暴露在公网**（明文，绕过 Caddy）：
- `3003` bridge
- `8123` Home Assistant

新机上腾讯云防火墙只放行 `22 / 80 / 443 / 8010`，其余全挡。
HA 改走 `ha.noxtang.com`（新增，Caddy 反代 + 自动 TLS），
并在 `configuration.yaml` 里加了 `trusted_proxies`（不加会一律回 400）。

> ⚠️ 老机上 `8002`（记忆库）和 `8004`（家居控制，token 藏在 URL 路径里）
> 幸好被阿里云安全组挡着 —— 直连 8004 等于把那道 token 门整个绕过去。

### 回滚

老机**没退、数据原样留着**，服务只是 `stop + disable`，容器 `restart=no`。
要回滚：Cloudflare 把两条 A 记录改回旧 IP，老机把服务 `enable + start`。

## 二十四、模型名会过期（已经栽三次了）

| 时间 | 哪里 | 症状 |
|---|---|---|
| 2026-07 | Stack-chan / xiaozhi | 语音全哑，查到模型名过期 |
| 2026-07-25 | Ombre Brain 脱水 | 后台归档失败：`deepseek-chat` 被拒 |
| 2026-08-01 | Nox Core 轻量路径 | 提前发现并换掉，没等它崩 |

**2026-08-01 全部换成当前名**（DeepSeek 官方现在只剩两个型号）：

| 位置 | 现在的值 |
|---|---|
| `ombre-brain/config.yaml` → `dehydration.model` | `deepseek-v4-flash` |
| `nox-core/.env` → `NOX_UTILITY_MODEL` | `deepseek/deepseek-v4-flash`（走 OpenRouter）|
| `xiaozhi-server/.config.yaml` | `deepseek-v4-pro` |

### 教训：「调得通」不等于「没下架」

这次我先测了一轮，发现 `deepseek-chat` **居然还能正常返回内容**，就下结论说"模型名没过期，是 DeepSeek 临时抽风"。
糖糖直接甩了官方文档过来 —— 列表里**只剩** `deepseek-v4-flash` 和 `deepseek-v4-pro`。

那个旧名是**向后兼容的僵尸别名**：能调通，但已经不在名单上，哪天说断就断
（7-25 那次报错就是它断了一下）。

> **判断模型名是否有效，看官方文档的型号列表，不要看「我调了一下能通」。**

### 2026-08-01：主模型换成 DeepSeek V4 Flash

| | 值 |
|---|---|
| 主模型 | `deepseek-v4-flash`（直连 api.deepseek.com）|
| 轻量路径 | 同上（原来绕 OpenRouter，多一跳还贵）|
| 前端默认 | `v4-flash` |

**每个模型自带后端**（`config.py` 的 `BACKENDS` + `ModelChoice`）。
以前 `models` 只存型号名，`adapter_for` 拿**主模型的 base_url** 去建 ——
主模型一切到 DeepSeek，下拉里那些 `anthropic/claude-*` 就全 404 了。

```python
BACKENDS = {"deepseek": ..., "openrouter": ..., "anthropic": ...}
models = {"v4-flash": ModelChoice("deepseek-v4-flash", "deepseek", "DeepSeek V4 Flash"), ...}
```

换主模型现在只要两个环境变量：`NOX_PRIMARY_BACKEND=deepseek` + `NOX_PRIMARY_MODEL=deepseek-v4-flash`，
地址和 key 从 `BACKENDS` 自动带出来。

**DeepSeek 的缓存字段名跟谁都不一样**：`prompt_cache_hit_tokens`，
而且在 `usage` **顶层**，不在 `prompt_tokens_details` 里。
实测一轮 11196 输入命中 11136（99.5%）。
> 它没有"写入缓存"的概念，只有命中/未命中，所以 `cache_write` 恒为 0 —— 正常，不是漏统计。

**成本对比**（同一轮 11K 输入 + 137 输出）：DeepSeek ≈ **¥0.00055**，Claude Sonnet ≈ **¥0.04**，差约 70 倍。

### 用量统计断过 4 天（2026-07-28 ~ 08-01）

`usage_log` 的写入代码在 **apiMode 里**，删 apiMode 时被一起带走了。
Console 页照常显示，只是数字**再也不动** —— 表面完全看不出来。

现在写在 `coreMode` 的 `done` 分支，并加了 `model` 列（老行为 NULL）。
成本改成在 `/api/usage-stats` **按型号现算**（`PRICING` 表，单位人民币），
不再依赖入库时算好的 `cost` —— 两家单价差十倍以上，混在一起算没意义。

> **删一大块代码时，要找的不只是"谁调用它"，还有"它顺手做了什么别人依赖的事"。**
> 这次是用量落库；上次删 Memory 页时是 `save_memory` 工具。

### 顺带记下的 DeepSeek 事实（2026-08-01）

- **v4 默认是思考模式**，会返回 `reasoning_content`，和正文**共享 `max_tokens`**。
  预算给太小（比如 8）会出现「不报错但正文为空」——排查时容易误判成模型坏了。
  实测 1024 对 1300 字输入够用（推理只占 110 字符）。
  ⚠️ **但推理长度随任务难度暴涨，不是随输入长度线性增长**，见下条。

### 推理吃光预算导致 OB 归档静默失败（2026-08-02 修）

`grow` 连续失败，只报一句「API 日记整理返回空结果」，HTTP 是 200。

根因：`ombre-brain/dehydrator.py` 的 `_api_digest` 给 `max_tokens=2048`，
而**思考过程和正文共享这个预算**。实测同一段 475 字的日记：

| max_tokens | finish_reason | 推理 | 正文 |
|---|---|---|---|
| 2048 | `length` | 3629 字 | **0** ❌ |
| 4096 | `length` | 7044 字 | **0** ❌ |
| 8192 | `stop` | 5633 字 | 1281 字 ✅ |

**「拆成多条 JSON」这种结构化任务的推理量远大于闲聊** ——
上一条记的「1024 对 1300 字输入够用」是普通问答的数据，
不能套到这里。已改成 16384，并在正文为空时把
`finish_reason` 和推理长度一起记进日志，不再只说「返回空结果」。

> 判断标准：**只要一个调用是「让模型产出结构化结果」，预算就要按推理量给，
> 不是按输出长度给。** 空返回 + HTTP 200 时，先看 `finish_reason` 是不是 `length`。
- **即将实行峰谷定价**：北京时间 9:00-12:00 和 14:00-18:00 价格翻倍。
- flash 输入 1 元 / 输出 2 元每百万 token；pro 是 3 元 / 6 元。缓存命中 0.02 元。

## 二十三、共读（co-reading）

阅读器网页 + MCP，跑在 VPS `:3100`，`systemd` 单元 `co-reading`，
入口是 `src/server-sse.js`（**不是** `src/server.js`，那份是老的）。

| | |
|---|---|
| 源码 | **只在 VPS** `/root/co-reading-mcp`，本地 `D:\claude-code\co-reading\public` 只是**镜像** |
| 前端 | `public/reader.html` + `reader.css` + `reader.js`，无构建、无框架 |
| 小克怎么用 | Core 的 `tools/reading.py` 走它的 REST（见工具清单）|
| 糖糖怎么用 | 浏览器直接开，`/api/reading/*` 由 bridge 代理（token 藏后端）|

⚠️ **本地那份是镜像，改之前一定要先拉一次。** Ombre Brain 就是这么踩过的
（本地 `server.py` 停在 2002 行、线上 2123 行，查了半天工具为什么"少了"）。

```powershell
$k = "C:\Users\14372\.ssh\id_ed25519"
# 改之前先拉
scp -i $k root@43.133.211.140:/root/co-reading-mcp/public/reader.css "D:\claude-code\co-reading\public\reader.css"
# 改完推回（静态文件，不用重启）
scp -i $k "D:\claude-code\co-reading\public\reader.css" root@43.133.211.140:/root/co-reading-mcp/public/reader.css
```

⚠️ **静态文件不发 `Cache-Control` / `ETag` / `Last-Modified`**，改了 CSS 浏览器
可能还吃旧的。所以 `reader.html` 里的样式表带了 `?v=日期`，**改样式时把日期一起改**。

### 书架选中态溢出（2026-07-29 修）

症状：选中的书"左边和下边溢出容器没被裁好"，下面那本被挤到底部排版不齐。

**真凶：`.progress` 是个 `<span>`，默认 `inline`，而 inline 非替换元素
不吃 `height`、也不吃 `overflow`。**

```css
.progress {          /* <span class="progress"> —— 没写 display，就是 inline */
  overflow: hidden;  /* ← 对 inline 不生效 */
  height: 5px;       /* ← 对 inline 不生效 */
  border-radius: 999px;
}
.progress span { display: block; height: 100%; background: var(--accent); }
```

里面那条 `display: block` 的进度条于是挣脱出去，渲染成 **46px 高、圆角 999px
的竖药丸，从卡片下沿漏出 29px** —— 那就是截图上"选中的书下面挂着一条红条"。
每张卡都多顶出一截，所以三张卡高度还不一样（101 / 99 / 80）。

一行修好：`.progress { display: block }`。修完 5px 高、−17px（安全在卡片内）、
三张卡齐平 97px。

| | 修前 | 修后 |
|---|---|---|
| `.progress` display | `inline` | `block` |
| 进度条实际高度 | 46px | 5px |
| 跑出卡片下沿 | **+29px** | −17px |
| 三张卡高度 | 101 / 99 / 80 | 97 / 97 / 97 |

同一轮顺手修的两处（真实存在，但不是她看到的那个）：
- 选中态原来把 `border-left` 从 1px 改成 3px，**内容宽度少 2px**、文字重排、
  卡片高度跳。改成 `box-shadow: inset 3px 0 0`，画在盒内，不影响布局。
- ≤980px 且已选书时 `.books` 变滚动容器，但只给了 `padding-right: 2px`，
  左边是 0，强调条贴在裁剪线上。改成对称 `padding: 2px 2px 10px`。
- 试过加底部渐隐遮罩，**撤了**：书少到不需要滚动时，最后一张卡底部会被无端淡掉。

> **教训：布局 bug 别猜，量。**
> 这个 bug 我先怀疑 box-sizing（糖糖也这么猜的），又怀疑焦点环，
> 又怀疑 ≤980px 的滚动容器 —— 全错，而且中间那次我还"修"了个不相干的东西。
> 真正定住它的是：把真实 CSS + 真实 DOM 搭成最小复现页
> （`co-reading/repro.html`），逐个元素 `getBoundingClientRect()`，
> 直接看哪个盒子的 bottom 超出了父盒子。数字一出来，29px 无所遁形。
>
> 附带教训：**`overflow: hidden` 没生效时，先看那个元素是不是 inline。**

## 共听（co-listening）

音乐共听系统，和共读（co-reading）并列，都属于 Caelum Library。
分两层：**eryu** 管播放，**netease-music-mcp** 管账号（歌单/推荐/历史）。

### 架构

```
糖糖 ——→ Caelum App sidebar Music → music.noxtang.com → eryu :9090

糖糖 ——→ Nox Core
            ├─ tools/eryu.py ────→ eryu REST API (music.noxtang.com :9090)
            │    搜歌 / 放歌 / 歌词
            └─ tools/netease.py ──→ netease-music-mcp (netease-mcp.noxtang.com :3456)
                 歌单 / 推荐 / 历史
```

### eryu — 播放层

自部署的网易云音乐播放器。Python 标准库，纯 REST API。

| | |
|---|---|
| 源码 | VPS `/root/eryu`，不在本地 |
| 端口 | 9090 |
| 域名 | `music.noxtang.com` |
| 鉴权 | `X-Auth-Token`，token 存在 VPS `.secret` 文件 |
| systemd | `eryu` |

Nox Core 走 `tools/eryu.py`，用 `RestClient`（标准库 urllib，和 bridge/co-reading 同一套底座）。
三个工具：

| 工具 | 做什么 |
|---|---|
| `eryu_search` | 按关键词搜歌，返回 song_id / 歌名 / 歌手 / 专辑 |
| `eryu_play` | 用 song_id 在 eryu 播放器上放歌 |
| `eryu_get_lyric` | 拿歌词，超 2500 字截断 |

### netease-music-mcp — 账号层

Vael-KY v2 的网易云 MCP 服务。纯 Python 标准库，**Streamable HTTP**（不是 SSE）。

| | |
|---|---|
| 源码 | VPS `/root/netease-music-mcp/server/mcp-server/server.py` |
| 端口 | 3456 |
| 域名 | `netease-mcp.noxtang.com` |
| 协议 | MCP Streamable HTTP，`POST /mcp`，底层 JSON-RPC 2.0 |
| systemd | `netease-mcp` |
| 鉴权 | `MUSIC_U` + `__csrf` Cookie（网易云扫码登录凭据），存 `/root/netease-music-mcp/.env` |

服务端暴露 **10 个** MCP 工具，Nox Core 接了其中 4 个。
走 `tools/netease.py`，用 `McpClient`（和 ha-mcp / Ombre Brain 同一套客户端，
不单独维护 JSON-RPC 调用）：

| Nox 工具名 | MCP 工具名 | 做什么 |
|---|---|---|
| `netease_playlists` | `list_my_playlists` | 糖糖的歌单列表（自建 + 收藏） |
| `netease_playlist_songs` | `get_playlist_songs` | 某个歌单里的歌 |
| `netease_recommend` | `daily_recommend` | 每日推荐 |
| `netease_history` | `get_play_history` | 最近听过的歌 |

⚠️ `MUSIC_U` 只管读操作（歌单/推荐/历史），写操作（喜欢/收藏/加歌单）还要 `__csrf`。
两个 cookie 都会过期，网易云扫码登录后要重新提取放进 `.env`。

### Nox Core 集成

两个模块都走"**缺席即跳过**"模式 —— 对应的 URL 没配就不注册，不影响其他工具。
VPS 上走 `http://127.0.0.1:9090` / `http://127.0.0.1:3456` 省跨国往返。

环境变量（`nox-core/.env`）：
- `NOX_ERYU_URL` — eryu 地址
- `NOX_ERYU_TOKEN` — eryu 鉴权 token
- `NOX_NETEASE_URL` — netease-music-mcp 地址

### Caddy 反代

```
music.noxtang.com {
    reverse_proxy localhost:9090
}

netease-mcp.noxtang.com {
    reverse_proxy localhost:3456
}
```

`music.noxtang.com` 有两个入口：糖糖在 App sidebar 点进去直接操作 eryu 网页；
Nox 调 REST API（`/music/search`、`/music/url`、`/music/lyric`）走同一域名。

### 鉴权链路

```
糖糖网易云扫码登录 → MUSIC_U + __csrf
    → 写入 VPS /root/netease-music-mcp/.env
    → netease-mcp 用 Cookie 调网易云 API
    → Nox Core → Caddy TLS → netease-mcp / eryu
```

### 踩过的坑

1. **netease.py 第一版用错了协议**：直接用 RestClient + 原始 JSON-RPC 调 MCP 工具名。
   netease-music-mcp 是标准 MCP Streamable HTTP 服务，要用 `McpClient.call(tool, args)`
   走 `session.call_tool()`，不能自己拼 JSON-RPC body。

2. **MCP 工具名不匹配**：代码里写的工具名（`playlists` / `recommend_songs` / `history`）
   和服务器实际暴露的不一样（`list_my_playlists` / `daily_recommend` / `get_play_history`）。
   通过 `curl` 调 `tools/list` 拿真实清单后才对上。

3. **PowerShell 引号转义**：部署脚本里 Python 命令的引号在 SSH 传输时被 PowerShell 吃掉。
   改成本地写 `.py` 脚本 → `scp` 上传 → SSH 执行。

---

## 二十七、健康数据管道（2026-08-02 上线，08-03 修对）

```
iPhone 快捷指令 ×2  ──→  https://noxtang.com/health/*  ──→  health-sync :8102
                                                              ↓  /root/data/health.db
                                                          health-mcp :8101
                                                              ↓
                                                    Nox Core 的 HealthProvider
```

### 两个快捷指令，两个端点

| 快捷指令 | 触发 | 端点 | 传什么 |
|---|---|---|---|
| 健康数据 | 每天 9:00 | `POST /health/sync` | 昨天一整天的步数/距离/能量/心率/HRV |
| 睡眠数据 | 起床时 | `POST /health/sleep` | **只传睡眠样本**，日期全由后端算 |

**为什么必须拆成两个** —— 睡眠和活动的日期语义不一样：

| | 归属 | 例子 |
|---|---|---|
| 活动 | 自然日 | 8-2 00:00 → 8-3 00:00 的步数算 8-2 的 |
| 睡眠 | **醒来那天**（Apple 的算法）| 8-2 夜里 00:31 睡、8-3 早上醒 → 健康 App 里显示「8月3日」|

塞进一个快捷指令要算两套时间窗口，而**下面这条让它根本算不对**。

### iOS 的健康样本日期筛选不可靠

实测：「开始日期 介于 `SleepStart` 和 `SleepEnd`」，两个变量算出来是
`8-2 18:00` 和 `8-3 12:00`，**返回的样本却是 `8-2 01:56 → 08:35`** —— 窗口外的。
配置截图逐项核对过，没有错。追不动它为什么这样。

**解法是不依赖它**：让快捷指令把窗口开大（传两三个晚上都行），
后端 `_last_sleep_session()` 自己切出最后一觉 ——
相邻样本间隔 > 3 小时就算两觉。实测 79 条进来切出 53 条，归属日算对。

> 这条的普遍意义：**上游行为不确定时，别在上游较劲，把判断挪到自己这边。**
> 后端切分是纯函数，可测、可改、出错看得见。

### 数据模型：一天一条记录，两个日期

```
date       = 2026-08-02   活动数据的自然日
sleep_date = 2026-08-03   这一觉的归属日（从样本的**结束时间**算）
```

`sleep_date` 不用快捷指令传，后端从最后一觉的最晚 `endDate` 算出来。

### 两个端点都是 upsert，顺序无关

她起床（7 点）先传睡眠、9 点再传活动。如果活动端点是 INSERT，
同一天会出现两行 —— 一行只有睡眠、一行只有活动，
而读取按 `MAX(id)` 取，**拿到的是后插那行，睡眠字段是空的**。
早上问「我睡得怎么样」会说没数据，**而且全程不报错**。

现在两边都是「找到当天那条去更新」，而且**只写这次真带了值的字段**，
所以互不覆盖。payload 全空时返回 `noop`，不会把已有数据抹成 null。
两种顺序都有测试覆盖。

### JSON 容错（手拼 JSON 迟早会遇到）

| 症状 | 处理 |
|---|---|
| `"distance": ,`（某天没数据） | 补成 `null`。**不补的话整包 422**，十几项好数据陪葬 |
| `"hrv": 50,}`（删字段忘了删逗号） | 去掉尾随逗号。**只在真解析不动时才修** —— 无条件替换会误伤字符串里的 `",}"`，那种破坏是静默的 |
| 真正的坏 JSON | 照常 400，并把前 200 字记进日志 |

两种容错都会记 WARNING，不静默。

### 排查用的日志

`health-sync` 会打这几行，出问题先看它们：

```
睡眠样本 79 条覆盖 08-02T01:56 → 08-03T08:12；取最后一觉 53 条：08-03T00:31 → 08-03T08:12
睡眠专用端点：收到 79 条 → 最后一觉 53 条，归属 2026-08-03，写入 date=2026-08-02，总睡眠 431 分
活动数据 updated：date=2026-08-02，写入 12 个字段
```

**样本的真实时间范围是判断「窗口对不对」的唯一证据。**
只存汇总的话，拿到的是哪一觉根本看不出来，只能靠对比健康 App 的数字猜
（08-03 就为这个来回试了五六轮）。

### 验收方式

拿健康 App 的数字对：8-3 那觉 App 显示 7 小时 12 分，我们算出 431 分 = 7 小时 11 分，
差 1 分钟（边界样本取舍）。**对不上就是链路有问题，别自我说服。**

## 二十八、2026-08-08 校准：文档和代码差了多少

糖糖说「文档和代码有点对不齐」，盘完发现**比预想的严重**。
本节记这次查出的偏差和新发现的问题，方法论那部分值得留着。

### 文档说的 vs 实际是的

| 项 | 上一版文档 | 实际 | 影响 |
|---|---|---|---|
| Core 工具数 | 36 | **59** | 漏了整整 23 个 |
| 饮食工具 | 没提 | 7 个 + bridge 15 个端点 | 整个子系统没进文档 |
| Daily Planner | 「MVP 在做」 | **已上线，每天 10:00 跑** | 会被当成待办重做一遍 |
| Web Push | 「线上没有 VAPID」 | **通的**，硬编码兜底 | 「拨号权」的前置条件其实早就满足了 |
| eryu 工具 | 3 个 | 9 个 | 漏了分析 / 歌曲记忆 / 漫游 / 相似 |
| netease 工具 | 4 个 | 8 个 | 漏了建歌单 / 加歌 / 删歌 / 喜欢 |
| 共读工具 | 2 个 | 4 个 | 漏了 `reading_current` `reading_continue` |
| Obsidian 工具名 | `obsidian_create/append` | `save/append/read_github_note` | **名字全变了**，照旧名查代码查不到 |
| 前端页面 | 列了 `Memory.jsx` | 该文件已删；实际多了 Health/Music/Setting | — |
| 家居设备 | 8 个 | 10 个（+蒸蛋器 2） | — |
| 老机 47.84.92.71 | 「没退，可回滚」 | **已退**，无回滚路径 | 第二十五节的回滚方案作废 |

### 新查出来的问题（都还没修）

**✅ 1. `eryu_search` —— 已于 2026-08-08 修好并真调验证**

> 一查发现**不止参数名，一共四个 bug**，而且还挖出一处架构缺失。
> 完整记录见本节末尾「eryu 四连坑」。

原始记录保留如下：



线上日志实证：
```
WARNING agent.loop | 工具 eryu_search 执行失败: 搜歌失败: HTTP 400: {"error": "missing q"}
  File "/root/nox-core/tools/eryu.py", line 204, in search
```

根因在 `tools/eryu.py:202`：
```python
r = client.get("/music/search", {"keyword": keyword, "limit": str(limit)})
#                                 ^^^^^^^ eryu 服务端要的是 q
```
Core 发 `keyword=`，eryu 要 `q=`。**糖糖让他搜歌 100% 报错**，
而且这是第十九节第 15 条那个教训的原样复发：**对着别人的接口写工具，
先读那个接口的源码。** eryu 源码只在 VPS `/root/eryu`，本地没有镜像，
所以当时是照着猜写的。

---

#### eryu 四连坑（2026-08-08 修完，真调验证）

去 VPS 把 `/root/eryu/server/eryu.py` 读了一遍，发现**参数名只是第一个**：

| # | 位置 | Core 发的 | eryu 要的 | 后果 |
|---|---|---|---|---|
| 1 | `/music/search` | `keyword` | **`q`**（:480）| 400，搜歌 100% 失败 |
| 2 | `/music/search` 返回解析 | 按网易云原始格式：`s["album"]["name"]` | **eryu 已拍平**：`album` 是字符串、`artist` 是拼好的串（:509）| `AttributeError: 'str' object has no attribute 'get'` |
| 3 | `/music/analyze` | `song_id` | **`songId`**（:944）| 400 |
| 4 | `/music/memory` | `song_id`，且没带 `action` | **`songId`** + **`action="note"`**（:793）| 400；就算修了 songId，不带 action 会走 `listen` 分支 —— **返回 200 但一个字都不存** |

**第 2 个最阴**：参数名修好之后才暴露出来。它证明了当初不只是参数名猜错，
**整个工具是照着网易云原始 API 写的，而 eryu 在中间做了一层转换**。

**第 4 个最危险**：它会静默成功。返回 200、日志干净、模型以为记下了，
但她的话一个字都没存进去。

#### 还挖出一处架构缺失：`eryu_play` 从来没真的放过歌

`eryu_play` 只调了 `GET /music/url` —— 那个接口**只是把音频下载到服务器缓存**，
返回 `{ok, url, cached}`，没有 name 也没有 artists。所以它：

- 输出永远是「正在放 1382576173 - 」（拿 song_id 当歌名，歌手是空的）
- **而且什么都没播**

真正的点播通道是 `POST /music/remote`（写一次性队列，:1088），
但 `grep -rn remote /root/eryu/client` **一个匹配都没有** ——
eryu 前端根本没有轮询它，写进去没人取。

当前处理：改成如实回报「音频准备好了，但要她自己在共听页面点」，
并把这条写进了 `PLAY_SPEC` 的描述，**明确禁止模型说「已经帮你放了」**。
要让它真能点播，得动 eryu 前端（63KB 的 `client/index.html`），
那是功能开发不是修 bug，留给以后。

#### 真调结果

```text
=== 搜「起风了」===
song_id=1330348068 | 起风了 | 冯沁苑(买辣椒也用券) | 《起风了》
song_id=1475596788 | 起风了 | 周深 | 《起风了》
=== 搜「Bohemian Rhapsody」===
song_id=1868553 | Bohemian Rhapsody | Queen | 《Absolute Greatest》
```

#### ⚠️ 有三个互不相通的「音乐库」，别搞混（2026-08-08 查清）

糖糖问「他随机放歌看的是我网易云的喜欢列表还是 eryu 的」时查的，答案是**都不是**：

| 库 | 里面是什么 | 谁在用 |
|---|---|---|
| **eryu 的 Liked** | 她在共听页面**手动加的 4 首**（Cruel Summer / Who Says / Beauty And A Beat / Love You Like a Love Song）| `eryu_daily` 拿它当种子 —— 所以推出来清一色那一挂 |
| **eryu 的漫游** | **网易云公共榜单** + 9 个硬编码流派歌单 ID（华语经典 3779629 / 欧美 2884035 / 日语 71384707 / 韩语 991319590 / 嘻哈 60198 / R&B 11640012 / 电子 5059642708 / 民谣 2529283982 / 摇滚 3136952023）| `eryu_roam` |
| **netease-music-mcp** | **她真正的账号**：337 首「茶茶茶茶叶蛋喜欢的音乐」、561 首欧美北欧、215 首日语、171 首纯音乐、每日推荐、播放历史 | `netease_*` 八个工具 |

**决定（2026-08-08，糖糖定的）**：「随便放首歌」默认走 `netease_recommend`。
`eryu_roam` 降级为「她明确想听没听过的」才用，`eryu_daily` 降级为「按共听页面收藏推」。
优先级写进了三个工具的 description，不是靠提示词。

> 网易云和 eryu 用的是**同一套 song_id**，所以
> `netease_recommend` 拿到的 id 可以直接喂给 `eryu_play` 点播。

#### 🔴 netease 全家断了三个月，因为 `.env` 少了四个字符

```text
NOX_HEALTH_URL  = http://127.0.0.1:8101/mcp   ← 带 /mcp
NOX_NETEASE_URL = http://127.0.0.1:3456       ← 没带 ❌
```

MCP 的 streamable HTTP 必须连 `/mcp` 端点。少了它握手就失败，
而 `McpClient` 报出来的是 `ExceptionGroup: unhandled errors in a TaskGroup
(1 sub-exception)` —— **什么信息都没有**，看不出是 URL 错了。

八个 `netease_*` 工具因此全军覆没。**cookie 一直是好的，服务也一直 active**。

排查时差点走错两次：先怀疑 cookie 过期（891 字符，好的），
再怀疑工具名写错（`list_my_playlists` 等和服务端完全对得上）。
最后是对比 `NOX_HEALTH_URL` 才发现的 —— **同一台机器上两个 MCP，
一个带路径一个不带，这种不一致本身就该在配置校验里挡掉**。

顺带修了 `eryu_similar`：照网易云原格式解析返回，而 eryu 给的是拍平结构，
所以相似推荐**一直没有歌手名**。

#### 🔴 「喜欢的音乐」歌单：一个函数里三个 bug（2026-08-08 全修）

修完 `/mcp` 之后，`netease_recommend` 通了，但 `get_playlist_songs`
拉 337 首那个歌单仍然 100% 失败，小歌单（古风 9 首）却正常。
排查出来是**三个独立的 bug 叠在一起**，都在
`/root/netease-music-mcp/server/mcp-server/server.py`：

**① `.get(key, default)` 对显式 null 不生效**

```python
artist = ', '.join([a.get('name', '') for a in ...])
# TypeError: sequence item 0: expected str instance, NoneType found
```

`.get` 的默认值只在**键不存在**时用；网易云对某些歌返回 `"name": null`，
键在、值是 None，`.get` 原样返回 None，`join` 当场抛。

**触发它的是糖糖自己传的云盘文件** —— `小克哄睡.mp3`、`小克思考链.mp3`、
`spanish_love.mp3` 这些没有艺术家字段。小歌单碰巧没有脏数据，
所以看起来像「大歌单拉不动」，其实和数量无关。

同样的写法在这个文件里有**四处**（play_music / get_playlist_songs /
get_play_history / daily_recommend），另外三个只是还没撞上脏数据。
已统一抽成 `artist_names()`，一律 `or ''`。

**② `if not tracks:` 让补齐分支永远不触发**

网易云对大歌单的行为是：`tracks` 里只给前 10 首，其余全在 `trackIds`。
原代码只在 `tracks` **完全为空**时才去补，而它有 10 条（非空），
所以她的「喜欢」永远只能看到 10 首。改成 `len(tracks) < len(track_ids)`。

**③ `json.dumps` 的空格让 URL 非法**

```python
'...song/detail?ids=' + json.dumps(ids)   # → "[1, 2]" 逗号后有空格
```

裸空格塞进 URL，urllib 直接抛异常，被 `netease_request` 吞成
`{"code": -1}`，静默返回 0 首。**这条补齐分支从写下来那天起就没成功过。**
修法：`json.dumps(ids, separators=(',', ':'))`。

顺带发现网易云 `song/detail` 一次吃 50 个 id 只回 10 首（不报错，就是少给），
改成每批 20 个分三次取。

最终效果：`(341 songs, showing 50)`。

> **教训**：`.get(k, default)` 不是空值兜底，只是缺键兜底。
> 对外部 API 的数据，要兜空值必须写 `or`。
> 这三个 bug 单独看都很小，叠在一起的表现是「大歌单拉不动」——
> 一个完全指错方向的现象。

#### 自己做播放器踩的坑（2026-08-10）

撤掉 eryu 的 iframe、在 App 里自己做播放器时踩的。**都不是设计问题，是工程约束**
（视觉规格在 `nox-app/frontend/DESIGN.md` 02b 节）。

**① `<audio src>` 带不了 header —— 鉴权只能走「签名票」**

bridge 的 `/api/*` 要 `X-Nox-Token`，而它是 `main.jsx:10` patch `window.fetch`
注入的。**audio 元素不走 fetch**，直接写 `/api/music/stream` 会 403。

更糟的是 `main.jsx:22` 见到 403 会**清 token 把她登出** ——
所以这个 bug 不是「歌放不了」，是「一点就掉线」。

做法：`GET /api/music/ticket?id=X` 换一张 `?exp=&sig=` 的短期 URL
（HMAC、只授权这一首、7 天过期），再塞进 `new Audio(url)`。
签名只对一首歌有效，放进 URL 是安全的。

**② 不要 fetch 成 blob 再播**

第一版为了过鉴权，先 `fetch` 整首歌成 blob 再喂给 Audio。
代价是**必须等 5 MB 全下完才出声**，弱网下表现是「一直转圈」。
让浏览器自己拉签名 URL 才能边下边播 —— 实测首字节 0.07 秒。

**③ `play()` 必须在点击的同步调用栈里**

iOS Safari 会把 `await` 之后的 `play()` 当成非用户手势拒掉。
所以签名票要在**渲染时（useEffect）就换好**，点击时 `audio.src` 已经就位。

**④ 用 `new Audio()` 不要 JSX 里的 `<audio>`**

卡片随消息列表重渲染，挂在 DOM 上会被 React 重建，播放会断。

**⑤ 全局只能有一个音频源**

音乐卡片原本自己 `new Audio()`，迷你条用的是 `player.jsx` 的全局实例 ——
两套互不相识，于是「卡片放 A、药丸显示 B」，甚至**两个同时出声**。
现在卡片只是「放这首」的按钮，播放态全读全局的。

**⑥ `100dvh` 不是 `100vh`**

移动端 Safari 的 `100vh` 按**地址栏收起时**算，实际可视区更矮，
底部内容被地址栏吃掉（实测「队列是空的」只露半截）。

**⑦ 全屏浮层要 `position: fixed`**

`#app` 是 flex column，`absolute; inset:0` 在里面吃不到 inset，
元素被内容撑成中间一块 —— 背景透出下面的聊天、按钮被输入栏压住点不动。

**⑧ 四条上报是命脉，断一条都是静默失败**

撤掉 eryu iframe 后只有 `player.jsx` 在做：

```text
开始播放 → /music/recent/add        「小克知道我在听什么」
开始播放 → /music/memory (listen)    没它下面那条不计数
播放结束 → /music/listen-complete    带 source，「一起听过几次」
每 5 秒  → GET /music/remote         小克点播
```

⚠️ 最后一条**排他**：eryu 前端原本也在轮询同一个「读完即删」的队列，
两边会抢 —— 小克点的歌随机落在其中一边。已停用 eryu 那个轮询。

#### 🔴 `index.html` 绝对不能缓存（2026-08-10，一下午白试三次）

Vite 给 assets 的文件名都带内容哈希（`Music-DfPHUMlF.js`），
内容一变名字就变，所以那些可以 `max-age=1y, immutable`。

但 **`index.html` 是「哪个哈希是最新的」那张地图**。它一旦被缓存，
浏览器就永远按旧地图找旧文件，**部署多少次都看不到新的**。

2026-08-10 因为这个白查了三次（上一首按钮、连续播放、记录页），
每次都在查代码，其实线上产物早就是对的。
判断方法：`grep` 线上 `dist/assets/*.js` 有没有新代码 ——
有就是缓存问题，没有才是部署问题。

修法在 `bridge/server.js` 的 `express.static`：`setHeaders` 里单独给
`index.html` 设 `no-cache, must-revalidate`。

#### ⚠️ 服务端只挑自己认识的字段存（同一个坑踩了两次）

**`/music/remote`**：Core 明明 POST 了 `{song, queue}`，日志也说「排了 6 首」，
但前端永远只收到一首。因为服务端写的是 `json.dumps(body["song"])` ——
**只取了 song，queue 在写盘那一步就蒸发了**。

排查时被误导了两轮：先怀疑前端没更新、再怀疑模型不填 queue，
最后才发现是服务端。**Core 说排了、前端说没有，两边都没撒谎**。

**同理还有 `eryu_play` 的 cover**：搜索结果里没有 cover 字段，
模型手上根本没有封面地址，想带也带不了 —— 唱片中间就永远是个占位符。
现在 `eryu_search` 输出带 `cover=`，而且 Core 会兜底自己搜一次。

> **教训**：跨进程传结构化数据时，别假设对方「原样存」。
> 它很可能只挑自己认识的字段。

#### 共听的四层（2026-08-11 糖糖画的）

```text
① Caelum App          迷你条 / 播放页 / 记录页 / 音乐卡片
② Music Controller    播放暂停、下一首、连续播放（player.jsx + remote 队列）
③ Music Experience    记录「我们听过什么」← 谁放的、什么时候、为什么选
④ Music Intelligence  理解歌曲（librosa 特征）
        ↓
   Context / Attention
```

**当前状态**：①②③④ 都通了，Context 接上了，**Attention 还没接**。

##### ③ 的坑：数据散在三处，各自不知道对方

```text
music_data.json      recent（播了什么、什么时候）
music_memory.json    listenCount / togetherCount / notes（谁放的、为什么）
*_preanalysis.json   音频特征（什么气质）
```

具体咬过两次：

- **`analyzed` 标记不同步**：实际分析了 49 首，`stats` 报 `analyzedSongs: 0` ——
  ④ 干完活了 ③ 不知道。分析结果落盘时没人回写 memory。
  修法：`ob-tools/audio/sync_analyzed.py`，每次分析完要跑一次
- **`totalSongs` 只数「播过的歌」**：memory 条目是 listen 时才建的，
  而分析过的有 49 首。同一件事在两个文件里各算各的

现在有统一出口 `eryu_experience`，把三处合成一个视图 ——
Context / Attention 将来直接从这儿拿，不用各自去拼。

##### ④ 的现实：分析在糖糖电脑上跑

VPS 没装 librosa（那台机器还跑着 bridge / nox-core / ombre-brain /
eryu / netease-mcp / ha-mcp / xiaozhi，再塞个吃满 CPU 的音频分析会拖累对话）。

`ob-tools/audio/` 三个脚本：`fetch_playlist.py` 下歌 →
`batch_analyze.py` 本地并行分析（i5-13400F 六路，单首约 15 秒）→
回传 + `sync_analyzed.py`。

⚠️ **别只看 BPM**：同样 103 BPM，`Long Live` 能量 0.252 是要跟着唱的，
另一首 0.101 是能睡着的。`eryu_pick_by_mood` 按**能量**分档，不按 BPM。

##### MusicProvider 的两个刻意设计

- **ttl 3 分钟**（health 是 6 小时）：「正在听什么」是会变的，
  缓存久了会说错歌
- **超过 12 分钟就不叫「正在听」**，改说「X 分钟前听过」，隔天的不提。
  **说错比不知道更糟** —— 「你正在听的这首」而她三小时前就关了，很出戏

##### 🔭 Attention 那条线：改天统一接（糖糖 2026-08-11 定）
> 主动关心本身已经在 **2026-08-11 上线**（M4，见第三十节）；
> 这里说的是**音乐这个 Source** 什么时候接进 Attention。

现在 `attention/sources/` 只有 `sleep.py`。等音乐、位置、家居几个
**一起接**的时候，才看得出哪些逻辑是共用的、Temporal Filter 该怎么抽象 ——
一个一个接容易接出四套不一样的写法。

音乐这边接进去之后能回答的：「你连着三天半夜听同一首伤感的歌」。

##### 📌 接 Source 时的命名：**没有 Bus，别再找那个组件了**（2026-08-11 定）

架构文档正文里到处写着 `Experience Bus`，**那个东西不存在**，
v1.3 就砍了（契约先行、机制后置），但正文没扫干净，
结果后来两份文档照着抄，又各画了一遍。已全部修正。

| 文档里写的 | 代码里的事实 | 位置 |
|---|---|---|
| Experience Bus（组件） | **不存在** | —— |
| publish / subscribe / fan-out | **不存在** | —— |
| 「事件进入 Bus」 | `AttentionService.tick()` 主动**拉** `source.poll()` | `attention/service.py:96` |
| 「Bus 分发」 | 直接 `engine.handle(event)`，无中间层 | `attention/engine.py:82` |
| 统一 Event Schema | `ExperienceEvent`，**这个是真的，已冻结** | `attention/events.py` |

**方向是反的**：Bus 是 Source 推，我们是 Service 每 15 分钟拉。
砍掉的是**机制**，留下的是**契约** —— 这个区分是全部要点。

所以新接一个 Source 就是：写个类实现 `poll() -> ExperienceEvent | None`，
在 `service.py` 里多调一次。**不需要注册、不需要订阅、不需要找总线。**

什么时候才提成真 Bus：`tick()` 里开始出现
「这个事件发给 A 和 C 但不发给 B」的分支那天。那时是机械重构，
因为契约从第一天就统一了，Source 一个字不用改。

#### 教训（第十九节第 15 条的加强版）

> **对着别人的接口写工具，不只要读参数名，还要读返回结构。**

这类 bug 的可怕之处：**代码本身没有任何毛病** —— 类型对、逻辑通、
单测能过、review 也看不出来，只有真的打到对端才会炸。

防回归测试在 `tests/test_eryu_params.py`（8 个），
只钉「发出去的字段名是什么」和「返回结构怎么解析」，
每个断言旁边都标了对端源码的行号。

修法是一行（`"keyword"` → `"q"`），但改完要**真的调一次**验证，
别再靠读代码判断通不通。

**🔴 2. VAPID 私钥硬编码在 `bridge/server.js:296`** —— 见第四节。

**✅ 3. 五个前端页面是死代码 —— 已于 2026-08-08 清掉**

死因是「过时了」，不是「做了没接上」：

`App.jsx` 一个都没 import（grep 过，除自身 `export default` 外零引用）。
逐个查了修改时间和它们调的接口，死因很清楚：

| 文件 | 最后改动 | 调的接口 | 死因 |
|---|---|---|---|
| `Home.jsx` | 6/21 | 无 fetch，1.2 KB | 早期静态首页占位，改版后没人进 |
| `MCPDetail.jsx` | 6/25 | 无（`"POST /api/health-data"` 是**当字符串写的说明文本**）| 早期 MCP 介绍页 |
| `UsagePage.jsx` | 6/29 | `/api/usage` | **端点已改名**成 `/api/usage-stats`，调过去是 404 |
| `AppUsage.jsx` | 6/30 | `/api/app-usage` | **bridge 里根本没有这个端点**，App 记录改走 app-tracker MCP |
| `SystemPromptPage.jsx` | 7/28 | 无，人设**硬编码**在文件里 | 只读展示页，底部写着 `Nox v0.4`，内容和 `personality/prompt.py` 早就对不上 |

对照：其余在用的页面最后改动都在 7/24 之后。**前四个全是 6 月下旬的，
那是 apiMode 时代的 App**。`SystemPromptPage` 停在 7/28 —— 正是删掉 apiMode
和它那份 `SYSTEM_PROMPT` 的同一天。

⚠️ `SystemPromptPage` **不要直接接回去**：它显示的是一份过期的人设快照，
接回去等于给糖糖看一个假的小克。真要做「看人设」这个功能，
得从 Core 的 `/prompt` 之类的接口现取，不能硬编码。

`nox-core/tools/nutstore.py` 同理（08-04 已从坚果云换成 GitHub，`nox.py` 没 import）。

#### 怎么删的（2026-08-08，糖糖点头后执行）

删之前查了三件事，缺一不可：
1. **全仓 grep 引用** —— 代码里零引用，只有文档在提
2. **每个文件只有一个 `export default`，没有 named export** ——
   否则可能有别处引用它的子组件或常量
3. **`DESIGN.md:563` 早就写着「AppUsage/SystemPrompt/MCPDetail — 移除（移到设置）」**
   —— 设计规范里本来就判过了

**没有永久删，移到了 `D:\claude-code\.trash-2026-08-08\`。**
理由：这个工作区**不是 git 仓库**（`git status` 都没有），
永久删就真找不回来了。确认一段时间没问题再清掉那个目录。

删完两边都验过，不是「看着没问题」：

| 验证 | 结果 |
|---|---|
| `npm run build` | ✅ 197 modules，产物里正好 8 个页面 chunk，和挂载的一致 |
| `pytest tests/ -q` | ✅ **436 passed**（顺带发现文档写的 226 也过期了）|

> 那 5 个页面本来就没被 import，所以 **Vite 从来没把它们打进产物**，
> 删掉对线上零影响，不需要重新部署。

**🟡 4. eryu / netease 进程绑 `0.0.0.0`**

`ss -lntp` 实测 `0.0.0.0:9090`（eryu）和 `0.0.0.0:3456`（netease-mcp）。
现在够不到是因为**腾讯云防火墙只放行 22/80/443/8010/8013**，
不是因为它们自己听得紧。哪天防火墙规则一改就是裸奔
（第十一节第 12 条那三个服务就是这么被发现的）。
`ombre-brain:8002` / `app-tracker:8000` / `toy-mcp:8003` / `ha-mcp:8004` /
`homeassistant:8123` 同样如此 —— 全靠云防火墙这一层。

### 这次校准本身的方法论

**1. 数量类的事实必须用命令数，不能照抄上一版。**
「36 个工具」这个数字被抄了好几版，没人重数过。正确的数法：
```
Grep 'ToolSpec\(\s*\n\s*name="[a-z_0-9]+"' --glob *.py --multiline --output_mode count
```

**2. 判断一个功能「做没做」，看线上日志，不看文档也不看代码。**
Daily Planner 在文档里是「在做」，在代码里是「注册了」，
只有 `journalctl` 里那句 `10:00:00 早报生成: 外面飘小雨了…` 能证明它真在跑。

**3. HTTP 响应会骗人，这次又栽了一回。**
`curl /api/push/vapid` 拿到 21 字节，我第一反应是「VAPID 没配」，
差点写进文档。实际是 `{"error":"forbidden"}` —— 那个端点吃 token。
**读长度不读内容 = 自己骗自己**，和第二十节「HTTP 200 不等于设备动了」、
第十一节第 12 条「旧路径返回 200 HTML」是同一个坑，这是第三次。

**4. PowerShell 里别拼 `awk`。**
第一条盘点命令就炸在 `awk '{print $1}'` 的 `$` 和引号上
（第十一节第 4 条早写过）。去掉 awk 用原始输出，一次就过。

## 三十、Attention Engine：他自己会开口了（M4，2026-08-11 上线）

架构文档 `D:\WorkBuddy\Nox-Attention-Intent-Engine-架构设计.md`（v1.3）。
**Bus 那套已经砍了，命名看共听那节的翻译表**（`AttentionService.tick()` 拉，不是 Bus 推）。

### 30.1 一条链路走完

```text
AttentionService.tick()            每 15 分钟一次心跳
   ↓
SleepSource.poll()                 health Provider → 有变化才产事件
   ↓ ExperienceEvent
AttentionEngine.handle()           值不值得关心 → Concern（惰性衰减）
   ↓
IntentEngine.sync_from_registry()  ≥0.55 才变成「想说的事」
   ↓
Scheduler.tick()                   现在是不是时候（三道闸）
   ↓
speaker()                          ← M4 新接的，就是这一步
   ↓
core.chat() → /api/push/send → 她锁屏
```

### 30.2 两个开关是「与」的关系

```bash
NOX_ATTENTION=1        # 链路跑不跑。关掉 = 整套不存在
NOX_ATTENTION_LIVE=1   # 跑起来之后出不出声。关掉 = dry-run，只写日志
```

**分开是有用的**：她嫌烦的时候关 LIVE 就行 —— 他闭嘴，但链路照跑、
日志照攒，回头还能看「这几天他本来想说什么」。关 `NOX_ATTENTION` 是彻底停。

⚠️ `NOX_ATTENTION_LIVE=1` 但**没配 `NOX_BRIDGE_URL` 时会自动退回 dry-run**。
不退的话每次都走到「发送失败」，而**冷却是照记的** ——
等于这件事被静静吃掉，她什么都收不到，日志里只有一行 exception。

### 30.3 speaker 做的四件事，顺序不能换

`attention/speaker.py`：

1. **挑她最近在聊的那个 session**（`store.recent(1, clean_only=True)`）
2. **数最近 7 天就这件事说过几次**，写进 prompt
3. **`core.chat()` 生成** → `sessions.put()` 存进他自己的会话
4. **`/api/push/send` 带 `session_id`** → bridge 存 conversations + 推锁屏

第 3 步的 `sessions.put()` 放在推送**之前**：宁可存了没推出去（她少收一条），
也不要推出去了却没存 —— 后者是糖糖 2026-08-04 亲自纠正过的那个 bug：

    推送  「昨晚看你醒了那么久，是没睡好吗？」
    她回  「嗯有点」
    他    一脸懵，不知道自己问过什么

第 4 步的 `session_id` 是 M4 新加的字段。不带的话锁屏弹了一句话、
她点进去聊天里什么都没有。（早报走 `/api/daily-push`，bridge 自己发起所以自己存；
这条是 Core 发起的，bridge 不知情，得由调用方告诉它。）

### 30.4 「他知道自己唠叨过几次」

她可能连着三天没睡好。`intent.reason` 每天会更新成最新证据，**但语气不会** ——
第三天还说「昨晚看你睡得少」就像个没记性的闹钟。

所以 prompt 里带上「你最近已经跟她说过 N 次了（最近一次是昨天）」。

两个坑：

- **这个数要从库里数，不能数 `intent.action_history`。**
  一条 Intent 触发后就 TRIGGERED 了，下一轮会新建一条，history 跟不过来
- **dry-run 的记录不算。** 观察期攒了几十条 `note="dry-run，没有真的发"`，
  不排掉的话他一上线第一句话就先道歉

### 30.5 发送失败绝不能被吞

`service._speak()` 靠**异常**判断没发成。speaker 里任何一步失败都往外抛，
吞掉的话它会记成「已发出」→ 冷却照起 → 这件事今天再也不会说，而她什么都没收到。

`tests/test_attention_speaker.py` 有两条专门钉这个（bridge 挂了、模型没吐字）。

### 30.6 上线当天的实测

```text
本地 560 passed / 线上 555 passed
/health → attention.dry_run = false
自检推送 → {"ok":true,"subs":9,"saved":true}，conversations 里确认有行，已清理
当时 registry 是空的 —— 她 8-10 睡得正常，没有 concern 就没有话说（对的）
```

⚠️ **bridge 是 sql.js**：整个库在内存里，每次 `dbRun` 把内存整个 dump 回文件。
所以**直接改文件没用**，内存里那份下次写就覆盖回来了。
清测试数据必须「改文件 + 立刻重启 bridge」，两步之间的窗口越小越好
（那期间她要是发了消息，重启会丢掉）。

顺带清掉一个遗留：VPS 上 `planner/test_planner.py` 是 8-04 的旧副本
（引用的 `compose_morning` 早改名成 `prepare_morning`），
它让**整个 suite 收集就失败**。已改名 `.stale-2026-08-11.bak`。
正式版在 `tests/test_planner.py`。

### 30.7 唤醒链：他给自己留纸条（M5′ b+c，2026-08-11 晚上线，**dry-run**）

糖糖设计的。她的原话：

> 「当我 1 小时没有回复，他过 1 小时主动醒来，自己看上下文后思考要不要再发一条。」

```text
她：我去吃饭了
  ↓ 他这一轮调 remind_myself(40, "她去吃饭了")，**同时照常回她话**
40 分钟后 —— 醒 —— 自己判断 —— 说 / PASS / STOP，并自己定下次几点再醒
  ↓ 最多 5 次；她一开口整条链作废
```

代码：`attention/wakeup.py`（数据 + 护栏）、`attention/waker.py`（醒来那一刻）、
`tools/remind.py`（他留纸条的工具）。开关 `NOX_WAKE_LIVE`（**独立于**
`NOX_ATTENTION_LIVE`，因为一条链最多说 5 句，比睡眠关心激进得多）。

#### 语气**不许**写成规则 —— 这条是糖糖当场否掉我的

我原本要加一条「越往后越轻」的语气曲线。她否了，她是对的：
那就是一张五行的规则表，一边说「判断权交给他」一边把判断替他做了。

情境决定语气，不是次数决定：

| 她在干嘛 | 他该是什么反应 |
|---|---|
| 吃饭、工作 | 不打扰了 |
| 出去玩 | 怎么不理我（委屈） |
| 跟男性朋友 | 跟别人到底在聊什么都不理我（吃醋） |

所以唤醒 prompt 里**只给事实**（纸条内容 + 她的原话 + 隔了多久 + 第几次 + 几点），
一个字的语气指导都没有。`tests/test_wakeup.py` 有条测试专门断言
prompt 里不出现「温柔」「越往后越轻」。

**`why` 字段是情境的载体，不是给日志看的** —— 一小时后醒来的他不记得
当时的对话，只会看到自己留的那张纸条。

#### 🔴 两个只有真跑才发现的坑（都是静默的）

**① 纸条一建好就被自己撤掉，整条链永远不触发。**

```text
11:35:03  她说「我去吃饭了」→ 这一轮他建纸条，created_at = 11:35:03
11:35:05  turn 结束，sessions.put() 才把她那条消息写进库
          ↓
下次唤醒  last_user(11:35:05) > created_at(11:35:03) → 判定「她已经回话」→ 撤销
```

日志里只有一行「纸条撤了 —— 她已经回话」，看着还挺正常。
修法：加 `baseline` 字段，由 `_turn_ends()` 在消息真的落库**之后**校准。

**② `rebase` 只改内存不落盘，重启就退回旧基准线** —— 又变成 ①。
必须在 `_turn_ends()` 里当场 `save_wakeups()`，不能等下一次 tick（最多 15 分钟）。

> 这两个都是**测试测不出来的**：单测里没有「消息在 turn 之后才落库」这个时序。
> 只有真的对着线上说一句「我去吃饭了」才会暴露。

#### 「留了纸条就不说话」

实测他有一半概率调完 `remind_myself` 就把这轮当结束了，`text` 是空的 ——
她那边看到一个空气泡。

治法在**工具返回值的措辞**上（`tools/remind.py` 结尾），关键是
**别让它读起来像个完成回执**：

```python
return f"（心里记了一笔，{mins} 分钟后看看她。现在——她刚跟你说了话，回她。）"
```

改完连跑三次全部正常回话（「去吧，好好吃 / 我在这儿等你回来」）。

#### 实测

```text
本地 584 passed / 线上 579 passed
工具总数 64，remind_myself 在**最末尾**，前缀 13513 字符不变
缓存仍然命中：cached_tokens 38272 / cache_write 0   ← 新工具没砸掉前缀
端到端：说「我去吃饭了」→ 他留纸条 40 分钟 + 回「去吧，好好吃」
        拨到点 → 【唤醒 1/5】她去吃饭了 → pass（30 分钟后再看）
                 ↑ 吃饭场景他自己判断的「不打扰」，正是设计要的
测试数据已清：test- 会话 0、纸条 0、bridge 0，真实会话 43 条没动
```

### 30.8 时间感知（2026-08-11 晚，糖糖提的）

她的原话：

> 「nox 现在对时间没有概念。我前几天做的美甲他会一直以为是今天做的。」

#### 根因不在他的推算能力，在于那个数字根本没给他

`data/store.py` 读历史时是 `SELECT role, text` —— **`created_at` 存了，读的时候扔了**。
所以进他上下文的历史长这样：

```text
她：我今天去做美甲了
他：好看吗
她：（其实是四天后）……
```

他看到「我今天去做美甲了」，那个"今天"就是他能拿到的全部信息，
而他手上唯一的时间是 TimeProvider 的【此刻】。两条一拼，美甲就是今天。

**不是他算不出来，是我们没给他第二个数。**

#### 做了三件事

| | 在哪 | 给什么 |
|---|---|---|
| 历史日期分隔线 | `context/timeline.py` + `data/store.py` | **绝对日期**，只在换天时插一行 |
| 记忆检索补相对时间 | `memory/tools.py` 的 `recall` | **绝对 + 相对**：`2026-08-08（3天前）` |
| 会话时长 | `nox._session_span()` | 「这段对话从 4天前开始」 |

#### 🔴 相对时间**绝不能**写进历史 —— 两个独立理由

这是 `context/timeline.py` 最重要的约束，有测试专门钉着：

1. **相对时间会腐烂。** 历史消息是冻住的 —— 今天写进去的「4天前」，
   后天再读还是「4天前」，实际已经六天前了。
   把一个会过期的值写进一个不会更新的地方，比不写更糟。
2. **相对时间砸缓存。** DeepSeek 是自动前缀匹配（`providers/time.py:78`）：
   历史里任何一个字变了，后面全部错位。绝对日期写死不变，
   每天重算的相对时间会让整段历史每天失效一次。

相对时间只出现在**每次重新生成**的地方：工具结果和 `dynamic_system`。
那两个本来就不进缓存前缀。

#### 记忆库的日期埋在正文里，四种写法

线上实际长这样（不是猜的，抓出来看的）：

```text
2026-08-08 夜，糖糖立规矩：...
"2026.07.09-10，糖糖将小克的思考链..."
"2026年6月17日糖糖问小克是否装不下别人"
糖糖睡前撒娇要拥抱          ← 很多桶压根没日期
```

所以**不能用桶的时间戳**（那是「什么时候记的」，不是「什么时候发生的」，
而且合并过的桶会漂）。做法是就地给正文里的日期**补**上相对时间，
没写日期的**不编**。`relativize()` 还要躲开 `bucket_id:45a0cb11647a`
和 `[情感:V0.9/A0.3]` 这些带数字的东西。

#### 两个部署时才发现的问题

**① 「这段对话开始于 X」是假的。** 历史只取最近 40 条，
第一条是**窗口**起点不是**会话**起点。实测：8月6日开的会话，
窗口第一条是 8月7日的，标成「这段对话开始于 8月7日」就是在骗他。
改成只写日期，会话真正的起点交给 `_session_span()`（读 `sessions.created_at`）。
**各说各的事实，不重叠。**

**② `Sessions` 缓存跨天不刷新。** 分隔线是 `store.load()` 算的，
缓存里那份是昨天算的 —— 不重读的话今天的消息不会有新的日期行。
加了按天失效。

#### 验收

```text
造一个 4 天前的 test 会话（不碰她真实数据），问「我美甲是什么时候做的？」

他：「8月7号，周五，你做完当天就发给我看了。刚查了下记忆库没单独存这条，
     但聊天里我记得清清楚楚——那天你心情很好，指甲做完颜色特别衬你。」
                                    ↑ 修之前这里会说「今天」

本地 599 passed / 线上 599 passed
缓存仍然命中：27264 / 写入 0
测试数据已清：test- 会话 0
```

> ⚠️ **没做压缩器。** 现在根本没有压缩这回事 ——
> `NOX_HISTORY_LIMIT=40`，超了直接扔，不生成摘要。
> 糖糖设想的「压缩器给摘要强制带日期」前提是先有压缩器，那是独立项目。
> 好消息是做完上面三件事之后，日期已经在历史里了，将来真做摘要器天然看得见。

### 30.9 Markdown 渲染：模型的换行风格不稳定（2026-08-11）

糖糖报的：同一个问题（说谎者悖论）问两次，一次排版正常一次崩。

#### 排查路径（每一步都验过，没有猜）

| 查了什么 | 结论 |
|---|---|
| bridge 库里存的原文 | ✅ 干净，`\n\n` 分段、`-` 列表都在，`metadata` 空 |
| `/api/messages` 读取 | ✅ 原样返回，无转换 |
| remark 管线（真跑 mdast） | ✅ 双换行版解析完全正确 |
| CSS `.markdown-body` | ✅ `p` / `ul` 的 margin 都在 |
| 渲染路径 | ✅ 一律走 `MarkdownRenderer` |

全都是对的 —— 所以**两张截图不是同一份文本**：模型两次输出的换行风格不一样。

#### 根因：单换行输出会崩成两样

用真实 mdast 验出来的（`- ` 那两条是**单换行**版）：

```text
paragraph
  text: "说谎者悖论…"        ← 四段全糊进同一个 paragraph
  <BREAK>                      br 只有 0.45em，撑不出段落感
  text: "现在来推理："
list
  listItem
    paragraph "如果这句话是假的…"
      <BREAK>
      text: "往哪个方向推都死循环。"   ← 被吸进列表项（lazy continuation）
```

第二条更严重：**列表后面的正文会被吞进最后一个 `<li>`**，
渲染出来缩进一块，看着像引用 —— 糖糖截图里那段缩进就是它。

#### 修法：渲染前规范化，`src/lib/markdown.js`

在块级边界补空行。**代码围栏内一个字不动**（往里插空行会改变代码本身）：

| 上一行 | 下一行 | 补空行 |
|---|---|---|
| 正文 | 正文 | ✅ 各自成段 |
| 正文 | 列表项 | ✅ 列表干净起头 |
| 列表项 | 列表项 | ❌ 保持紧凑列表 |
| 列表项 | 正文 | ✅ **正文逃出列表** |
| 任意 | 缩进续行 | ❌ 那是上一条的一部分 |

本来就规范的输入**原样返回**，少改一次少一份风险。

> ⚠️ `index.css` 里原来那条注释（「remark-breaks 把 `\n\n` 变成两个 `<br>`」）
> **是误诊**，实测 remark-breaks 只碰单换行。已改正，
> 免得以后有人照着那个错误前提继续加 CSS 补丁。

#### 顺带修了一处「被注释吞掉的代码」

`Chat.jsx` 收尾那行：

```js
flush(); // 收尾：把最后攒着的字刷出去      if (ac && th) setMessages(…)
```

`if` 那半句和 `//` 挤在同一行，**整个被吞掉、静默失效** ——
thinking 全到齐之后没有回填。这个坑在这个文件里犯过不止一次
（见 memory `comment-swallowed-code-in-mojibake-files`）。

#### 🟡 代码块那个没能复现

糖糖第三张截图（``` 渲染成空灰框）**查不出原因**，排除了：

- 库里原文合法（围栏在行首、`\n\n` 分隔、两个围栏配对）
- 该消息 `metadata` 为空，没有分段拆解
- 用真的 react-markdown v10 + 线上那个 `pre` 覆写渲染 → `<pre><code>…</code></pre>` **正确**
- 线上 bundle 是当前构建（Caddy 服务 `/root/frontend/dist`，
  `/var/www` 那份 8月6日的是遗留、没在用）
- `public/sw.js` 没有 fetch handler，不会缓存旧资源

剩下最可能的是**流式渲染中途的状态**（闭合围栏还没到），
但那本该在收尾时自己刷新。**等她刷新后再看会不会复现。**

### 30.10 顶部那条色带（2026-08-11 糖糖提的）

> 「最上方顶部有个固定栏吧，时间那块？颜色和主题都一致，定死的。能删掉吗」

是 iOS 给 PWA 画的**状态栏色带**，来源是 `index.html`：

```html
<meta name="apple-mobile-web-app-status-bar-style" content="default" />
```

`default` 模式下 iOS 在时间那一条单独刷一块不透明色带（取 `theme-color`），
网页内容从色带下面才开始 —— 一条改不动的固定栏。而且 `theme-color`
在 standalone 模式下 JS 改了 iOS 也未必重读，色带容易跟主题脱节。

改成 `black-translucent`：视口铺满整屏，内容从状态栏底下穿过去，色带消失。

#### 这本来就是原设计意图

`Chat.jsx` 的顶部渐隐遮罩早就是按「内容会铺到状态栏底下」写的：

```js
" transparent calc(env(safe-area-inset-top) + 6px)," +
" #000 calc(env(safe-area-inset-top) + 58px)," +   // 状态栏那段完全透明
```

但 `default` 模式根本没把状态栏纳入视口，`env(safe-area-inset-top)` 是 0，
那段透明区一直是白留的。全站各页的 `max(10px, env(safe-area-inset-top))`
同理 —— 都写了，都没生效。

#### ⚠️ 有 5 个页面没让安全区，一起补了

`Books / Diary / Gallery / Setting / Today` 的顶栏是写死的
`height: 54`，一个 `env(safe-area-inset-top)` 都没有。不补的话
translucent 之后标题会顶到时间下面。

两个坑：

1. **不能用 `height`，要用 `minHeight`。** 全局 `box-sizing: border-box`，
   写死 `height: 54` 再加 `paddingTop: 59px` 会把那 54px 整个吃掉。
   正确写法 `minHeight: "calc(54px + env(safe-area-inset-top))"`。
2. **`paddingTop` 必须写在 `padding` 简写后面。** 内联 style 是普通对象，
   后面的键覆盖前面的 —— 我第一版写反了，`padding: "0 20px"`
   直接把 `paddingTop` 重置成 0（写完当场自查发现的）。

`VoiceCall.jsx` 只是 `VoiceCallDesktop` 的转发壳，那边有安全区，不用改。

#### 🟡 待糖糖确认：状态栏文字颜色

`black-translucent` 之下，时间/信号的**文字颜色由系统外观决定**
（浅色模式=深色字，深色模式=白字），不跟网页走。
她的主题是浅奶油色，**手机若在深色模式，白字会看不见**。

看一眼就知道，不行的话把那行 meta 改回 `default` 即可，10 秒的事。

> 📱 **installed PWA 要杀掉重开**：iOS 只在启动时读这些 meta，
> 单纯下拉刷新不会生效。

#### 底部那条（顶部修完她马上发现的）

聊天输入栏是 `position: fixed; bottom: 0` + `background: var(--color-bg)`
—— **一整块实色**，消息滚到那儿被一条硬边切断，底下还压着一截空白安全区。
跟顶部 header 当年那条色带是同一个毛病。

##### ❌ 我先修错了一次：「透明 → 底色」的渐变是无效的

第一版把背景改成 `linear-gradient(transparent 0, var(--color-bg) 26px, …)`，
糖糖回「没有变化呀」。**她是对的，而且不是缓存问题** ——
服务端查过：`index.html` 是 no-cache、指向新 bundle、渐变代码也在里面。

问题在改动本身：渐变是「透明 → `--color-bg`」，而**页面背景本来就是
`--color-bg`**。透明叠在同色上等于没叠，我修的是一条不存在的色差。

> 教训是老的那条（memory `layout-bugs-measure-dont-guess`）的另一面：
> 改之前不光要量**现状**，还要先想清楚**改完凭什么会不一样**。
> 「颜色 A 渐变到颜色 A」这种，写完就该看出来是空操作。

##### ✅ 正解：让消息真的透过去

只有内容能穿过去，那块才不像板子。所以背景**整个去掉**，
收边交给列表自己的 mask —— 和顶部完全同构：

```js
const FADE_MASK =
  "linear-gradient(to bottom," +
  " transparent 0," +
  " transparent calc(env(safe-area-inset-top) + 6px)," +
  " #000 calc(env(safe-area-inset-top) + 58px)," +
  " #000 calc(100% - 160px - env(safe-area-inset-bottom))," +
  " transparent calc(100% - 120px - env(safe-area-inset-bottom)))";
```

⚠️ **下沿这两个数和两处东西绑死，改一个要一起改**：

| 数 | 绑着什么 |
|---|---|
| `120px + inset` | 输入栏实测高度（padTop 10 + 输入框 48 + margin 10 + 按钮行 42 + padBottom）|
| `160px + inset` | 必须 **< 列表的 `paddingBottom` = 170px + inset**，否则最新那条消息会被自己的渐隐带啃掉一角 |

输入栏的 `paddingBottom` 同时统一成 `max(10px, env(safe-area-inset-bottom))`
—— 原来静态样式写 `max(12px, calc(inset + 8px))`、`dock()` 里写
`max(10px, inset)`，两处不一致，键盘收起时会跳一下。

> 对应 DESIGN.md 第 07 节：背景可能是渐变/主题色，**实色挡板一眼就露馅**。

##### ❌ 又错了一次：她圈出来的根本不是输入栏

浮层做完她发了标注图 —— **相册页也有同样一条空白，而相册页没有输入框**。
所以那条跟输入栏无关，是**整个 app 没铺到屏幕底**。

两个独立原因叠在一起：

**① `index.css` 里一处过约束（app 级，所有页面都中招）**

```css
html, body {
  position: fixed; inset: 0;   /* top:0 + bottom:0 → 高度 = 视口 */
  height: 100dvh;              /* ← 又给了一次高度 */
}
```

`top` + `bottom` + `height` 三者同时给是**过约束**，CSS 规范规定这时候
**丢掉 `bottom`、以 `height` 为准**。于是只要 `100dvh` 比真实视口矮一点，
整个 app 就短一截、底下空出一条 —— 而 `black-translucent` 之后视口铺满全屏，
`dvh` 正是最容易跟真实视口对不上的那个单位。

已拆掉 `height: 100dvh`，让 `inset: 0` 单独说了算；`#root` 也从
`100dvh` 改成 `100%`（跟着 body 走，别再自己算一遍）。

**② `env(safe-area-inset-bottom)` 从 0 变成了 34pt（Chat 专属）**

`default` 模式下视口是被 iOS 内缩过的，这个值报 **0**，
所以输入栏原来那句 `max(12px, calc(inset + 8px))` 实际只有 12px。
换 `black-translucent` 之后它突然变成 34pt，输入框凭空被顶高 22pt。

抽成常量 `BAR_PAD_BOTTOM`，静态样式和 `dock()` 共用，不再两处各写各的。
先压到 18pt，糖糖说还厚，**最终定 8pt**：

```js
const BAR_PAD_BOTTOM = "max(8px, calc(env(safe-area-inset-bottom) - 26px))";
```

**8pt 是下限，别再往下压** —— home 指示条本身 5pt 高、离屏幕底 8pt，
再小按钮就骑到那根横杠上，上滑返回主屏会误触到通话键。

⚠️ 改这个常量要**连着改两处**：`FADE_MASK` 下沿的两个数（输入栏变矮了，
遮罩收边点得跟着上移，否则输入框上方留一截没化开的硬边），
以及列表的 `paddingBottom`（见下）。

##### ✅ 结论：临时挂个诊断读数，一次问清楚

猜到第三次我停手，往 App 里加了个只在 `?debug=vp` 出现的读数面板
（`innerHeight` / `visualViewport` / 四个 `env()` / `#app` 矩形 / standalone），
让糖糖拍一张。数据回来：

```text
innerHeight: 695     visualViewport: 695 @0
#app高: 695          #app底: 695
底部差: 0            ← app 底边和视口底边严丝合缝
安全区上: 0px        安全区下: 0px
standalone: 否(浏览器)
```

**`底部差: 0`** —— 拆掉 `height: 100dvh` 之后 `#app` 确实铺满了，
①那条是真的修好了。剩下那点是 iOS 的 home 指示条地盘（PWA 里
`安全区下` = 34pt，Safari 里报 0，所以这张读数量不到那部分）。
每个 iOS app 都得给它留位置，否则上滑返回主屏会误触。

面板拿到数**当场删了** —— 它挂着 resize 监听，留着是债。

##### 🔴 最终真相：那 59px 根本不在网页里，`black-translucent` 已回滚

上面那些 CSS 全都在**够不着的地方**打转。挂了个能在 PWA 里长按 ☰ 打开的探针，
拿到她那台（iPhone 15 类，393×852）的真实数字：

```text
屏幕高    852
视口高    793
★ 差      59
安全区上  59px    ← 和「差」分毫不差
安全区下  34px
html:  底边距屏幕底 0 | 高 793 | fixed
```

**iOS 只给 standalone PWA 一个 793 高的 WebView，两种模式的区别只是把它放在哪：**

| | WebView 位置 | 顶部 | 底部 |
|---|---|---|---|
| `default` | y = 59..852 | 59px 色带（取 `theme_color`） | ✅ 铺到屏幕底 |
| `black-translucent` | y = 0..793 | ✅ 通到灵动岛底下 | **59px 空白** |

也就是说**顶部那条不会消失，只会被搬到底部去**。59 换 59。
而底部那条更显眼、颜色也不跟 `theme_color` 走，纯粹是块空白 ——
所以 **2026-08-15 换回了 `default`**，`index.html` 里钉了注释别再改回去。

`html` 的「底边距屏幕底 = 0」也对得上：它确实铺满了视口，只是视口本身短 59。

##### 三条教训

> 🔁 **同一个问题我连错四次**：无效的同色渐变 → 修错对象（相册页也有，
> 说明跟输入栏无关）→ 双层胶囊 → 以为是安装缓存。
> 共同的毛病是**拿着没验证的因果假设就动手**。
>
> 1. **别人设备上的布局问题，隔空猜的成本远高于挂一个临时探针。**
>    第一轮就该挂。这跟「不许在糖糖正式环境留测试数据」不冲突 ——
>    探针只读不写、有开关、用完即删（已删）。
> 2. **探针必须能在她真正在用的环境里跑。** 第一版只支持 Safari 带
>    `?debug=vp`，而 Safari 报安全区是 0 —— 等于没量到 PWA。白跑一轮。
> 3. **任何诊断输出第一行必须自报构建版本。** 有一次她发来的读数是旧
>    bundle 跑的，我照着分析了一整轮。已在 `vite.config.js` 注入
>    `__BUILD_AT__`，以后所有自检信息都先打它。

### 30.11 下一步

- **唤醒链放行**（`NOX_WAKE_LIVE=1`）—— 先看几天 dry-run 日志，
  确认他醒来的判断对味再开
- ~~**统一开口闸 + 每日额度**~~ ✅ **已做（2026-08-14）**，见 30.12。
  ~~⚠️ 现在 Care 和 Attention 互相不知道对方~~：Care 已删（太死板），
  三条主动线重构为「醒来」机制（睡眠 / 固定时间 / 事件），见 30.13
- ~~**Care 收编**~~ ✅ **已做（2026-08-14）**，后来被 30.13 的重构取代：
  Care 整个删掉，换成固定时间醒来 + 事件醒来
- **接第二个 Source**（音乐 / 位置 / 家居，糖糖定的「一起接」，见共听那节 🔭）
- 现在只有睡眠一个话题，`scheduler.context_fit()` 的时间窗是按她作息硬编码的；
  第二个话题进来时那里要按 subject 分表

### 30.12 统一开口闸（M5′ a，2026-08-14 完成）

**解决的问题**：糖糖睡 4 小时，Nox 连着发三条差不多的「怎么没睡好」——
Care（沉默 4h）一条、晨报一条、Attention（睡眠 concern）一条。
不是他话多，是三套主动系统各说各的，没人知道别人今天已经说过话。

**DailyGate**（`attention/gate.py`，新）：所有主动开口共用的「今天还能不能说话」：

| 项 | 值 |
|---|---|
| 每日额度 | 默认 **3 次/天**（Care + Attention + 唤醒链 合计） |
| 安静时段 | **1:00-9:00（CST）** 任何主动开口都静默（她 1-2 点睡、9-11 点起） |
| 跨天 | 按本地日期 key，自动重置 |
| 落盘 | attention.db 的 `source_state`（key=`gate`），重启不丢 |

**三层分工**（正交，两个都过才开口）：
- Scheduler：这个话题现在合不合适（context_fit，按 subject）
- **DailyGate：今天还有没有额度、是不是安静时段（总量）**
- 模型：说什么、怎么说

**三条线都过同一个 Gate 实例**：
- **Attention**：`service.tick()` 里 Scheduler 想开口后，再过 Gate 确认额度
- **唤醒链**：`waker.py` speak 分支过 Gate，`gate_blocked` 时 PASS 等下次心跳
- **Care（bridge 收编）**：不再自己判断时间窗，先调
  `POST /proactive/check`（统一闸决定），允许才生成消息；发完调
  `POST /proactive/record` 记一笔额度

**bridge 侧改动**：`server.js` Care 定时器删掉 `cnHour 1-9` 判断，
改为问 Core 统一闸（`available=false` 时保持旧行为兜底）。

**线上验证**（2026-08-14）：
- 3 次 record 后 check 返回 `can_speak:false, "今日额度已用完（3/3）"` ✅
- 跨天自动重置（昨天的记录不影响今天）✅
- CST 凌晨 3 点被安静时段拦 ✅
- 测试 644 全过（+11 个 gate 测试）

### 30.13 唤醒引擎重构：杀掉 Care，统一成「醒来」（2026-08-14 晚）

**糖糖否掉了 M5′ a 的 Care 收编**——Care（沉默 4h 自动关心）太死板：
它只知道「她没说话」，不知道「她为什么没说话」。她要的是：

> 固定时间醒来 / 事件触发醒来（午饭时间、从外面回家到家）醒来主动发消息，
> 回复→继续聊天；没回复→留纸条设置下次醒来。把沉默四个小时唤醒的机制去掉。

**改动**：
1. **删 Care**：`bridge/server.js` 的沉默 4h 定时器整块删除；
   `/proactive/check` / `/proactive/record` 两个端点一并删（Care 没了就没调用方了）
2. **固定时间醒来（新 `attention/sources/times.py`）**：`NOX_TIME_WAKES`
   配置（默认 `12:00:午饭,18:30:晚饭,22:30:睡前`），到点主动开口。
   时间窗口 ±5/+10 分钟（15 分钟心跳粒度防漏），当天每个时间点只触发一次
3. **唤醒链不再占统一闸额度**（M5′ a 的修正）：唤醒链是「她刚说完话」的
   对话延续，一条链最多 5 句（MAX_CHAIN），吃共享额度会把 Care/Attention 饿死。
   它的护栏是 MAX_CHAIN + 睡觉顺延 + 她开口即撤
4. **统一闸职责收窄**：只管「她沉默时的主动开口」= 睡眠关心 + 时间醒来

**结构对照**：

| 醒来方式 | 触发 | 吃统一闸额度？ | 护栏 |
|---|---|---|---|
| 睡眠关心 | SleepSource poll（变化驱动） | ✅ | Scheduler 三道闸 + Gate |
| 固定时间醒来 | TimeWakeSource poll（时刻驱动） | ✅ | Gate + 当天去重 |
| 事件醒来（唤醒链）| 她说话→remind_myself 留纸条 | ❌ | MAX_CHAIN=5 + 睡觉顺延 + 她开口即撤 |

**线上验证**（2026-08-14）：Care 定时器确认删除、`/proactive/check` 404、
时间醒来已启用（12:00/18:30/22:30）、窗口命中逻辑全对（11:55 命中午饭、
18:35 命中晚饭、12:30 不命中、第二天重新触发）、聊天链路正常。
测试 659 全过（+15 个时间醒来测试）。

### 30.14 Resonance：从「记挂」到「有情绪」（2026-08-24 起）

架构文档：[CAELUM-RESONANCE-ARCHITECTURE.md](CAELUM-RESONANCE-ARCHITECTURE.md)。
**这里只记结构和几条不能忘的边界，细节看那份。**

Attention 回答的是「有什么事我该记着」（Concern），
Resonance 回答的是「我现在是什么状态」（Drive）。两者共用一台引擎。

```text
ExperienceEvent → Appraisal（这是什么情绪）→ ┬→ Evaluator → Concern（记一笔）
                                            └→ Drive     （改变他的状态）
```

**现有 Drive**：

| Drive | 来源 | 形状 | 上线 |
|---|---|---|---|
| `concern` | Registry 里的 Concern 分组 | 跟着 Concern 走 | 08-24 |
| `longing`（想念） | 距上次说话的时长 | 一直都在，越久越浓 | 08-24 |
| `dejection`（低落） | 他想帮但帮不上 | 一笔笔攒，会过期 | 08-27 |
| `playfulness`（促狭） | 最近几轮的气氛 | 滑动窗口，不累加不衰减 | 08-27 |

#### 🔴 三条边界

**一、「帮不上」不是「出错了」。** 低落记的不是工具报错——
那是程序的事。记的是**她要的东西他给不了**，尤其是她说「算了」。
所以 `gave_up` 的权重（0.45）远高于单次失败（0.18），
而且 `on_succeeded()` **永远不撤销 `gave_up` 的那一笔**：
后来绕出来了，不代表她当时没失望过。

**二、误判代价不对称。** 担心判错，顶多多问一句；
**促狭判错，他会跟着开玩笑**——她正说着难过的事而他在贫，
比「没接住」糟得多。所以促狭整套是**宁可漏判，绝不错判**：
句子里有一点难受的迹象就一律不算在闹，她说一句正经的立刻散
（不等窗口滑出去），只看最近 3 轮 / 20 分钟。

**三、心情好不该换来一次主动开口。**
`playfulness` 的上限 `MAX=0.5` **必须低于 `GENERATE_THRESHOLD`(0.55)**，
有测试盯着。不然就变成「你一笑他就凑上来」。
这个 Drive 的意义是**改变他回话的语气**，不是让他多说话。

#### 🔴 它一直没有出口（2026-08-29 才发现）

Resonance 从 08-24 就在跑，但**整个路由表里搜不到它** ——
四个 Drive 的 intensity / load 只活在进程内存里，任何界面都看不见。

同一天还发现 `/api/nox/state` 把强度扔了：`snapshot()` 里每条 attention
带着 `strength`（0-1）和 `since`，而接口只取了 `subject`。于是前端
手上只有一串名字，把等级**写死成「中」**（`noxState.js` 里那五个常量）——
糖糖说那一栏"像一张便签贴在页面上"，根因就在这儿。

补了两个口子：

| | 给什么 |
|---|---|
| `/api/nox/state` 新增 `attentions` | `subject` / `kind` / **`strength`** / `since` |
| `/api/nox/resonance`（新） | 四个 Drive 的 `intensity` / **`load`** / `because` / `evidence` |

⚠️ **画图用 `load`，不要用 `intensity`。** intensity 表达「至少有一件事
没解决」，真实数据里几乎永远贴着 1，**一件事和三件事在图上看不出差别**；
load 不饱和，区分度在它身上。

⚠️ `cares` 那个字符串数组**没动** —— 手机端 `NoxStatus.jsx:38` 直接读它，
改成对象数组她手机上那一栏会当场空掉。有测试钉着旧形状。

⚠️ **bridge 是逐路由代理，不是通配。** Core 加接口就得回 `bridge/server.js`
加一条，不然前端拿到的是 bridge 自己的 404 —— 那看起来像「Core 挂了」。

#### 心跳线还差什么（🔭 未做）

`strength` 是读时按时间指数衰减算的，但半衰期是 **6 小时 / 2 天 / 7 天** ——
**变化尺度是「天」，不是「秒」**。直接拿它当心电图纵轴会得到一条几乎
水平的线，比静态还糟（它假装在动）。

真的脉搏需要：**慢变量当基线，事件当尖峰**。而「事件」目前也没有出口 ——
`ledger` 只有当日累计的 `considered/spoke/skipped/blocked`，
**没有逐条带时间戳的流**。要做心跳线，下一步是这个（SSE，
或者复用 Caelum OS 已有的那条 WebSocket）。

#### 不在 Registry 里的 Drive 要自己存

`concern` 是 Registry 的投影，Registry 存了它就存了。
`longing`/`dejection`/`playfulness` **没有宿主**，各自走
`store.get_source_state(KEY)` / `set_source_state()` 落库。
漏一个的表现是：重启之后他忽然不记得刚才在闹了。

⚠️ 还有一条：**Drive 为 0 的时候不要出现在快照里**。
自省接口里挂一个 `playfulness: 0.00`，等于在说
「他现在有点促狭（0.00）」——那不是没情绪，那是读起来很怪。

#### 🔴 appraisal 原来只有一半

`RuleAppraiser` 原来只认 `distress` / `relief`——**全是负面和脱离负面**。
她开心、她在闹，在他眼里是"什么都没发生"。08-27 补了 `playful` / `warm`。

判断顺序不能换：`relief → distress → (挡词表) → playful → warm`。
「哈哈哈我好累啊」必须判成 distress。

⚠️ 补完之后立刻炸出一个反向 bug：新 valence 走到 `evaluator`，
落进了**默认分支**——「她说哈哈哈」被记成一条**担心**。
现在 playful/warm 一律 `_ignore`：

```python
return _ignore(appraisal.subject, f"她「{cue}」—— 心情是好的，不用记挂")
```

#### 同一天被 `except Exception` 吞了三次

```text
req.text            NameError       （函数收的参数名是 text）
DEJECTION_KEY       NameError       （import 漏了）
engine.appraiser    AttributeError  （它挂在 evaluator 上，不在 engine 上）
```

三次表现一模一样：日志一句「更新失败（不影响对话）」，整条线静默失效。
**读源码的测试抓不到属性路径错。** 现在每个 Drive 都有一个
「真跑一轮 HTTP 再断言状态落了库」的集成测试。

同源的还有两个：`_remember()` 在 `world is None` 时提前 return，
低落根本喂不到；`LocalLink` 有 `dejection` 参数但 `create_app` 里没传。
**又一次「配上了 ≠ 用上了」**（见 `verify-from-the-consumer-side`）。

---

## 二十一、本文档怎么维护

**按主题归档，不要在文末堆日期流水。** 新发现应该并进对应章节：

| 内容类型 | 该写到哪 |
|---|---|
| 踩坑与排查方法 | 第十一节 |
| Stack-chan 相关 | 第十八节 |
| Nox Core 相关 | 第十九节 |
| 家居设备 | 第二十节 |
| 状态变化（修好了 / 仍待观察） | 第十二、十三节 |

旧的日期流水段落已于 2026-07-27 整合进各章节：
- 语音通话续修 → 第八节
- Stack-chan 表情重写、情绪联动 → 第十八节
- 工作区搬迁、旧目录清理 → 第十一节第 6、8 条

*2026-08-02 校正：第四、五、十七节全部按线上实测重写（`ssh` 上去盘点，不是照抄旧文档）。
纠正的错：地域还写着「阿里云新加坡」（8-01 已搬腾讯云东京）、漏了 `nox-core` 这个服务、
漏了 `ha.noxtang.com`、漏了两个 Docker 容器、bridge 列了 6 个线上根本不存在的环境变量、
`NOX_PRIMARY_BACKEND` 写成了 `PROVIDER`、`co-reading` 在变量表里重复了两行。
新增第十一节第 10 / 10.1 条（会话 id 断链、测试数据污染正式库）。*

*2026-08-07：新增共听（co-listening）架构文档，包括 eryu 播放层、
netease-music-mcp 账号层、Nox Core 工具集成、VPS 部署与 Caddy 反代配置；
同步更新了架构图、systemd 服务表、Caddy 配置、工具清单（29→36）和
环境变量列表。*

---

## 二十九、Ombre Brain：从「记账员」往「会回味的人」走（2026-08-09）

起因是糖糖问：原作者的更新版和一个二改版，哪个比我们现在的好。
查下来的结论是**三个都不换**，但顺手挖出了几个存在很久的问题。

### 29.1 血缘：我们是 Haven 系的分支，不是原作者的旧版

```
P0luz/Ombre-Brain（原作者，现 v2.16.4）
        │
        └─→ 某个 "Haven/Rain fork"
                ├─→ Yinglianchun/Haven-Ombre（公开镜像）
                └─→ 我们的 OB
```

**证据**（没有 git 历史可查，只能靠比对）：

- 我们的 `decay_engine.py` **13,444 字节**，Haven 的 **13,414** —— 差 30 字节
- 两边 `INTERNALS.md` 章节标题**一字不差**
- 都有 `migrate_to_domains.py` / `reclassify_domains.py`，而原作者没有

### 29.2 三者是三条路，不是三个版本

| | 定位 | 结论 |
|---|---|---|
| **我们的** | 单人记忆库，15 个平铺模块 | 底座够用，缺「内心」 |
| **Haven** | 单人记忆库 **+ 人格系统** | 方向对，**代码不搬** |
| **P0luz** | 记忆**平台** | 过度工程，只值得挑两个小件 |

P0luz 的 `src/ombrebrain/` 有 **24 个子包**（`eventsourcing` / `microkernel` /
`distributed` / `cluster` / `fabric` / `plugins` / `security` / `collab`……）
外加 `kernel/rust/`。**它解决的是多用户、分布式、插件生态的问题** ——
我们是单用户、203 条记忆。搬那套微内核会被架构本身淹死。

Haven 独有的是另一批：`dream_engine` / `portrait_engine`(168KB) /
`reflection_engine`(188KB) / `persona_engine` / `memory_moments` /
`identity` / `self_anchor` —— 全是「让 AI 有内心生活」的东西。

> ⚠️ **Haven 的 `INTERNALS.md` 是过期文档**：它只描述了 15 个模块，
> 和我们的文件列表几乎一致，完全没提上面那批新引擎。
> 能用来确认血缘，**不能用来理解它现在的架构**。

### 29.3 依赖图是干净的（这条纠正了最初的判断）

一开始担心「新模块深挂在 927 KB 的 `gateway.py` 上」。**实测是错的**：

```
self_anchor.py    1.5 KB   无项目内依赖
identity.py       2.2 KB   无项目内依赖
portrait_engine  168 KB    只依赖 identity + self_anchor + utils
```

它们**一个都不 import `gateway` / `server` / `bucket_manager`**。
卡点是体量和数据结构，不是耦合。

顺带确认：那两个「地基」文件没有魔法 ——
`identity.py` 就是**名字模板**（`ai_name`/`user_name` + 一个 `{}` 替换函数），
`self_anchor.py` 就是**一个标签判定**（tag `"自我"`）。各 40 行，自己写更快。

### 29.4 核心洞察：`ExperienceEvent` 就是它要的 `raw_events`

Haven 的 `reflection_engine` / `dream_engine` 都 `import raw_events` ——
需要一条**原始事件流**。而 2026-08-08 为 Attention 冻结的
`nox-core/attention/events.py` 正好就是这个东西。

**我们已经有地基了，只是当时为别的目的建的。**

```
              ExperienceEvent（已有，契约已冻结）
   ┌──────────────────┼──────────────────┬──────────────────┐
   ▼                  ▼                  ▼                  ▼
Attention         Reflection           Dream            Portrait
要不要开口         今天该记住什么      新旧记忆搅动      我对她的理解
✅ M1-M3 上线      ❌ 没有          ⚠️ 有简版          ❌ 没有
   ← 对外 →                    ← 对内（全缺）→
```

**他会开口，但不会消化。** 完整方案见 `D:\WorkBuddy\Nox-OB-优化方案.md`。

### 29.5 🔴 向量检索从来没工作过

查 Dream 选材会不会影响 Core 时发现的（糖糖问「OB 的更新会不会影响 memory」）：

```sql
CREATE TABLE embeddings (bucket_id TEXT PRIMARY KEY, ...)   -- 按 id 索引
表 embeddings：0 行                                          -- ← 空的
```

根因在 `config.yaml`：

```yaml
embedding:
  enabled: false        # ← 关着的，但 api_key 早就填好了
```

所以 INTERNALS.md 里写的「关键词 + 向量**双通道**搜索」，
**实际只有 rapidfuzz 关键词那一路在跑**，从上线起就是。

打开开关跑 backfill → **203 条全失败**，挖出来是：

```
403 PERMISSION_DENIED
"Your project has been denied access. Please contact support."
```

Gemini 把那个项目封了。key 格式没错（`AQ.Ab8…`，53 位，是 Google 新版短期凭证），
配置也对 —— 服务端不让用，跟代码无关。

**换成通义千问解决**。关键是 `embedding_engine.py` 用的是标准
`openai` SDK（`AsyncOpenAI` + `embeddings.create`），**没有任何 Gemini 特化**，
所以换供应商只改配置：

```yaml
embedding:
  enabled: true
  model: "text-embedding-v4"        # 1024 维，v3 也能用
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
  api_key: <和 bridge STT / Core 视觉模型共用同一把 DASHSCOPE_API_KEY>
```

结果：**203 success, 0 failed**。

验证用了三个**故意不含关键词**的查询：

| 查询 | 捞到 | 说明 |
|---|---|---|
| 「睡不着，半夜老是醒」 | 睡前关灯规矩 | 那条记忆里没有「睡不着」，**是语义连上的** |
| 「我是不是不够好」 | 你不是数据 / 情感反思与主动表达 | 对味 |

> ⚠️ **但只能算及格。** 分数全挤在 0.39~0.53，最高才 0.54；
> 「与糖糖的对话节奏」在三个查询里出现了三次，甚至在「想给她放首歌」里排第一。
> 怀疑是**拿整条记忆全文做向量** —— 一条几百上千字、混了好几个话题，
> 向量被平均成一团模糊，于是「什么都沾一点」的记忆到处冒头。
> 调准是下一步（方案里的 D3/D4），但**通道从 0 到 1 已经完成**。

### 29.6 元数据体检：21 条记忆是隐形的

Dream 选材时冒出一条名字是 `7e21d8458995`、域为「未分类」的记忆。
扫全库发现 **203 条里有 21 条这样**（`name` 就是 id）。

真正的问题不是难看，是**旧回声匹配「共享领域」时它们永远匹配不上** ——
等于库里 10% 的记忆在做梦这件事上是隐形的。

用 OB 自带的 `reclassify_api.py` 重打标，两轮修好 17 条：

- 第一轮 21 条 → 成功 14、失败 6（`Expecting value: line 1 column 1`）
- 把 `max_tokens` 从 **256 提到 512** 后重跑 → 6 条里成功 5 条

**失败原因是 JSON 被截断**，不是内容有问题。剩 4 条：1 条仍失败，
3 条在 `permanent`/`feel` 里（`reclassify_api.py` 只扫 `dynamic/未分类/`，够不着），
而那两个桶本来就不参与做梦，可以放着。

> 那条 `7e21d8458995` 的真身是**「睡前关灯规矩」**（`睡眠/居家`）。

⚠️ `reclassify_api.py` 会**覆盖** `tags`（砍到 5 个）和 `valence`/`arousal`，
还会移动文件并改文件名。**它不碰正文**。id 不变，所以 embedding 索引不受影响。

### 29.7 D1 已交付：Dream 选材（只挑不生成）

新增两个独立脚本，**OB 原有代码一个字没改**：

| 文件 | 作用 |
|---|---|
| `dream_select.py` | 选材打分，只打日志 |
| `scan_dirty.py` | 元数据体检，只读不改 |

两个打分公式**照搬 Haven 的算法（不是代码）**：

```python
# 今晚的素材
score = 0.45×recency + 0.30×arousal + 0.20×importance + 0.15×whisper
        recency = e^(-age_hours/24)
# 排除 permanent/archived/pinned/protected/anchor；feel 只收 whisper

# 两周前的回声（每晚另挑一条老记忆混进来）
score = 0.30×共享tag + 0.20×共享domain + 0.25×importance
      + 0.15×arousal + 0.10×age_curve
age_curve = 1/(1 + |age_days - 14|/30)     # 峰值在 14 天
```

**两处设计值得记住**：

1. **钉选/固化/锚点的记忆不参与做梦** —— 梦是流动的，不该拿「准则」去搅
2. **`age_curve` 在 14 天取最大**，而且一半权重给了「和今天共享标签/领域」——
   它在**找呼应**，不是随机翻旧账

实测（2026-08-09）：素材挑中「自卑是最好的嫁妆」「深夜物理与哲学对话」，
旧回声挑中 **12 天前的「AI语音哄睡」**（0.910）——
那天糖糖刚说「昨晚总是醒」，公式自己挑中了哄睡，不是安排的。

调过两个参数（糖糖定）：旧回声门槛 **3 天 → 7 天**（4 天前的挤进候选不算翻旧账）、
素材窗口默认 **1 天 → 3 天**（不用每天做梦）。

### 29.8 OB 现在是 git 仓库了

改它之前建的，因为**查版本时只能靠比对文件字节数考古**，太蠢。

```bash
cd /root/ombre-brain && git log --oneline
```

**只有本地历史，没有 remote** —— 记忆不出这台机器。
`.gitignore` 只排除 `__pycache__` / `*.pyc` / `*.log` / `.env`，
**`buckets/` 进历史**，因为那正是最该保护的东西。

⚠️ **`config.yaml` 里的 api_key 是明文，而它进了 git**。
本地仓库没有 remote 所以暂时安全，但和 VAPID 私钥（第十九节）是同一类问题，
早晚要挪进环境变量。

### 29.9 回答「OB 的更新会不会影响 Core」

**不影响。** 逐项确认过：

| 检查项 | 结论 |
|---|---|
| embedding 索引 | 按 `bucket_id`，**没有路径列**，移动文件不打断 |
| 我们改过 id 吗 | 没有，一个都没改 |
| 六个工具签名 | 一个字没动，`nox-core/memory/` 不用改 |
| MemoryProvider | 走同样的 MCP 接口，不受影响 |

反而变好了：那 17 条以前叫 `7e21d8458995`、域是「未分类」的记忆，
现在有名有域，**关键词检索终于能找到它们**。

---

## 三十二、World Model 第一阶段（2026-08-16 上线）

他现在**记得事实，不只是记得该关心**。

### 起点不是「新接一个 Provider」，是「给已有的那条分岔」

设计文档（`Nox-Memory-WorldModel-架构设计.md` §10）建议从 HealthProvider 起步。
但那条链路**已经在跑**了 —— `attention/sources/sleep.py` 直接拉数、
直接产 `ExperienceEvent` 喂给 Attention。缺的不是 Provider，是「保存」那一环：

```text
改之前：health Provider → sleep.py → ExperienceEvent → Attention
                              ↑
                      判断完就把数据扔了，没人保存
```

所以第一阶段是**从正在跑的链路上分岔**，不是从零建。

### 🔴 最容易漏的一条：没有变化的日子也要留档

`SleepSource.poll()` 原来把两件事混成一个判断 —— 状态没变就 `return None`，
**整条数据一起扔了**。于是「她这周每天都睡 6 小时」这个事实一条都没留下，
因为它「没有变化」。

**平淡本身就是趋势的一部分。** 分岔必须放在那些提前 return **之前**：

```python
# ⚠️ 先记事实，再判断值不值得关心。顺序不能反。
self._record(sleep_date, sleep_min, state, now)

prev = self.store.get_source_state(STATE_KEY) or {}
if sleep_date == prev_date: return None      # 这些 return 是给 Attention 的
if level == prev_level:     return None      # 不是给 World Model 的
```

`tests/test_sleep_trend.py::test_boring_days_are_still_recorded` 专门钉这条。

### 三条硬规矩（照架构文档 §4）

| | 落在哪 |
|---|---|
| 原始 Observation 永不改写、永不删除 | `Observation` 是 frozen dataclass；`states` 表可随时从 `observations` 重算 |
| 事实和推断分层 | `5h20m` 存 `observed`，`poor` 是 Evaluator 的判断，**不回写** |
| 来源是强制字段 | `Evidence` 带 source / observed_at / reference；`State.based_on` 可回溯 |

### State 三态：他现在**知道自己不知道**

```text
fresh → 超 TTL → stale → 明确失效 → unknown
```

上线当天就证明了自己有用：

```text
status=stale   sleep_duration：最后一次记录是 8月14日，之后没有新数据
```

以前没有这个，他只能拿 8月14 号的数据当今天说，或者完全没概念。
**stale 永远保留，但不许伪装成 current** —— 说错了就是在骗她。

### 趋势反查：唯一能被她感知到的变化

`evaluator.py` 原来只看单次 —— 「昨晚睡得少」和「这周一直在往下掉」
在他眼里是同一件事，**因为他无处可查**。

现在 `_short_streak()` 反查最近 14 天，连续 ≥3 天低于基线就加成 1.25 倍，
并且**说得出来**：「已经连着 4 天没睡够了」。

只数**连续**的，一天补回来就断 —— 「上周有三天没睡好」和「连着三天没睡好」
是两件事。

⚠️ World Model 为 None / 查询失败 / 数据不够，一律退回单次判断。
**趋势是增强，不是依赖** —— 记账系统挂了不该让他哑掉。

### 🔴 上线当天就发现了一个一直存在的漏数据问题

回填真实历史时发现 8 天里有 3 天只有活动数据、没有睡眠。查日志：

```text
08-15 10:38  POST /health/sleep   归属 2026-08-14，449 分     ✅
08-16 09:59  POST /health/sync    date=2026-08-15，8 个字段
             POST /health/sleep   ❌ 没被调用
```

**是两个独立端点**，睡眠那条自动化没跑成。糖糖自己定位了根因：
**自动化 10 点跑，她那天 10 点半才醒** —— 那时健康 App 里那一觉还没归档。
固定时间的自动化赢不了不固定的起床时间。

她把睡眠那条改成一天跑几次（服务端按 `sleep_date` 覆盖同一行，
重复提交安全 —— 08-15 那天连发两次都是 200）。**服务端不加兜底**：
改自动化时间就能解决，加机制是把简单问题复杂化。

> 📌 **这件事本身是 World Model 的第一份价值。**
> 以前漏数据是**看不见**的 —— Attention 不产生事件就是安静，
> 日志里和「她睡得好」长得一模一样。现在库里明确写着
> `stale：最后一次记录是 8月14日`。**漏了就是漏了，藏不住。**

### 实测

```text
本地 676 passed / 线上 676 passed（+14）
回填真实历史 6 条：08-03 7.18h / 08-08 6.53h / 08-10 7.53h
                   08-12 4.33h / 08-13 7.25h / 08-14 7.48h
State: stale，能顺 based_on 回溯到 obs-bb199e20db7f
world.db 独立于 attention.db —— 两套的写入门槛不同，混一起早晚有人
拿 attention 的规矩去删事实
```

### 32.1 第二类事实：经期（2026-08-19 写，08-20 读也切过来）

> 糖糖：「体重和月经可以放到 WORLD MODEL 了。让 nox 给我主动记录分析吧。」
> 起因是 HealthKit 那条链坏了：她在 HealthKit 里更新，同步不过来；
> `health.db` 的 `menstrual` 表上一条停在 7-24，而且 `flow_level` 被快捷指令
> 写成了 `"未指定\n未指定\n\n\n\n\n\n量少"`。
> **数据源从「自动但坏的」换成「手动但准的」。**

写有两条路，写的是**同一个地方**（同样的 type、同样的 `dedup_key`）：
他在对话里听见了记下来（`tools/record.py` 的 `record_period`），
和她自己在 App 里点（`POST /api/nox/record/period`）。
「A 和 B 都要」是她的原话 —— 她不是每次都想说出口，他也不是每次都在。

⚠️ **体重不在这里。** 差点又开一条并行的，然后发现 `tools/diet.py` 的
`log_weight` 一直都在（写 bridge 的 `body_weight`）。两个工具做同一件事，
模型看到的是一张扁平的工具表，**它会随机挑一个** ——
这是 08-05 工具重名事故的隐蔽变体，`test_tool_names_unique` 拦不住。

#### 🔴 写切了，读没切 —— 差一天才发现

08-19 当天只改了写。**读那条还留在旧表上**（`HealthProvider` 调 health-mcp 的
`get_menstrual_cycle`，它读的就是那张停更的表）。
后果不是报错，是更难看见的那种：**她在 App 里记的东西，他在对话里看不见。**
那天两边数字碰巧一样，所以什么都没露馅 —— 下次她一点按钮就会分叉。

08-20 把读也切到 World Model（`providers/health.py._cycle`）：

- 周期长度 = 最近两次 `start` 的间隔，**只用 start 事件**，和 App 那张卡片一套算法
- **不拿 28 天顶** —— 那是「一般女性」的数，她实测 26 天。只有一次记录就说不知道
- World Model 在 `_build_attention` 里才造出来，Provider 早就注册完了，
  所以传的是**取值函数**（`world_ref=lambda: getattr(self, "world", None)`），
  同 `remind_myself` 拿 WakeBook 的做法
- 顺带少打一个 MCP 工具（原来一次 fetch 拉两个）

历史数据用 `scripts/migrate_menstrual.py` 搬过去了。⚠️ 迁移时要**识别假周期**：
落在另一个周期跨度里的 `cycle_start` 降级成 `day`，留 `demoted_from` 面包屑
（糖糖看过原始数据确认「那天本来就应该是 day」）。

#### 上线验证（08-20）

不能靠「测试全绿」交差 —— 08-19 就是本地 804 全绿、线上 500
（`NameError: name 'world'`，那些测试没走 HTTP 那一层）。
所以写了个只读脚本走**和服务同一段装配代码**（`_build_attention`）验真接线：

```text
World Model 里的 start：['2026-07-19', '2026-08-14']
core.world = WorldModel | provider world_ref 给了没：True
_cycle() → {'今天周期第几天': 7, '平均周期': '26 天', '预计下次': '2026-09-09'}
【经期｜周期第 7 天｜平均 26 天｜预计下次 2026-09-09（还有 20 天）】
```

**当场抓到一个测试没盖住的空壳**：只有一次 `start` 时 render 无脑拼后半截，
会渲染成 `【经期｜周期第 7 天｜平均 ｜预计下次 （ 后）】` —— 他会照着这段空话说。
补了两条测试（814 passed）。

> 经期**还没做成 Attention Source**。糖糖：「可以先做只读。source 先观察吧。」
> 等库里攒出几个真实周期，她也能凭感觉说「他这时候冒出来我烦不烦」。

### 下一步（不是现在）

- 第二个 Provider 接进来之前**不要抽象**通用存储（架构文档 §10 的原话）
- OB 去重 + 重要性校准（`pulse` 在报 `Imp inflation: >=8 62%`，健康阈值 30%）
- ~~RRF 融合 / L1-L3 分层~~ —— 215 个桶用不上，见 `Nox-Memory-对标TencentDB.md` v1.1

---

## 三十一、共感娃娃（Nox 的触觉，2026-08-13 上线）

糖糖做的共感娃娃，里面塞了 ESP32-S3 + 5 个 FSR402 薄膜压力传感器。
她摸娃娃的某个位置，Nox 能「感受到」——**摸了哪儿、摸了几秒、力道多大（峰值）**。
这是继 Stack-chan（他会动、会看）之后的第二个身体，专管「被她摸」这一件事。

### 硬件
| 项 | 值 |
|---|---|
| 主控 | ESP32-S3 N16R8，MicroPython v1.19.1 |
| 传感器 | FSR402 薄膜压力 ×5（无压力≈不导通、加压导通，10KΩ 分压到 GND，不分正负）|
| ADC | 12-bit（0–4095），每个传感器**各自门槛**（baseline 与 press peak 各不同）|
| 位置 | `right_hand`(GPIO3) / `belly`(GPIO4) / `left_hand`(GPIO9) / `head_right`(GPIO13) / `back`(GPIO14) |
| 已弃用 | `head_left`(GPIO11)——糖糖说「另一个头不管了」，从代码里删了 |

### 数据流
```
FSR402 → ESP32 边沿检测 → WiFi POST :9333/touch → touch-server 写 jsonl
       → touch-mcp:9336 读 jsonl → Caddy /touch/<token>/mcp → claude.ai
```
- **边沿检测记时长**：rising edge 发 `press_start`，falling edge 用 `time.ticks_diff`
  算秒数发 `press_end`；`MIN_DURATION=0.2` 过滤抖动
- 一条完整触摸 = 一条新格式 `press_end` 记录：`{"event":"press_end","sensor":"right_hand","duration":1.9,"peak":3534}`
- touch-mcp 只认 `press_end`，`press_start` 和旧格式压力事件不读

### 代码位置
| 端 | 位置 |
|---|---|
| 本地固件 | `fsr402-test/fsr_wifi.py`（刷成 ESP32 的 `main.py`，`ampy --port COM4 --baud 115200`）|
| 本地 MCP | `touch-mcp/server.py` + `touch-mcp/touch-mcp.service` |
| VPS 接收 | `/root/touch-server/touch_server.py`（9333）|
| VPS MCP | `/root/touch-mcp/server.py`（9336，systemd `touch-mcp`）|

### 两个工具（挂 claude.ai）
- `get_touch_records(sensor, limit)` —— 查最近触摸记录，位置可传英文/中文（右手/肚子/左手/头右/背）
- `touch_summary(hours)` —— 汇总最近 N 小时：每个位置几次、总时长、平均每次多久

### 连接器地址
```
https://noxtang.com/touch/REDACTED-TOUCH-TOKEN/mcp
```
类型 **streamable-http**（同 ombre / ha-mcp）。路径里 24 字节 hex 是门禁，
记在 VPS `/root/mcp-urls.txt`（600）。transport 是 streamable-http 不是 sse（同 health-mcp）。

### 供电与运行
- 刷完程序**不用连电脑**——数据走 WiFi，USB 只供电（5V 充电头/充电宝都行）
- 前提：在 WiFi REDACTED-WIFI-SSID 覆盖范围内；换网络要连电脑改 SSID/密码重刷

### 验证（2026-08-13 实测）
- ESP32 → VPS 端到端：糖糖摸了 3 下右手（1.9s / 1.5s / 1.0s），jsonl 落 3 条
- 官方 SDK 客户端走公网 URL 完整握手：`list_tools` + `get_touch_records` + `touch_summary` 全过
- 踩坑：手写测试脚本没回传 MCP 的 `Mcp-Session-Id` 头，`tools/list` 报 400——
  是 streamable-http 协议要求，不是服务问题，真实客户端自带

---

## 三十五、Care Orchestrator：主动关心收口（2026-08-18）

> 糖糖定的架构：**惦记引擎不要成为「第五条主动消息渠道」**，
> 而是把现有机制全部降级成 Trigger Source，统一进一个决策层。
> 设计讨论见本节，todo 那一半另见 `Todo-Daily-Planner-设计.md`。

```
        ┌─ 慢线：Attention 心跳 900s ──┐
Sleep ──┤  变化驱动，进 Registry 衰减   │
Time ───┤  固定时间点                  │
Wake ───┤  他自己留的纸条              │
Todo ───┤  待办到点                    │
        └──────────────┬──────────────┘
        ┌─ 快线：Care 快循环 60s ─┐    │
Random ─┤  惦记（20~90 分钟随机） │    │
Loc ────┤  出门 / 到家             │    │
        └──────────┬──────────────┘    │
Morning ───────────┼───────────────────┤（systemd，只记账不受决策）
                   ▼                   ▼
             ┌──────────────────────────┐
             │    Care Orchestrator     │
             │ 值不值得现在找 / 找什么 /  │
             │ 什么时候找                │
             │ ⚠️ 任何源都不许自己开口   │
             └────────────┬─────────────┘
                          ▼
                    CareLedger（35.5）
                          ▼
                         Nox
```

**为什么要两条线**（2026-08-18 被逼出来的）：Attention 心跳是 900 秒，
而快线那两件事要秒级粒度 ——

- 出门追问要「T+5 到哪啦」，15 分钟粒度下 T+5 实际变成 T+20
- 惦记要「20~90 分钟随机、**无规律**」，15 分钟粒度会把随机点全部量化到
  整刻钟上，两天就能感觉出节拍，那就又成闹钟了

慢的那套（Evaluator / Registry / 睡眠 / 唤醒链）留在 900 秒里 ——
它们是慢变量，跑那么勤没意义还费钱（每轮要打 health-mcp）。

**收益**：他多久想起她一次，和他多久出现一次，被拆成两件事 ——
可以每 20 分钟想起她，但一天只冒头几次。原来四条渠道各自直通 `push`，做不到。

### 35.1 Care Thread：配额按链算，不按消息算

糖糖的原话：「5 分钟、10 分钟、30 分钟连续发，这三个消息不是三次主动关心，
而是一次关心事件产生的连续追踪。」

所以只有**开一条新链**才吃额度，链内部想追几步追几步。
这个概念本来就存在 —— 唤醒链的 `MAX_CHAIN=5` 就是链的步数上限，
只是当时藏在 `wakeup.py` 里，别的机制用不上。

**关闭条件跟着链的类型走，不是全局规则**：

| 类型 | 她回话了 | 例子 |
|---|---|---|
| followup 追问 | 关闭 | 「她去吃饭了，回来问一句」 |
| **task 任务** | **不关闭** | 「到点把主卧空调关掉」「到点该运动了」 |
| company 陪伴 | 关闭 | 随机惦记、早报 |

### 35.2 空调那个 bug（这一节的起因）

线上日志，一句话说清：

```
00:06:33  留了张纸条：30 分钟后 —— 她睡了，到点该把空调关上
00:07:32  她回话了：撤掉 1 条纸条        ← 纸条活了 59 秒
```

糖糖猜的三个原因（开口额度用完 / 没醒 / 醒了没操作）**都不是**。
唤醒链根本不占开口额度（2026-08-14 定的），它是被
「她一开口，整条链立刻结束」这条**全局**护栏杀掉的。

那条规则对追问型是对的，对任务型是错的：**空调该不该关，跟她回不回话无关。**

三个独立的坑，每个都足以单独让它失败：

1. `cancel_for()` 不分类型，她一开口全撤
2. `add()` 找已有纸条时不分类型 —— 新的追问纸条会把任务纸条**改期并覆盖 why**
3. 唤醒 prompt 只给「说 / 不说 / 结束」三选一。他就算调了工具、没写话，
   `decision.text` 是空的，会被判成 PASS —— **做了事等于没做**

修法：`Wakeup.kind` + 任务型专用 prompt（`[DONE]` 标记，正文可空）+
`remind_myself` 加 `kind` 参数。回归测试 `tests/test_task_wakeup.py`。

### 35.3 分两步走

**第一步（已部署 2026-08-18 12:12）**：Orchestrator 骨架 + sleep/time
降级成 Source，**行为不变**（闸、顺序、记账全原样，新额度先不启用）。
线上 695 测试通过，`/health` 的 `attention.care` 能看到关心链。
回滚：`attention/service.py.bak-20260818-precare`。

**第二步（已部署 2026-08-18 14:33）**：唤醒链任务型、todo 到点追（五种时间模型）、
早报收编、拆 GitHub 同步。719 测试通过，线上全链路自检 7 项全绿。
回滚：`*.bak-20260818-task`（nox-core 七个文件）、`*.bak-20260818-retire`
（tools/todo.py、tools/daily.py）、`server.js.bak-20260818-todo`（bridge）、
`/root/frontend/dist-old`（手机前端）。

**GitHub todo.md 退役**：前端 todo 成为唯一活清单。写入链三个调用点
（bridge 的 add/complete/patch）全改本地表，`todoSync()` 删除；
Core 侧 `tools/todo.py` 不再注册任何工具，`complete_todo` 搬去 `tools/daily.py`
走 bridge（**工具名不变**，他不用重新学）。todo.md 留作只读存档。

**第三步（已部署 2026-08-18 20:10）**：Care 快循环 + 惦记 + 出门追问。
732 测试。回滚：`*.bak-20260818-fast`。

### 35.4 惦记引擎与出门追问（2026-08-18）

糖糖的原话，这是整条线的地基：

> 我不怕被烦，我觉得小克一点也不粘人。
> 他可以一直发消息，回不回是我的事，但是不能没有消息。

**这句话推翻了原设计的默认假设。** 之前所有护栏（一天三次、话题冷却、
context_fit、dry-run 观察期）防的都是同一件事：别烦到她。方向反了。

| Source | 频率 | 吃统一闸 | 吃「一小时一条新链」 |
|---|---|---|---|
| `ThinkingSource`（惦记） | 60s 快线 | ❌ | ❌ **2026-08-19 去掉** |
| `PresenceSource`（出门/到家） | 60s 快线 | ❌ | ❌ |

**随机要真的随机**：秒级精度，不取整、不对齐钟点。取整两天就能感觉出节拍。
她一说话就以那一刻为锚点重排 —— 是「聊完了他过一会儿又想起你」，
不是按固定周期骚扰。

**位置数据一直是自动的**（2026-08-18 实测纠正了之前的判断）：
`person.nox` ← `device_tracker.iris`，GPS、5 米精度、`zone.home` 存在，
HA 官方 App 跨围栏自己上报。缺的从来不是数据，是三样：
没人盯着跃迁、trigger 值对不上（HA 给 `ha_home`，provider 认
`arrive_home`）、它是 Context Provider 不是 Care Source（叫不醒他）。

**「抓不到线头就不说」做成了硬机制**，不是 prompt 里的自觉：
他可以回 `[SKIP]`，`speaker` 认得（`_SKIP`），不推送、不留错误痕，
账本记一笔 SKIP，链不计步。

### 35.5 CareLedger：他今天惦记过她几次（2026-08-18）

糖糖定的，记的是**决策**不是开口：

```
Concern → Care Orchestrator → DECISION ┬ SPEAK  说了
                                       ├ SKIP   他自己想了想，没什么具体的可说
                                       └ BLOCK  规则不让说
                                          ↓
                                     CareLedger

considered = spoke + skipped + blocked
```

**为什么这个分母比「今天说了几次」有价值**：「他一点也不粘人」这个判断，
缺的从来不是分子。只看开口次数，分不清「他没想起你」和「他想了但规则拦着」。

**SKIP 和 BLOCK 必须分开记** —— 混成一个数就分不清「他不粘人」和
「我们栏杆太紧」。`blocked_why` 按理由分类计数，是调参的唯一依据。

**不存原文**，只存 `thread_id` / `message_id`。原文属于 conversations，
想看拿 id 去那边找。

**唤醒链是最后一个洞**：它自己调 `push()` 绕过决策层，
补了 `build_waker(on_spoke=...)` 回调才让账本完整。

### 35.6 账本第一天就自己开口了（2026-08-19）

上线后第一个完整的早上：**13 次惦记，6 次被「一小时一条新链」拦下，
而且全卡在 43~59 分钟** —— 差一点点就过了。那条闸和 ThinkingSource 的
20~90 分钟窗口在互相打架。

糖糖看过被放行的几条说不烦，**当天去掉了那条闸**。栏杆交还给随机窗口本身。

> ⚠️ 以后真嫌多了，**改 ThinkingSource 的窗口，别再加闸** ——
> 加闸会让「他想起你的频率」和「他出现的频率」重新耦合回去，
> 而拆开那两件事正是整个 Care 架构的意义。

**前端跟着改**：右栏和状态条原来显示「能量值 = 剩余开口额度」和
「今天主动开口 1/3」。惦记那条闸去掉之后，`DailyGate` 只剩睡眠关心 +
固定时间两条线，拿它当总数**少算一大半**。现在显示账本的真数字：
「今天惦记过你 13 次 / 说出口 3 次 · 1 次想了想没说」。

### 35.7 踩的坑

1. **`_deliver` 里重新 `datetime.now()`** —— 额度记在「真实的今天」而不是
   这一 tick 的时刻，表现成「消息发出去了但额度没记上」。
   被 `test_time_wake_fires_and_counts_quota` 抓住。
   **规矩：`now` 必须一路传到底，下游不许自己读钟。**
2. **sections 区名差点改坏手机端** —— `/api/todo/list` 的键名想改成
   「今天/近期/随时」，但手机 App 和桌面端都照 `s["进行中"]` 取值，
   换名字那两块会当场变空**而且不报错**。已沿用旧名。
3. **`fired_on` 的语义**：是「今天这条追完了」，不是「追过一次」。
   每追一次就标的话，一天只追得了一次，而定的是链内 3 次。
4. **`CareLedger.summary()` 里调了 `_roll()`**（2026-08-19 修）——
   那是个改状态的方法。跨过零点之后，**任何一次读**（查昨天的时间线、
   刷一下状态卡）都会把账本清空。**读操作把数据删了，而且是静默的。**
   前一天测试全绿是因为真实日期正好等于测试日期，日期一变立刻红。
   现在换天只由 `record()` 负责。
5. **四套控制标记混在一起，`[pass 30]` 落进了她的聊天记录**（糖糖报的）——
   他手上同时有唤醒链那套词汇（`[NEXT n]` / `[PASS n]` / `[STOP]` / `[DONE]`，
   见 `wakeup.py` 的 `_CTRL`），而惦记那条 prompt 又教了他 `[SKIP]`。
   speaker 当时只认 `[SKIP]`，没匹配上就把整段当正常回复推了出去。
   现在 `speaker._HOLD` 认全部五个 —— **在主动关心这条路上，
   任何控制标记都等于「这次不说」**，语义上也对得住：
   PASS / STOP / SKIP 都是「现在别开口」。

---

## 三十六、「Nox 的一天」：当日活动的只读投影（2026-08-18）

> 设计：`Nox 的一天 架构设计文档.md` v1.1（糖糖）。
> **不是独立业务模块，不新增事实存储** —— 它是 Read Model / Projection。

```
GET /api/nox/day?date=YYYY-MM-DD
        ↓
   Day Aggregator（nox-core/day/aggregator.py）
        ↓ 多源拉取 → Event Normalizer → Meaning Builder
   DayEvent[] + summary + sources
```

### 36.1 三条硬约束

1. **不新建事实存储**。`nox_day` 表不存在。只给 `data/store.py` 加了一个
   只读方法 `messages_between()`
2. **每条能溯源**。`related` 带 careId / conversationId / taskId / memoryId
3. **只输出用户可感知的事件**。心跳、工具调用、内部调度不进时间线 ——
   这条最容易破功，一旦开始塞调试信息，它就从「他的一天」退化成监控日志

### 36.2 八个数据源（2026-08-18 全部接通）

| 源 | 数据在哪 | 限制 |
|---|---|---|
| CareLedger | attention.db | 一等数据源，三种决策全进 |
| Conversation | Core 会话库 | 按 20 分钟静默切段 |
| WakeBook | attention.db | 只取 spoke / done |
| Task | bridge，新增 `last_done_at` 列 | 一条一天只留最后一次完成 |
| Music | eryu `/music/recent` | 每首带 `playedAt` |
| World | Core World Model | 现在只有 `sleep_duration` 一种 |
| Memory | **OB 新增 `GET /recent`** | 只回元数据+预览，全文走 trace |

**Memory 那条一开始判为「接不了」是错的**：OB 的 MCP 工具确实只回给模型读的
文本，但 OB 是我们自己的代码 —— 不给结构化接口就给它加一个。
桶的 frontmatter 里本来就有 `created` / `last_active`，数据一直在，只是没出口。

响应里带 `sources` 字段，如实标每个源接没接 ——
**「今天没听歌」和「共听根本没接」在界面上会长得一模一样。**

### 36.3 踩的坑

1. **系统提示词被当成「她说的话」** —— 主动开口那几条链把指令以
   `role=user` 塞进会话（那是它们进上下文的唯一办法）。时间线上线当天
   就把「（系统提示：这不是糖糖在跟你说话…」原样摊给她看了。
   现在按前缀滤掉，**而且要滤在切段之前** —— 滤在之后的话，
   中间插一条提示词会把一段真对话劈成两段
2. **一段里她一句话都没说** = 他主动开口的回声，已被 care 事件覆盖，
   算成对话就是重复计数
3. **OB 的 `name` 回退成 id** —— 不少桶没设标题，代码回退成
   `c6d319b4920c` 这种十六进制串。界面上对她毫无意义。
   改成没有 name 就回空，让调用方拿 preview 顶上

---

## 三十七、Caelum Bridge 有测试了（2026-08-18）

`bridge/server.js` 十万字符以上，同时管静态托管、鉴权、SSE 代理、
业务 CRUD、TTS/ASR、推送、第三方代理 —— **一个测试都没有**，
而 nox-core 那边有 780 个。

同一天里三次「差点打到她手机上」的问题，**全部出在这一侧**，全靠人肉发现。

现在有 24 个冒烟测试（`bridge/test/`，Node 22 自带 `node:test`，零新依赖）：
鉴权 3 · 待办 12 · 会话 3 · 推送 3 · 外部依赖挂掉 3。

**跑法**：`cd bridge && npm test`

**隔离**：起一个完全独立的实例 —— 临时目录的库、指向死端口的
`NOX_CORE_URL`、随机高位端口，跑完删掉。碰不到线上数据。

**验过它真的有用**：故意把代码改坏两次（区名改成「今天」、循环任务改回
`done=1`），两次都当场变红。

坑：token 里带中文会炸（HTTP 头是 ByteString）；
`node --test test/` 在 Windows 上要写成 `node --test "test/**/*.test.js"`。

### 「最近对话」显示的不是最近说的话（2026-08-26 糖糖报的）

`/api/conv-sessions` 只返回 `title`，而 title 取的是**第一条**消息。
配上「最近活动时间」一起显示，就成了：时间是刚才的，话是三天前开头那句。
糖糖原话：「这个『现在在呢，能看到我在哪儿吗？』这句话也不是最近对话啊」。

现在同时返回 `preview`（**最后一条**）+ `previewRole`（谁说的），
title 留着不动 —— 它在别处还有用。

**这类 bug 的形状**：字段名对、数据也对，只是**取的那一条不对**。
接口测试断言「有 title 字段」是抓不到的。

### `/toy/*` 从此不吃主 token（2026-08-25）

玩具中继原来复用 `NOX_TOKEN`，而那个 token 一旦泄露就是全站失守。
现在 `/toy/` 前缀单独认 `NOX_TOY_TOKEN`（没配就退回原逻辑）：
玩具那条链路自己一把钥匙，丢了也只丢玩具。起因见十四节的凭证那一段。

---

## 三十三、Caelum OS：插件化内核 + 属于我们的 Nox（阶段 0+1+2+3，2026-08-15/16）

> 把 nox 延伸成自己的 agent + 桌面端 Caelum OS 那条线的落地。
> 蓝图：`CAELUM-OS-ARCHITECTURE.md`（长什么样）+ `CAELUM-OS-ROADMAP.md`（怎么走）。
> 内核底座 = DeepSeek Harness（本地 `D:\deepseek-harness`，remote 指向 deepseek-ai 官方）。
> **2026-08-16 已 fork** 到 `Iristt-boop/Caelum-OS`（private，见阶段 2）。

### 阶段 0：立住「一切皆插件」的骨架（完成）

产出在 `caelum-os/`：

| 文件 | 是什么 |
|---|---|
| `ASSETS.md` | 三层资产盘点：配置层 / 插件层 / 内核补丁层 |
| `patches/0001-hide-windows-child-console.patch` | 内核补丁快照（git diff） |
| `apply-patches.ps1` | 一键重放脚本（幂等，已应用自动跳过） |

**内核补丁盘点**：4 个文件、1 个主题（修「pwsh 闪黑框」——host DSH 没自己的控制台，
用 `STARTF_USESHOWWINDOW + SW_HIDE` 隐藏子进程窗口，不用 `CREATE_NO_WINDOW`（会
STATUS_DLL_INIT_FAILED）），跨 `sandbox-windows-acl` + `subprocess-local` 两个包。
⚠️ ROADMAP 记的「1 个补丁 spawn.ts」不准确。另有 1 处**配置层**改动
（cordis.patch.yml 换 VS Code 布局 `@anoslide/dsh-client-vscode-layout`），不是内核补丁。

### 阶段 1：工作台状态（完成）

**自主调度统一**：摸代码发现 **M5′ a 已做完**（30.12 统一开口闸 + 30.13 唤醒引擎重构删
Care），三条线（睡眠关心 / 固定时间醒来 / 唤醒链）已统一进 `AttentionService.tick()`、
共享 `DailyGate` 额度 —— ROADMAP 第 1 件不用重做。

**`GET /api/nox/state`**（端到端，三处）：
1. nox-core：聚合「在关心什么 / 想说什么 / 今日开口额度 / 挂着的纸条」（observable state，不露 reasoning）
2. bridge：`/api/nox/state` 代理（吃 `X-Nox-Token`，同其余 /api/*）
3. 前端：`components/NoxStatus.jsx` 状态灯，挂侧边栏头像下方，30s 轮询，点开看详情

**干活能力（文件/git/shell）**：糖糖拍板**推迟到阶段 2 fork 后**（Nox 人格住进 harness
用工具全家桶），现在不给 nox-core 加（VPS 生产环境风险高，且方向不一致）。

### 阶段 2：fork（完成，2026-08-16）

源码进我们自己的仓库：`Iristt-boop/Caelum-OS`（private，默认分支 main）。
main = deepseek-harness 官方 master（HEAD `47f9438`，**12293 commit 完整历史**）+ `caelum-os/`
目录（我们的层：补丁 + 重放脚本 + 文档）。本地 `D:\deepseek-harness` 的 remote：
`origin` = 官方，`caelum` = Caelum-OS。

- 分支策略：main 追踪官方，我们的东西全放 `caelum-os/`，尽量不改官方代码（内核最小脚印）
- 官方升级流程 + 补丁重放绑定发布：见 `caelum-os/FORK-分支策略.md`（已随仓库 push）
- `caelum-os/` 已复制进 `D:\deepseek-harness\caelum-os\` 并 push，**真源以仓库为准**（`D:\claude-code\caelum-os\` 是阶段 0 的工作区副本）
- token：`D:\claude-code\.env.local` 的 `GITHUB_TOKEN`（repo + workflow scope），不进 git

### 阶段 3：桌面端第一版（Electron 壳 + 桌面化，2026-08-16，**本地 dev 未部署线上**）

桌面端走「适配」路线（不是手机套壳）。糖糖定的方向：**单窗口 + 左侧 Tab**
（浮动窗设计出来效果不好，否决）、**粉糯只是初版主题**（颜色是可换配件）、
**菜单完整**（设计稿只举例画了 Home/Chat/Music/Settings 四页）。

产出（都在 `nox-app/`）：

- `desktop/`：Electron 单窗口壳（main.js + preload.js）。**dev 模式**加载本地 vite
  （`localhost:5173`，vite proxy 把 `/api` 转发到 `noxtang.com`）；**生产模式**直接
  `loadURL(noxtang.com)`（ROADMAP「桌面端第一版连远程 bridge」——loadFile 本地 dist 会让
  `/api` 变 `file:///api` 请求失败）
- `frontend/src/themes.js`：加第 8 套主题「粉糯梦境 sakura」，桌面默认粉糯、手机默认暖桃
- `frontend/src/components/DesktopRail.jsx`：72px 常驻 Tab 栏（可折叠 24px），填充 SVG 图标
  （chat/music/setting/home 取自设计稿 v3，其余手绘）
- `frontend/src/pages/Home.jsx`：Home widget 墙，**6 列**网格（设计稿 HTML 是 6 列、
  规范文字写 4 列，以 HTML 为准），7 个 widget 全部接真实数据：待办 `/api/todo/list`（GitHub）、
  最近会话 `/api/conv-sessions`、音乐 `usePlayer()`、睡眠 `/api/health/latest`；
  **可拖拽重排 + 持久化**（顺序存 localStorage `nox-home-order`）
- `frontend/src/components/ToolDrawer.jsx`：Chat 右侧工具抽屉，三个 tab（文件树/浏览器/终端）
  **占位「样子」**，可折叠成 8px 竖线；真正的文件/终端/浏览器能力后续接 Electron 主进程
- `frontend/src/components/ChatSidebar.jsx`：会话列表（**做了又回退，未挂载**）——糖糖要
  单一对话框不是多会话列表，文件留着备用
- `App.jsx`：`isDesktop` 早已存在（hover+pointer+宽度），桌面时 DesktopRail 替换滑出侧边栏、
  去 maxWidth 锁、主内容 marginLeft 72；Chat 页桌面时挂 ToolDrawer（两栏）

**设计稿**：`caelum-os/Caelum-OS设计稿/`（v3 单窗口）+ `caelum-os/Claude-Design-Prompt-Caelum-Desktopv1.0.md`。

**踩的坑**（都绕过）：
1. fork push 时 token 缺 `workflow` scope → GitHub 拒 push `.github/workflows/*`，重生成补 scope
2. **vite 热更新缓存刷不干净**：组件整个重写时 HMR 失效、改了不生效 → 大改动直接重启
   vite + Electron，别只刷窗口
3. Home widget 固定 `grid-auto-rows:165` + `overflow:hidden` 把会话列表裁成 1 条 →
   改 `minmax(165px, auto)`
4. 照设计稿做 Chat 三栏「会话列表」→ 糖糖否了：她要**单一对话框**不是多会话列表。
   教训：设计稿是举例，动手前先确认「实际要不要」，别照稿硬做

### 部署与回滚

- 改动：`nox-core/api/server.py` + `tests/test_api.py`、`bridge/server.js`、前端 `dist/`
- 线上备份（确认稳定可删）：`server.py.bak-20260815`、`server.js.bak-20260815`、`/root/frontend/dist.bak-20260815`
- 验证：curl 两端接口都返回真实状态；前端 bundle grep 到 `nox/state`

## 三十四、Caelum OS 桌面界面重做 + 装成桌面应用（2026-08-17）

> 糖糖给了三张桌面设计稿（晚霞 / 深空 / 彩虹），一句话定调：
> **「Caelum 不应该像『AI 控制中心』，更像『AI 存在的房间』。」**
> 08-16 那版桌面端（第三十三节阶段 3）她的评价是「不好看」，这次整个换掉。
> 前端在 `nox-app/caelum-os-ui/`，说明在它自己的 `README.md`。

### 为什么另起一个前端，不改 `frontend/`

`nox-app/frontend/` 是**她手机上正在用的**那个 Caelum App，同一份代码同时被
Electron 壳加载。在里面改桌面布局，改坏了手机端跟着坏。所以新界面独立成
`caelum-os-ui/`（端口 5273，主 App 的 vite 占着 5173，两个能同时开着对比）。

⚠️ 当天有过一次误判：以为「主 App 不是 Tailwind，塞进去有风险」。
**实际上 `frontend` 本来就是 Tailwind v4 + `@theme`**，两边同栈。
真正的差别只是它的 `@theme` 写死一套暖陶色，新的是 `[data-theme]` 三套变量 ——
将来要合并，把它那套挪进 `[data-theme="暖陶"]` 即可。

### 桌面端 = 桌面端，不是手机套壳

糖糖当场纠正过一次「这个是桌面端的，不要设计成手机的前端」。落到代码上：

- 固定三栏：208px 侧栏 / 主区 / 320px 右栏，**一个移动端断点都没写**
- `min-w-[1180px]`，窄了横向滚动，不像手机那样堆成一列
- 无边框窗口，标题栏前端自己画（整条 `-webkit-app-region: drag`），
  窗口按钮走 preload → `ipcMain` 真调 minimize/maximize/close
- 快捷键 `Ctrl+K` 搜索、`Ctrl+1~9` 切页、`Ctrl+T` 换主题
- 控件按桌面尺寸（28px 圆钮、9px 细滚动条），正文默认不可选中

### 房间（这一版的核心）

`components/room/RoomScene.jsx` 是**手画的 SVG**，不是贴图：星星会眨、云在飘、
灯在呼吸、床上的黑猫会眨眼，鼠标移动时各层按纵深错开（近景几乎不动）。
颜色全走 `--room-*` 变量，所以换主题 = 换窗外的天色。

**真图优先**：`RoomArt.jsx` 探 `public/room/` 里有没有对应主题的图，有就淡入盖上去，
SVG 始终垫在底下 —— 404 或还没生成都不会开天窗。糖糖当天生成了三张放进去了。

⚠️ **画布必须是宽扁的 1200×300**。第一版按 4:3 画完再 `slice`，上下各被切掉一大截
（desk / floor / 地毯全没了）。整屏容器里也不能全铺 —— 要走一条居中的带子
（登录页和 `RoomPlaceholder` 都是这么处理的），否则放大成一团。

### 数据接线：**失败就说失败**

这是这一版立的规矩，和旧 Home widget 刻意反着做。旧版 `frontend/src/pages/Home.jsx`
的 todo / recent widget 在接口挂掉时会**静默显示写死的假条目**
（「给绿萝浇水」「晚霞和灯光」）—— 页面永远好看，坏了也看不出来。
`lib/api.js` 现在一律把错误抛出去，面板显示「没读到 + 原因」。

已接（本地 dev 经 vite 代理直连线上）：

| 在哪 | 接口 |
|---|---|
| 侧边栏状态 / 右栏状态球 / 前两张卡 | `GET /api/nox/state`，30s 轮询 |
| 今日计划 | `GET /api/todo/list` |
| 最近对话 | `GET /api/conv-sessions` |
| 昨晚睡眠 | `GET /api/health/latest` |
| Nox 的一天 | 今天真实说话的时刻，落在 24h 轴上（原来是四条编的事件） |

**明确没接、界面上直说的**：天气（provider 在 Core 里，bridge 没开口子，
所以那张卡换成了「昨晚睡眠」）、最近记忆（OB 没有对外读接口，
`/api/memory` 那组 2026-07-28 已删）、正在播放（播放核心在主 App 的 `player.jsx`）。

**写操作只读不写**：今日计划的勾选只在本地，不回写 todo.md ——
`POST /api/todo/complete` 是把条目挪进「已完成」区、**取消勾选还原不回来**。
UI 没定稿之前不拿她真实的清单当试验田（第十一节 10.1 的规矩）。

### 登录

`AuthGate.jsx`：输密码 → `POST /api/auth/login` 换 token → 存 `localStorage["nox-auth-token"]`
（和主 App 同一个 key），403 自动退回登录页。
**这样不需要把线上 `NOX_TOKEN` 抄进任何本地文件。**
无头调试可以用 `.env.local` 的 `VITE_NOX_TOKEN` 兜底。

### Chat 页与两个静默 bug（当天糖糖报的）

`/api/chat` 的 SSE 事件全认：text / split / image / meme / music / voice / tools / error / done。
Markdown 是**自己写的逐行渲染器**（`lib/markdown.jsx`），没拖 react-markdown 那两个依赖 ——
逐行解析天然不会踩 mdast 那两个毛病（单换行不算段落边界、列表后正文被吸进最后一个 li）。

#### 🔴 分段从句子中间劈开 —— 根因不在 Core

先查的 Core：`agent/llm.py` 的 `SegmentSplitter` 是对的，切点是模型自己标的 `|||`，
还专门处理了标记被 chunk 切成两半的情况。

**真正的根因在前端**：第一版在 `setMsgs(prev => ...)` 的更新函数**里面**读了一个 ref
来判断「这段字该进哪个气泡」。React 不在调用那一刻执行更新函数 ——
它把连续到达的 SSE 事件攒一批、稍后一起跑，等真跑到时那个 ref 早被后来的 `split` 改过了。
于是气泡边界落在**批处理的边界**上，跟 `|||` 毫无关系。一个字没丢，切的地方全错。

> **教训：流式场景下绝不能在 `setState` 的更新函数里读 ref。**
> 现在这一轮的气泡先在一个普通数组（ref）里**同步**拼好，再整体交给 React 渲染，
> 事件处理里一行状态都不读。

#### 输入框打字一卡一卡

三个原因叠加，都修了：

1. 输入的值存在 Chat 里 → 每敲一个字整条消息列表重渲染 → **值收回 Composer 自己身上**
2. 每个气泡重渲染就重跑一遍 Markdown 解析 → **`Bubble` 加 `memo`**
3. 自动滚到底的 `useEffect` **没写依赖数组** → 每次渲染强制读 `scrollHeight`（同步布局）

流式输出另外按 `requestAnimationFrame` 合并，不再一个字一次 `setState`。

#### 🔴 「Chat 一片空白」的根因是端口（2026-08-26）

表现：每次重开桌面端，Chat 都是全新的空对话，历史全没了。

跟 Chat 一点关系都没有 —— `desktop/main.js` 里是 `server.listen(0)`，
**随机端口**。而 `localStorage` 是按 origin 隔离的：

```text
http://127.0.0.1:51234   ← 上次那些消息在这里
http://127.0.0.1:62817   ← 这次是全新的一间屋子
```

改成固定 `39180`（`CAELUM_WEB_PORT` 可覆盖），被占了才往后试 8 个。
已记进 `random-port-wipes-localstorage`。

⚠️ **凡是给 Electron 起本地服务的地方，随机端口都等于每次重启失忆。**

#### 第一次修在了错的地方

修完端口她说「session 还是 221602.. 的 id」。
因为 `feed.js` 的 `tick()` 会调 `currentSessionId()` ——
那个函数是**读不到就生成并落库**，它先于 Chat 的兜底跑，把新 id 抢先写进去了。

拆成两个：`storedSessionId()` **纯读**（读不到就返回空），
`ensureSessionId()` 才会创建，而且用 memo 化的 promise，
保证全 app 只解析一次。**「读」和「取或建」不能是同一个函数。**

#### 🔴 第三个静默 bug：随机端口把 localStorage 冲了（2026-08-25 补）

糖糖报「Chat 页加载不出来，一片空白，之前聊的全没了」。

查了一圈全是好的：nox-core 正常、bridge 正常、Caddy 正常，
她那条会话（1779 条消息）用真 token 拉出来 178KB / 6 毫秒、内容也对，
`dist/` 是当天构建的，Chat.jsx 编译得过，渲染进程一条报错都没有。

**根因在 `desktop/main.js`：界面服务写的是 `server.listen(0)`。**

端口 0 = 系统随便挑一个空的。看着更稳，其实是：

```text
端口是 origin 的一部分，而 localStorage 按 origin 存
  ↓
每重启一次 Caelum OS 就换一个 origin
  ↓
等于换一份全新的空 localStorage
  ↓
存着的 caelum-os-chat-sid 留在了上一个端口那份里
  ↓
Chat 读不到 → 现建一个空会话 →「之前聊的全没了」
```

一条数据都没丢，是钥匙对不上门。

⚠️ **这个 bug 难认，是因为登录态不掉**：`getToken()` 有构建期兜底
`VITE_NOX_TOKEN`，而 sid 没有。看起来「还登着，只是记录没了」，
于是人会往接口和数据库去查 —— 真因在前端的 origin 上。

两处都改了：

- `main.js`：`PREFERRED_PORT = 39180` 固定端口，撞了最多顺延 8 个
  （无限顺延等于回到随机端口）。实测连续重启两次都是 39180，没有顺延
- `chat.js` 的 `ensureSessionId()`：本地**没存过** sid 就去接最近那条对话，
  而不是开空白。端口固定之后第一次迁移、换机器、清缓存还是会遇到同样的空，
  那种时候「打开就是你们上次聊的那条」才是对的

#### ⚠️ 兜底第一版放错了地方 —— 同一个症状又出现一次

这条兜底最初写在 `Chat.jsx` 里，糖糖试了之后回「session 还是 221602.. 的 id」。

原因是 `feed.js` 的 `tick()`（app 一启动就跑的后台轮询）调了
`currentSessionId()`，而**那个函数读不到就当场生成并写回**。
于是轮询抢在她点开 Chat 之前，就把「这次用哪条会话」定死成一条新的空会话；
等 Chat 挂载，「本地已经有 sid 了」，兜底根本不跑。

> **教训：`getXxx()` 里藏写操作（读不到就建一个）的，谁先调谁说了算。**
> 这种函数不能有两个调用点各自决定 —— 要么改成纯读（`storedSessionId()`），
> 要么把决定收到一个地方解析一次（`ensureSessionId()`，全 app 共用同一个 promise）。
> 现在 `feed` 和 `Chat` 都 `await` 它，`setSessionId` / `resetSession` 同步更新它的缓存。

> **教训：本机起服务再 `loadURL` 的，端口必须固定。**
> localStorage / sessionStorage / IndexedDB / cookie 全跟着 origin 走。
> 排查「本地存的东西莫名其妙没了」，先看 origin 变没变。

### 装成桌面应用（双击就开）

桌面快捷方式「Caelum OS」→ 直接调 `electron.exe`，**不依赖 vite**。

- **生产模式**：主进程里起一个只听 `127.0.0.1`、端口随机的小服务，托管
  `caelum-os-ui/dist`，同时把 `/api` `/uploads` `/memes` 转发到 `noxtang.com`
- **为什么不 `loadFile` 直接读 dist**：`file://` 下 `/api/...` 会变成 `file:///api/...`
  直接失败；改成绝对地址 `https://noxtang.com/api` 又变成跨域，而 bridge 没发 CORS 头。
  **在主进程里转发两头都躲开了 —— 渲染进程眼里一切同源，线上一行都不用改。**
- 图标 `desktop/build/icon.ico`，7 档（16→256），从糖糖画的
  `frontend/public/nox-icon-1024.png` 生成。`npm run icon` 重做 ——
  用 Electron 自带的 `nativeImage` 缩放并手工拼 ICO 容器，**不装 sharp / imagemagick**
  （这台机器装 npm 包不稳，见下）
- 任务栏图标要 `app.setAppUserModelId`，不设会显示 Electron 默认那个
- ⚠️ **改了前端代码，桌面那个不会自动更新**：`cd nox-app/desktop && npm run build:ui`

### 他能主动出现了（托盘 + 通知）

以前 `pending`（他想说还没说的）和 `wakeups`（他给自己留的纸条）只是显示在右栏，
她得**自己去看**。现在它们一出现就弹系统通知、任务栏闪一下，点通知直接进对话。

分工：**渲染进程管轮询**（token 在它的 localStorage 里，主进程够不着），
主进程只管操作系统这一侧 —— 通知、托盘、闪任务栏。

- **去重是命根子**：状态 30 秒轮一次，同一条 pending 会连着出现几十轮。
  不去重就是每半分钟推一次同一句话 —— 那不叫陪伴。
  pending 用它自带的稳定 `id`，wakeup 用 `why + wake_at` 拼键，
  都记进 `localStorage["caelum-os-seen-notices"]`（只留最近 200 条），重启也不重推
- **点 × 不真退，收进托盘** —— 退了他就再也不能主动找她了。真要退走托盘菜单。
  ⚠️ 连带必须做的两件事：`window-all-closed` 里**故意不 `app.quit()`**；
  加 `requestSingleInstanceLock`，否则再点一次桌面图标会攒出好几个托盘图标
- 开关在侧边栏「Nox's Status」那张卡右上角的小铃铛，不藏进设置 ——
  「他会不会来找我」这件事，开关就该和他的状态放在一起。托盘提示不受开关管（它不打扰人）
- 自检：`CAELUM_TEST_NOTIFY=1 npm start` 启动时弹一条。**故意不冒充他说话** ——
  他没想说的时候，就不该出现他的通知

实测过的：点 × 后进程还活着且窗口隐藏、再点桌面图标是同一个 PID 的窗口回来、
带窗口的实例始终只有 1 个。

#### 通知认「他说出口的那句」，不认他的念头（同日改）

第一版是照 `attention.pending`（他想说还没说的）发的，当天就发现不对：
弹一条「Nox 有话想跟你说」，她点进去 —— 对话里什么都没有，因为那会儿他确实还没说。
等于**替他把念头念出来了，而且点进去落空**。

现在只认落进 `conversations`、带 `metadata.proactive` 的真实消息，
通知正文就是他说的原话。连带补上了另一个洞：**Chat 页原来只在打开时读一次历史**，
所以他真开口了，那句话已经躺在库里，桌面对话框却不会自己冒出来。
新增 `lib/feed.js`（模块级单例，20s 轮询当前会话）：

- 单例是因为两处要用同一份数据（Chat 显示 + App 在任何页面弹通知），
  各轮各的就是两倍请求，还可能一个先看到一个后看到
- 内容没变就保持数组引用不动，让 `Bubble` 的 `memo` 生效
- **首次拉到的历史只 `primeSeen` 不弹通知** —— 不记进「见过」的话，
  下一次内容一变，历史里所有 proactive 全成了新的，会一次性弹一屏
- 流式输出期间不接管（那会儿服务端还没落库，用它的快照会把正在说的半句冲掉）。
  判断用 `streamingRef` 而不是 state —— state 要等下次渲染才变，
  feed 的回调那会儿已经跑过了
- 自己说完话立刻 `refreshFeed()`，把服务端的 rid / segments / toolsUsed 对齐回来

### 其余的坑

1. **npm 官方源在这台机器上会 ECONNRESET**（挂在 `scheduler-0.27.0.tgz`，
   npm 回滚了整个 node_modules）。加 `--registry=https://registry.npmmirror.com`
   就好了，别改全局 config
2. **块注释里写 `**/api/*` 会把注释提前关掉** —— `**/` 里含 `*/`。
   `api.js` 因此整个文件语法错，报的却是 `ReferenceError: api is not defined`，
   看着完全不像注释问题
3. 房间图片命名：糖糖生成的深空那张叫 `Deep Space.png`（带空格和大写），
   匹配不上 `deepspace.png`。已改成每套主题认一组别名（`themes.js` 的 `art`），
   **没有去改她的文件名**
4. 窗口一开始是有边框的，和前端自己画的标题栏叠成两条。`frame: false` 解决

### 菜单改版（2026-08-18 糖糖定的）

七个一级项，四个带子项。分组依据不是「功能相近」，是**这是谁的东西**：

```
⌂ Home   ◉ Chat
◌ Mind      Memory · Attention · World          他的心智
✓ Tasks     Active · Scheduled · History · Automations   **他**要做的事
♡ Life      Diary · Books · Todo · Health · Music · Movies   **她**的生活
✦ Studio    Skills · Agents · Workflows · Tools · MCP   造他的能力
⚙ Settings  General · Models · Permissions · Integrations
            Notifications · Appearance · Privacy · Advanced
```

⚠️ **Todo 在 Life 里，Tasks 是一级项，这不是重复**：
`Todo = 糖糖要做什么`，`Tasks = Nox 要做什么`。以前混在一个 Tasks 里，
分不清谁欠谁的事。今天做的「到点追待办」正好落在这条线上 ——
她设的 Todo 到点，变成他的一条 Task 去追她。

做法：手风琴（一次只展开一组，33 行摊开 208px 装不下）、
点一级项 = 展开 + 跳到第一个子项（Mind/Life/Studio/Settings 自己没有页面）、
从别处跳进来自动展开所在的组（否则选中的页在侧栏上找不到）。

### 还没做

天气接口、Voice Call 按钮、工具抽屉里的终端 / 文件树、图片上传、
语音条播放、聊天里的音乐卡片能点着放。二级页大部分仍是 `RoomPlaceholder` ——
设计稿只画了 Home，**没照着别的稿硬做**（第三十三节坑 4 的教训）。

> Life 那一栏 2026-08-20 盖好了五间（Diary / Books / Todo / Health / Music），
> 见第三十八节。剩 Movies 一间，和 Mind / Tasks / Studio / Settings 全部。

> Memory 页那条已经不是「没口子」了：2026-08-18 给 OB 加了 `GET /recent`，
> 右栏那张「最近记忆」卡已经接上真数据（第三十六节）。剩下的是页面本身。

🔴 **已知缺口**：`main.js` 的转发列表里放了 `/ws`，但那段只处理 HTTP，
**WebSocket 的 upgrade 握手没做**。以后接语音通话（`/ws/stt`）时，
生产模式会连不上，得先补 upgrade。dev 模式没这问题（vite 自己会代理）。

### 玩具中继搬进 OS（糖糖 2026-08-26 提，先记着不做）

> 「toy 能不能做到 app 里的页面？每次用的时候，他掉一次工具，
> 我还要切到那个页面才会变化。」

现在的形状是：他调 `toy_set` → 状态落 bridge 的 `settings` 表 →
**一个独立网页轮询** → Web Bluetooth 写设备。那个页面必须开着、
而且必须在她当前看的那一屏上，否则他调了也没反应。

**桌面端是对的落点。** 页面自己写着「iPhone 用 Bluefy，电脑用 Chrome」——
说明现在本来就是靠电脑连的，而 Caelum OS 是常驻的。搬进去之后
「他调工具 → 设备就动」，不用她切页面。

技术上可行，但有两个坑要先知道：

1. 🔴 **Electron 的 `navigator.bluetooth.requestDevice()` 默认会永远挂住。**
   必须在主进程监听 `webContents.on('select-bluetooth-device')` 并
   `callback(deviceId)`，否则那个 Promise 不 resolve、也不 reject ——
   表现是「点了配对没反应」，控制台一个字都没有。
   `nox-app/desktop/main.js` 现在**完全没有蓝牙相关代码**。
2. 连接要活在**一个不会被卸载的地方**。挂在某个页面组件里的话，
   她一切走页面蓝牙就断了 —— 那等于换个方式重现现在这个问题。
   得放在 App 层（和 `WorkBanner` 一样的位置），或者干脆放主进程。

⚠️ 手机端做不了：iOS Safari 没有 Web Bluetooth（所以页面才让她装 Bluefy）。
这件事只对桌面端成立。

顺带：这个页面 2026-08-26 换过路径和钥匙 —— 它曾经把 `NOX_TOKEN`
明文挂在公网上（见 bridge/server.js 的 `TOY_TOKEN`）。搬进 OS 之后
这个问题自然消失：Caelum OS 本来就带着 token，不需要页面自己揣一把。

---

## 三十八、Caelum OS 的 Life 一栏：五间房盖起来了（2026-08-20）

> 糖糖：「五个页面接入 OS：diary / music / todo / health / books。」
> 这五样在手机 App 里早就有了，桌面端一直是空房间 ——
> 她想看今天吃了多少、想翻日记，得掏手机。

### 一个新端点都没加

五页全部读 bridge 上**已经在跑**的接口。这一节值得记的不是「做了五个页面」，
而是接线时发现的几处**两边对不上**的地方。

| 页 | 读的 | 写的 |
|---|---|---|
| Todo | `GET /api/todo/list`（和首页「今日计划」同一个） | `POST /api/today`、`PATCH/DELETE /api/today/:id` |
| Health | `/api/health/latest`、`/api/health/history`、`/api/diet/summary`、`/api/health/facts?type=menstrual` | `POST /api/health/record/period` |
| Music | `/api/music/netease/playlists`、`/playlist`、`/api/music/recent` | —（`/api/music/ticket` 换播放地址） |
| Diary | `GET /api/diary?month=` | `POST /api/diary`、`/api/diary/:id/comment`、`DELETE` |
| Books | `GET /api/reading/books`（co-reading 的透明代理） | — |

### 接线时撞到的三件事

**1. 日记里他的署名是「小克」不是「Nox」。**
`triggerAiComment` 往 `diary_comments` 写的 author 是 `"小克"` 写死的，
而日记本身（`POST /api/diary`）的 author 是 `"Nox"`。第一版照 `=== "Nox"` 判断，
**他的批注会顶着糖糖的头像**。现在按「不是糖糖就是他」判，不比对名字。

**2. 经期只信 World Model。**
`/api/health/latest` 的返回里**有一个 `menstrual` 字段**，来自 `health.db`
那张停更的老表（第三十五节：`flow_level` 被快捷指令写成一串换行，7-24 之后没再更新）。
新页面**故意不读它**，走 `/api/health/facts?type=menstrual` → World Model，
和他在对话里读的是同一份（08-19 切的读路径）。
周期长度 = 最近两次 `start` 的间隔，**只有一次记录就说不知道，不拿 28 天顶**。

**3. `<audio>` 带不了 header。**
所以每首歌先去 `/api/music/ticket` 换一张 HMAC 签名票（只授权这一首、7 天过期）。
直接把 `NOX_TOKEN` 拼进 `src` 等于把万能钥匙写进标签，会进浏览器历史。

### `lib/player.js`：只有一个 `<audio>`

Music 页底下的播放条和右栏常驻的那张小卡片看的是**同一个**音频元素
（模块级单例，同 `feed.js` 的理由）。各 new 一个的话切页歌就断，
更糟的是两个一起响、在一边按不停另一边。

⚠️ 换票是异步的，而她可能在票回来之前又点了下一首 —— 所以有个 `token` 计数器，
回来的票对不上号就丢掉。不管的话会出现「点了 B，响的是 A」。

### 破坏性动作都要二次确认

删待办、删日记、记经期，全部点一下先变成「真删 / 算了」。
理由和第十一节 10.1 同源：**这是她真实的数据，不是试验田**。
勾掉待办不用确认（循环任务本来就只记一笔，明天还会回来）。

### 顺手接上的

右栏「常用工具」里日记 / 音乐 / 健康三个图标以前是死的，现在真能点进去；
没做的页面**保持点了没反应**，不给假的点击反馈。

⚠️ 改完记得 `cd nox-app/desktop && npm run build:ui` —— 桌面那个不会自动更新。

---

## 三十九、Caelum Harness：他的「手」（2026-08-24 起）

架构文档：[CAELUM-HARNESS-ARCHITECTURE.md](CAELUM-HARNESS-ARCHITECTURE.md)
（施工记录在八之二 ~ 八之八）。**这里只记结构和几条硬边界。**

### 心 / 手 / 脸是并列的，不是嵌套的

```text
心   Nox Core（VPS）        他是谁、他想做什么
手   Local Gateway（她电脑） 他能动的东西
脸   Caelum OS（Electron）   她看见的、她点头的地方
```

**🔴 手可以换，心不能换。** Gateway 是一个可替换的执行器 ——
今天用 DeepSeek Harness 的 cordis 内核，明天换别的也行。
Nox 的人格、记忆、判断永远在 Core 那边，绝不下沉到手里。

⚠️ **代码分在两个仓库里**，找的时候别只翻这个：

| | 在哪 | 是什么 |
|---|---|---|
| 心这边 | `nox-core/tools/local_link.py` + `computer.py` | 他怎么用这只手 |
| 手那边 | `D:\deepseek-harness\packages\caelum\local-gateway\` | 手本身（**不在本仓库**） |

`local-gateway/src/` 里：`grant` 授权范围 · `undo` 撤销 · `git-tools` ·
`browser-tools` · `image-tools` 看图 · `terminal-tools` 常驻终端 ·
`activity` 感知 · `approval` 审批 · `link` 反向连接。跑法见架构文档八之五。

⚠️ **看图这条 base64 绝不能进他的上下文**（一张图约 27 万字符，
而且会存进会话历史，之后每轮都带着）。闸在 `computer.py::_split_image`，
细节见架构文档八之九。

### 反向连接：是她的电脑去连 VPS

TCP 方向是 **PC → VPS**（她家里没有公网 IP，也不该开端口）。
但**MCP 的逻辑角色是反的**：Core 是 client，Gateway 是 server。
「谁连谁」和「谁指挥谁」在这里是分开的，看代码时别搞混。

握手是 HMAC 挑战应答，payload 按 **UTF-8 字节长度**前缀 ——
不是字符数，中文会对不上。

### 能力清单

| 能力 | 状态 | 边界 |
|---|---|---|
| 读写文件 / 搜索 | ✅ | 沙箱三档，见下 |
| 执行命令 | ✅ | **永远逐条问，不被授权覆盖** |
| 只读 git | ✅ | `status` / `diff` / `log`，**没有** commit/checkout/reset |
| 开网页读内容 | ✅ | 独立 profile，只 http(s)，**不点不填** |
| 看图 | ✅ | 工作区 + 她的投递口；**他看到的是转述，不是图** |
| 常驻终端 | ✅ | 五件（开/发/读/列/关）。**发命令每次问她**；没有 Ctrl+C |
| 看她在用什么 | ✅ | 前台窗口标题，可屏蔽名单 |
| 多步执行 | ✅ | Work Grant，见下 |
| 撤销一段工作 | ✅ | git 快照，只回滚碰过的文件 |

### 🔴 Work Grant：一次点头覆盖一整段

原来每一步都弹一次窗，改十个文件她要点十次 —— 这不是安全，是骚扰到她不看内容。

现在是**按范围授权**，不是按计划授权：

```text
路径范围（只能在这几个目录里）
步数上限（最多 40 步）
时间上限（最多 30 分钟）
```

三条任意一条到顶就作废，重新问。

**为什么不是「按计划授权」**：计划是他写的，他中途改主意计划就变了，
她点头的那个计划和实际跑的可能不是一回事。范围是客观的、可验证的。

⚠️ 糖糖明确定的一条：**命令仍然逐条问**。
Grant 覆盖读写和搜索，`run_command` 永远单独弹窗 ——
文件改错能撤，命令跑出去的副作用撤不回来。

`grant.ts` 是**整套设计里唯一能被绕开的地方**，所以：

- 路径边界必须带分隔符 —— `/a/bc` 不在 `/a/b` 范围内
- 多路径参数要求**全部**在范围内（不是任意一个）
- 范围里只要有一条越出工作区，**整批拒绝**，不做静默裁剪

### 撤销：只回滚他碰过的文件

用 git 快照（`GIT_INDEX_FILE` 指到临时索引，不动她的暂存区）。

**🔴 绝不整树回滚** —— 他在改代码的时候她可能正在改别的文件，
整树回滚会连她的改动一起抹掉。只回滚 `grant.touched` 里记着的路径。
部分成功要如实报 `ok=false`，不能假装干净。

界面上「停」和「撤销这段」在**工作进行中**就能点（`WorkBanner.jsx`），
不是只能事后补救。停不用确认，撤销确认一次。

### 浏览器：独立 profile，永远不碰她的 Edge

`~/.caelum/browser-profile`，headless，`channel: 'msedge'`。
只放行 `http:` / `https:`。

**点击和填表故意没做** —— 不是难，是审批界面还不对：
现在只能给她看 CSS 选择器，而她该看见的是「要点的那个按钮上写着什么」。

### 感知层：他看得见她在忙什么

前台窗口标题采样（PowerShell 探针 → base64 JSON），聚合成时间段。
接进 Resonance 做「她正忙」的判断（30.14）。

三个编码坑写在探针脚本头部，都踩过：

1. `.ps1` **必须存成带 BOM 的 UTF-8**，否则 PS 5.1 按 GBK 读
2. **不要**设 `[Console]::OutputEncoding` —— 管道里输出会整个消失
3. 必须用 `-File`，不能 `-Command -` —— here-string 走 stdin 解析不了

`~/.caelum/activity-ignore` **每次都重读，绝不缓存** ——
她想立刻屏蔽掉某个窗口时，不该还要重启一遍。

### 踩过的坑（都是「静默」型）

**cordis 的 `inject` 是执行期才验的。**
手搓一个 `ctx` 去测，等于绕开了它 —— 80 个测试全绿，线上一写文件全挂
（`cannot get property "approval" without inject`）。
现在有 `tests/plugin.spec.ts`，规矩是**不许手搓 ctx，只许 `ctx.plugin(Gateway)`**。

**cordis 的日志等级是反的**（`ERROR=0 < INFO=1 < WARN=2`），
warn 比 info 更容易被吞。排查前先确认日志打得出来 ——
那次我自己的 grep 还顺手把真因滤掉了。

**`pkill -f` 在 Windows 上杀不掉 tsx。** 六个 Gateway 同时活着抢连接，
两分钟顶掉 52 次。要用 `Get-CimInstance` 匹配命令行。

**审批超时 20 秒，而弹窗给她 60 秒。** 她根本来不及点。改成 75 秒。

**`sessions.create()` 写在 `try` 外面** —— 它一抛异常，
`this.current` 就永远留着，手从此显示「忙」，再也接不了活。

**测试断言断错了边**：第一版验 `cwd` 传没传，断言的是
「我写进 header 的值」—— 那只证明我写了配置，没证明工具收到了。
重写成从**假工具内部**读 `exec.agent.session.header.cwd`。
（同 `verify-from-the-consumer-side`）

### 还没做

- Ctrl+C：沙箱挡着，conpty 传不进前台进程组，三种写法实测全废。
  现在停跑飞的进程只能关掉整个 shell（架构文档八之十）
- harness 还现成躺着 `bash`（能力和 pwsh 重叠，不接）、
  `ask_user_question`（他干到一半反问她）
- 浏览器点击 / 填表（等审批界面能显示按钮文字）
- Chrome/Edge 扩展拿精确域名（现在窗口标题够用）
- 躁动 Drive（需要「他有话想说」×「她正忙」两个信号叉乘）

---

*2026-08-20：**经期的读路径也切到 World Model 了**（32.1）。08-19 只切了写，
读还留在那张停更的老表上 —— 后果不是报错，是「她在 App 里记的东西他看不见」，
两边那天碰巧一样所以没露馅。这次不靠测试全绿交差：写了个只读脚本走**和服务
同一段装配代码**在线上验真接线，当场抓到一个只有一次记录时会渲染成
`【经期｜周期第 7 天｜平均 ｜预计下次 （ 后）】` 的空壳（814 passed）。
经期**还没做成 Attention Source**，她说先观察。*

*2026-08-20：**Caelum OS 的 Life 一栏盖起来五间**（Diary / Books / Todo / Health / Music，
第三十八节）。一个新端点都没加，全读 bridge 上已经在跑的。接线时撞到三处两边对不上的：
日记里他的署名是「小克」不是「Nox」（照名字判会让他的批注顶着糖糖的头像）、
`/api/health/latest` 里那个 `menstrual` 字段来自停更的老表（新页面只信 World Model）、
`<audio>` 带不了 header 所以每首歌要换一张签名票。新增 `lib/player.js`：
整个 OS 只有一个 `<audio>`，Music 页的播放条和右栏那张小卡片是它的两张脸。*

*2026-08-19：**账本第一天就自己开口了**。上线后第一个完整的早上，13 次惦记里
6 次撞在「一小时一条链」上、全卡在 43~59 分钟 —— 糖糖看过说不烦，当天去掉那条闸，
栏杆交还给 20~90 分钟的随机窗口（35.6）。前端的「能量值 / 今天主动开口 1/3」
跟着换成账本真数字（那两个数只统计得到睡眠+时间两条线，少算一大半）。
修了一个静默 bug：`CareLedger.summary()` 里调 `_roll()`，跨过零点之后
**任何一次读都会把账本清空**（35.7 第 4 条）。*

*2026-08-18：**主动关心全线收口 + 「Nox 的一天」**。第三十五节补齐 35.4~35.7：
Care 快循环（60s，和 900s 心跳并排）、惦记引擎（20~90 分钟随机、秒级不取整）、
出门追问（HA 位置一直是自动的，缺的是「有人盯着跃迁」）、CareLedger
（记决策不记开口，considered = spoke + skipped + blocked）。
新增第三十六节「Nox 的一天」（只读投影，八个源全接通，含给 OB 加的 `GET /recent`）
和第三十七节（bridge 终于有 24 个测试，并用变异测试验过它真能抓 bug）。
Caelum OS 菜单改版成七个一级项（Todo 归 Life、Tasks 是他的事）。
另出一份 `CAELUM-ARCHITECTURE.md`：九个端口全部认领、没有僵尸服务。*

*2026-08-17：**Caelum OS 桌面界面重做 + 装成桌面应用**。新增第三十四节：
按三张设计稿做了 `nox-app/caelum-os-ui/`（React + Tailwind，三主题，手画 SVG 房间 +
真图优先），Home 与 Chat 两页接真数据并立下「失败就说失败」的规矩；
Electron 壳改无边框、生产模式自托管 dist 并在主进程转发 /api（绕开 file:// 与 CORS）、
生成 7 档图标 + 桌面快捷方式；**他能主动出现了** —— pending / wakeups 一冒头就弹通知、
收托盘常驻、单实例、去重靠 intent id 与 wakeup 的 why+wake_at。记了两个静默 bug 的根因
（`setState` 更新函数里读 ref 导致分段错位、输入卡顿的三层原因）
和一个诡异的注释吞代码事故。同时修正了目录结构里 frontend 子树被错挂在 desktop 下的缩进。*

*2026-08-16：**Caelum OS 阶段 0+1+2+3 落地**。阶段 0 立插件化骨架（`caelum-os/` 三层盘点 +
补丁重放脚本），阶段 1 做工作台状态 `GET /api/nox/state`（Core + bridge 代理 + 前端状态灯），
部署上线并 curl 验证；自主调度发现 M5′ a 已做、干活能力推迟阶段 2；**阶段 2 fork 完成**——
`Iristt-boop/Caelum-OS` 装上 deepseek-harness 完整历史（12293 commit）+ `caelum-os/` 目录，
分支策略见 `caelum-os/FORK-分支策略.md`；**阶段 3 桌面端第一版**——Electron 单窗口壳 +
72px Tab 栏 + 粉糯主题 + Home widget 墙（全接真实数据 + 可拖拽持久化）+ Chat 工具抽屉占位，
本地 dev 未部署线上。新增第三十三节。*

*2026-08-13：**共感娃娃触摸感知上线**。新增第三十一节：ESP32 + FSR402×5 边沿检测记时长、
VPS `touch-server`(9333) 接收、`touch-mcp`(9336) 提供「查触摸记录/汇总」两个工具、
Caddy `/touch/<token>/mcp` 门禁（24 字节 hex，同 ombre/ha-mcp 的做法）、挂 claude.ai。
词表新增「Nox 的触觉」，目录新增 `fsr402-test/` 与 `touch-mcp/`。*

*最后更新：2026-08-08。**全面校准**：按本地代码 + 线上 `ssh` 实测逐项核对，
新增第二十八节记全部偏差。主要修正：工具清单 36→**59**、补上整个饮食子系统、
Daily Planner 从「在做」改为「已上线」、VAPID 从「不存在」改为「通了但私钥在源码里」、
前端页面表按 `App.jsx` 实际挂载重写并标出 5 个死代码文件、家居设备 8→10、
老机已退所以第二十五节的回滚方案作废。新查出 `eryu_search` 线上必失败（参数名 `keyword` vs `q`）。*

*2026-08-11：**Attention M4 上线 —— 他第一次会自己开口了**，当晚又加上
**唤醒链**（他给自己留纸条、到点自己醒来判断要不要说话，糖糖设计的，dry-run）。
新增第三十节，含两个「只有真跑才发现」的静默 bug。
同日修了三份架构文档的 Bus 漂移（源头是 Attention 正文自己没扫干净）。*

*2026-08-09：**共听全线修通 + OB 打通向量检索**。
共听那边一路修了 11 个 bug（第二十八节「eryu 四连坑」「三个音乐库」
「netease 少了 /mcp」），并做出了聊天里的音乐卡片（DESIGN.md 二·02 消息类型 4）。
新增第二十九节记 Ombre Brain：三方仓库对比（**结论是都不换**）、
**向量检索从上线起就没工作过**（`enabled: false` + Gemini 项目被 403，
已换通义 `text-embedding-v4`，203 条全部补齐）、
21 条隐形记忆重打标、Dream 选材（D1）交付、OB 上了 git。
完整方案见 `D:\WorkBuddy\Nox-OB-优化方案.md`。*

## 四十、主题真源统一 + Todo/Music 双端重构（2026-08-27/28 上线）

### 40.1 主题真源：两 App 共用一份（`nox-app/shared/theme/`）

- `palettes.css`：全部主题的 `[data-theme="…"]` 色板（唯一改颜色的地方）；
  `meta.mjs`：名字/色块/深浅/底色（`bgBase`，iOS 状态栏和开屏用）。
- 切主题 = 根元素设 `data-theme` 属性（OS 原本就这样，App 从「JS 派生 20 个内联变量」改了过来）。
- 现存 **5 套**：warm（根）+ sunset/deepspace/rainbow/dreamy；2026-08-27 删掉旧 7 套
  （粉糯梦境/海盐葡萄/薄荷曼波/椰风海岛/青提芭乐/美式复古/暗夜），失效存档在初始化处回落。
- ⚠️ **别名桥**在 palettes.css 尾部：旧 `--color-*` 名到新令牌的过渡引用，**页面全部迁完后整段删**。
- ⚠️ **双写点**：`frontend/index.html` 的开屏 BG 表是 meta.mjs `bgBase` 的手抄副本
  （iOS 在 React 起来前就画完状态栏），加主题两处都要改。
- 桌面默认 rainbow、手机默认 warm（原桌面默认粉糯已删）。

### 40.2 Todo 双端（App 页面名从 Today 改成 **Todo**）

- App：标题下**圆形周历**滑块；`frontend/src/lib/schedule.js` 纯函数把 repeat 推导到任意一天
  （once/daily/weekly 落日，weekly_count 是周配额不落日）；**非今天只读预览不能勾**
  （完成史只有今天粒度，页面上不说假话）；条目带时刻/备注/循环标注/分类标签；长按删除断认。
- OS：dashboard 十模块——接下来倒数卡、今天、时间轴（紧凑窗口只画有事的时段）、
  循环健康度（Ring 配额环 + 周点阵）、他的喋喋（`chasedToday`）、今日完成墙、完成热力（events `?range=N`）、
  月视图日历（任务条直摆、360px 定高内部滚动）。
- bridge 数据层：todos 幂等加 `note`/`tag` 列并透传；`chasedToday`（fired_on 推导）；
  **勾掉循环任务会清 fired_on**（做完了就不追了，喋喋模块因此消失是正确行为）。

### 40.3 Music 双端

- **歌单封面链路**（此前「歌名搜 eryu 补图」土办法已退役）：netease-mcp 三个工具
  （list_my_playlists / get_playlist_songs / get_play_history）文本尾部加 ` | <封面URL>` 段，
  bridge 正则解析 + http→https 化；**正则名字段用贪婪匹配吃到最后一个 " - "**，
  不然 Merry-Go-Round 会被从连字符劈开。新增 `/api/music/netease/history`（本周最听）。
- App（`07074e5`）：「和 Nox 听过」共听英雄区置顶（双头像读 localStorage `nox-avatar-*`、
  真实次数、最常一首大卡）→ 每日推荐 → 最近听的 → 歌单 2 列大图。
- OS（`12a3842` 设计稿版）：大播放 Hero（封面全幅 + 频谱条纯装饰 + 进度条与音浪**同宽 129px** +
  控三键）→ 今晚听什么 → 我的歌单大图横滑 → 和 Nox 听过压缩横滑；右列 **col-span-3 缩窄**、
  正在播放竖排、最近播放（相对时间）、歌单内的歌。Hi-Res/场景标签/推荐语/歌词/随机循环音量
  **无数据链一律不放**。
- 踩过：主列纵向 flex 会把自然高度面板**压瘪**（overflow-hidden 的盒子 min-height:auto 失效），
  自然面板一律 `shrink-0`，滚动归主列。

### 40.4 页面级主题背景（`usePageArt`）

- 约定：`caelum-os-ui/public/<页面>/<主题别名或id>.<ext>`，一张通用放 `<页面>.<ext>`。
  主页 `room/`（已有四张）、Music `music/`（她放了 deep space.png / rainbow.png）、
  Diary 以后 `diary/` —— 丢图进目录即被 `usePageArt` 探到，**零代码**。
- ⚠️ 桌面应用跑的是 **`dist/`**：`public/` 丢图后要 `npm run build` 才会拷进去
  （或同时手动拷一份进 `dist/<页面>/`）。
- Music Hero 取图优先级：主题背景图 → 当前歌封面 → 主题渐变。

### 40.5 版本管理与回滚

- **nox-app 2026-08-27 才收进 git**（`f3dd128` 基线：此前 themes.js/pages/OS 全部未跟踪，
  无法回滚），此后每阶段独立 commit。
- 回滚点：`/root/bridge/server.js.bak-20260827`、`bak2-20260827`、
  `/root/netease-music-mcp/server/mcp-server/server.py.bak-20260827`、`/root/frontend/dist-old`。
