# Caelum OS 资产盘点（阶段 0 产出）

> 日期：2026-08-15
> 配套 `CAELUM-OS-ROADMAP.md`（讲怎么走）与 `CAELUM-OS-ARCHITECTURE.md`（讲长什么样）。
> 本文是阶段 0 的落地物：把现有资产按「三层边界」归类，并给出内核补丁的重放方式。

## 三层边界（回顾，来自 ROADMAP 〇节）

| 层 | 里面是什么 | 官方大改时 |
|---|---|---|
| 配置层 | 人格、记忆、Skill、插件清单、世界观 | 基本不动 |
| 插件层 | 自研 MCP / 工具 / UI 插件 | 小适配（API 微调） |
| 内核补丁层 | 直接改内核文件的地方（spawn.ts 等） | 要重放（脚本一键铺回） |

> 规矩：**尽量把「我们的一切」放在配置层和插件层，内核只留最小脚印。**

---

## 一、内核补丁层（4 个文件，1 个主题）

**主题：隐藏 Windows 子进程控制台窗口（修「pwsh 闪黑框」）**
根因：host DSH 进程自己没有控制台，导致每个 spawn 出来的子进程（pwsh 等）都会
在桌面闪一个黑框。修法是 `STARTF_USESHOWWINDOW + SW_HIDE`（不用 `CREATE_NO_WINDOW`，
后者在该限制令牌方案下会让子进程死于 `STATUS_DLL_INIT_FAILED 0xC0000142`）。

| 文件 | 改动 |
|---|---|
| `packages/sandbox/sandbox-windows-acl/src/ffi.ts` | `StartupInfoInput` 加 `wShowWindow` 字段 |
| `packages/sandbox/sandbox-windows-acl/src/spawn.ts` | 两处 spawn 加 `STARTF_USESHOWWINDOW \| SW_HIDE` |
| `packages/sandbox/sandbox-windows-acl/src/win32-abi.ts` | 新增 `STARTF_USESHOWWINDOW`、`SW_HIDE` 常量 |
| `packages/subprocess/subprocess-local/src/spawn.ts` | `taskkill` 与 `spawnSubprocess` 加 `windowsHide` |

**管理方式**：
- 补丁快照：`caelum-os/patches/0001-hide-windows-child-console.patch`
- 重放脚本：`caelum-os/apply-patches.ps1`（幂等，已应用自动跳过）

> ⚠️ 这 4 个文件在 ROADMAP 里被记成「1 个补丁：spawn.ts 弹窗修复」——不准确，
> 实际是一组 4 文件、跨两个包（sandbox-windows-acl + subprocess-local）的同一主题改动。

---

## 二、配置层（基本不动）

| 资产 | 位置 | 说明 |
|---|---|---|
| 人格 | `nox-core/personality/prompt.py` | 人设 + 静态前缀组装（缓存前缀，不可变） |
| 世界观 | `Iristt-boop/Claude` 仓库 `nox-docs/Caelum-世界观.md` | 唯一真源；工程映射见 `PROJECT.md` 〇节 |
| 记忆筛选/写入门槛 | `nox-core/memory/`（ob_client.py、tools.py） | 「存什么、筛什么」的规则是 Nox 层决策 |
| 插件清单 | `.mcp.json`、内核 `cordis.patch.yml` 里的插件替换 | 含 VS Code 布局插件替换（见下） |

**配置层里的一处「插件清单」改动（非内核补丁）**：
`packages/bundle/web-app/cordis.patch.yml` 把 `ui-layout` 从
`@deepseek-ai/dsh-client-ui-layout` 换成 `@anoslide/dsh-client-vscode-layout`（VS Code 布局）。
这属于「换哪个 UI 插件」的清单配置，不是内核逻辑，因此**不进补丁重放脚本**，随配置层管理。

---

## 三、插件层（小适配）

### 3.1 Nox Core（`nox-core/`，Python + FastAPI，:8100）
| 模块 | 说明 |
|---|---|
| `agent/` | llm.py（中性接口）+ adapters.py + guard.py（不许编）+ loop.py（六种结局） |
| `router/` | intent.py（规则分类）+ router.py（轻量/完整双路径，决定加载哪些 Provider） |
| `context/` | Context Engine：base.py + cache.py + registry.py + providers/（8 个 Provider） |
| `memory/` | Ombre Brain 客户端（ob_client.py）+ 记忆工具（tools.py） |
| `tools/` | 59 个工具（mcp_client/http/ha/daily/intimate/diet/planner/todo/github_obsidian/reading/eryu/netease/notion/tracker/misc） |
| `personality/` | 人设（见配置层） |
| `api/` | FastAPI 会话管理 + 结局翻译 |

### 3.2 感知 Provider（`nox-core/context/providers/`）
`mood` / `time` / `memory` / `home` / `health` / `weather` / `todo` / `location`

### 3.3 MCP 连接器（连外部世界）
| 服务 | 端口 | 说明 |
|---|---|---|
| `Ombre-Brain`（ombre-brain） | 8002 | 记忆系统（关系记忆） |
| `ha-mcp` | 8004 | Home Assistant 家居 |
| `health-mcp` | 8101 | 健康数据（streamable-http） |
| `health-sync` | 8102 | 健康数据接收 |
| `app-tracker` | 8000 | App 使用追踪 |
| `toy-mcp` | 8003 | 玩具控制 |
| `netease-music-mcp` | 3456 | 共听账号层 |
| `touch-mcp` | 9336 | 共感娃娃触摸记录 |
| `stackchan-mcp` | — | Stack-chan 身体（gateway + firmware） |

### 3.4 入口 / 前端
| 模块 | 说明 |
|---|---|
| `bridge/`（:3003） | App 后端：静态托管 / TTS / STT / 共读代理 / SSE / WS |
| `nox-app/frontend/` | Caelum App（React 19 + Vite 8，PWA） |
| `co-reading` / `eryu` | 共读 / 共听 |

### 3.5 身体 / 触觉
`stackchan-mcp/`（ESP32 固件 + 网关）、`fsr402-*`（共感娃娃固件）、`touch-mcp`

---

## 四、阶段 0 出口验收

- [x] 内核补丁盘点完成（4 文件 / 1 主题 + 1 配置改动）
- [x] 补丁快照导出并 `git apply --check --reverse` 验证一致
- [ ] 重放脚本 `apply-patches.ps1` 可运行（幂等验证）
- [ ] 三层资产归类文档（本文）

> 结论与架构文档一致：**80% 的东西已经在了**。内核补丁层只有一组改动，
> 且已沉淀成可重放的 patch。真正的大头是插件层（Nox Core + MCP + 前端），
> 它们已经在跑，只待「收编进 Nox 层」这一步（属阶段 1，不在阶段 0 范围）。
