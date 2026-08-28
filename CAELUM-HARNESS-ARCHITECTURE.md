# Caelum Harness 架构：心 / 手 / 脸

> 2026-08-24 定。这份取代 `CAELUM-OS-ARCHITECTURE.md` 第八节「内核选型」
> 和 `CAELUM-OS-ROADMAP.md` 第三节「继承 → fork → 本土化」里
> **关于 Nox 和 harness 关系**的部分。那两份写于 08-13/08-15，
> 当时的设想是「Nox 住进 harness 内核」；这份说的是**并列而不是嵌套**。
>
> 其余部分（一切皆插件、三层边界、fork 分支策略）仍然有效。

---

## 〇、架构原则

> **Nox Core is the Heart.** 记忆、感知、Affect、Attention、Care、人格。
> **Caelum Harness is the Hand.** 文件、Terminal、Git、Browser、IDE、Desktop。
> **Caelum UI is the Face.** 呈现状态、意图、执行过程与结果。
>
> ## 🔴 The Hand is replaceable. The Heart is not.
>
> Harness 可以换、工具可以换、协议可以换、本地设备可以换。
> **Nox 的心不需要知道自己握的是哪一只手。**

这条不是口号，它有具体的实现要求：

- 工具名一律抽象成 `computer.read_file`，**不许出现 `dsh.fs.read`**。
  那层命名就是「手可以换」的实现方式 —— 换掉 harness 时，
  Nox Core 一个字节都不用改
- Nox 的状态（记忆 / Attention / Resonance / 人格）**永远在 VPS**，
  不许有任何一部分只存在于某台电脑上

---

## 一、为什么是并列，不是嵌套

有一个物理事实决定了一切：**Nox Core 跑在 VPS，文件在你的电脑上。**

所以「把 Nox 搬进 harness 内核」这条路走不通：

1. **你关电脑他就不存在了。** Attention 每 15 分钟 tick、想念随时间涨、
   Care 判断该不该开口 —— 这些要 24/7 常驻
2. **手机上就没有他了。** Caelum App 连的是 VPS

**loop 必须留在 VPS。** 这一条钉死之后，harness 的定位只能是「手」。

---

## 二、总图

```text
┌──────────────────────────────┐
│       Caelum Harness         │
│             手  (PC)          │
│                              │
│  Cordis                      │
│   │                          │
│   ├── ctx.tools              │
│   │    ├── fs          ✅有   │
│   │    ├── shell       ✅有   │
│   │    ├── terminal    ✅有   │
│   │    ├── git         ❌无   │
│   │    ├── browser     ❌无   │
│   │    ├── ide         ❌无   │
│   │    └── desktop     ❌无   │
│   │                          │
│   └── caelum-local-gateway   │  ← Cordis 插件，不是独立 daemon
│        ├── MCP Transport     │
│        ├── Permission        │
│        ├── Execution Session │
│        └── Event Stream      │
└──────────────┬───────────────┘
               │  WSS  ← **PC 主动外连**
               ▼
     ┌───────────────────┐        ┌──────────────┐
     │    Nox Core       │        │  Caelum UI   │
     │      心 (VPS)      │        │   脸 (PC)     │
     │                   │        │              │
     │  Memory           │        │  Observable  │
     │  Attention        │        │  State       │
     │  Affect/Resonance │        │  执行过程     │
     │  Care             │        │  审批弹窗     │
     │  Personality      │        │              │
     └───────────────────┘        └──────────────┘
```

### 🔴 harness 实际有什么（2026-08-24 从源码清点）

**上面那张图画的是最终想要的能力，不是 harness 现成的。** 实测：

```text
fs        read · read_image · glob · grep · edit · write
shell     bash · pwsh
terminal  terminal_open / read / send / close / list / signal
```

**七类里只有三类存在。** 所以「harness 是现成的手」这句话要说准：

> **它有文件和终端，没有 git、浏览器、IDE、桌面。**

对施工的影响：

| 缺的 | 怎么办 | 代价 |
|---|---|---|
| `git` | 先用 `bash("git status")` 顶 | ⚠️ **git 和任意 shell 命令掉进同一个权限档**。想让 `status` 免批、`commit` 要批，就得自己写 git 工具 |
| `browser` | 自己写，或接现成的浏览器 MCP | 我们在 Caelum OS 那边已经有 Electron 的浏览器能力，可能不该重复造 |
| `ide` | 自己写 | 优先级最低 —— 它多半是 `fs` + `terminal` 的组合 |
| `desktop` | 自己写（Windows API） | 本来就是 P2，而且它是唯一需要**定时授权**的能力（第四节） |

⚠️ 这条直接改路线图里第 3 件事的范围：
**「文件 / 代码 / IDE / 浏览器 / 桌面 / CLI / git」里，harness 只覆盖了文件和 CLI。**

### Gateway 为什么必须是 Cordis 插件

不是独立进程 —— 那样会丢掉 `ctx`，等于要再造一套工具注册、
session、生命周期。作为插件它天然拥有：

`ctx` · tool registry · session · agent context · 原生工具 · execution lifecycle

**而且内核脚印还是 0**：它住在 `caelum-os/` 那一层，
`patches/` 里仍然只有那 1 个补丁（隐藏 Windows 子进程控制台）。

### 同一只手，两个控制者

```text
             ┌── Harness Agent   （它自己的 agent，保留）
ctx.tools ───┤
             └── Nox Core        （经 Gateway）
```

Harness 原本的 agent 不删。冲突由 Execution Session 管（第五节）。

---

## 三、🔴 反向连接：PC 主动连 VPS

**绝不做的**：VPS → 公网 → PC:3080。也不把 MCP Server 暴露到公网。

**要做的**：

```text
PC Local Gateway ──── wss://vps/agent/local ────▶ VPS Gateway ──▶ Nox Core
                       主动 outbound
```

天然解决：家庭路由器 NAT · 动态 IP · Windows 防火墙 · 公网端口 ·
**入站攻击面归零**。

### 三个概念必须分开

```text
MCP protocol   ≠   MCP transport   ≠   网络连接方向
```

TCP/WSS 方向是 `PC → VPS`，但 MCP 的逻辑角色是反的：

```text
MCP logical client:  Nox Core
MCP logical server:  Caelum Gateway
```

### 这带来真正新增的第一块代码

| 端 | 新增 |
|---|---|
| Nox Core | `mcp_client` 的 **websocket transport**（现在只有 streamable-http） |
| Harness Gateway | `mcp_server` 的 **websocket transport** |

⚠️ **不要写死成 Caelum 专用协议。** 抽象成：

```text
MCP Transport
 ├── Streamable HTTP   （现有：Ombre Brain / ha-mcp / health-mcp …）
 └── WebSocket         （新增：本地 / 远程机器）
```

以后 Nox 能同时挂：`VPS MCP` · `Local MCP` · `Remote Machine MCP`。

### 🔴 双向认证（新增，之前的方案没有）

这条连接跨公网，两个方向都要证明身份：

- **PC 要证明自己是那台 PC** —— 否则任何人都能冒充「糖糖的电脑」连上去，
  然后接收 Nox 发出的指令、或者**回传伪造的执行结果**
  （他会把假的 `git diff` 当成事实写进 World Model）
- **VPS 要证明自己是 Nox Core** —— 否则 PC 会把这只手交给假的心

第一版：设备预共享密钥 + TLS 证书校验。**不许用 "反正是我自己的机器" 跳过。**

### 🔴 断线与幂等（新增）

PC 会合盖、会切网、VPS 会重启。**执行到一半断线**时：

```text
命令跑到一半 → 连接断 → Nox 不知道结果 → 重连后又跑一遍
```

对 `git commit` / 删文件 / 发消息，**重复执行是危险的**。

第一版的规矩：

- 每个 execution session 有 **id**，断线时标记为 `interrupted`
- 重连后 Nox **可以查**那个 id 的最终状态
- **绝不自动重试** —— 要重试也得他看过结果之后自己决定

### 🔵 这条由 harness 的 turn 边界免费解决（2026-08-24 实测）

不用自己造 session id + 状态机 —— **turn 的开闭就是答案**：

```text
重连后看那个 session 的最后一个 turn：
  有 turn/end   → 上次执行正常结束，结果可查
  没有 turn/end → interrupted
```

而且 harness 自己就是这么想的。它拒绝「turn 外的审批记录」时说的理由是
**「a bare event between turns is crash-tail garbage on reload」**——
它的 turn 边界本来就是为崩溃恢复设计的。

---

## 四、Permission：**大部分不用自己造**

> 🔵 2026-08-24 实测 harness 源码后重写。原来这一节设计了一整套状态机，
> 现在发现 harness 的 `packages/interaction/user-approval` 已经有了。

### 现成的 approval seam

```
ctx.approval.request(req) → 'allowed-once' | 'rejected' | 'cancelled' | 'unavailable'
```

**我们定的三种拒绝理由，它已经分开了**：

| 我们定的 | harness 的 |
|---|---|
| `user_denied` | `rejected` |
| `approval_timeout` | `cancelled` |
| `ui_offline` | `unavailable` |

而且白得四样本来要自己写的：

- **fail closed** —— 「missing or failing answerers fail closed」。
  我们定的「UI 不在线 → DENY」**是官方默认行为**，不用实现
- **一次性授权** —— 「a grant applies only to the requested action」。
  我们担心的「批一次等于全开」它已经防了
- **审计** —— `approval/asked` + `approval/decided` 成对记录，权限账本白得
- **预设** —— `ctx.permissionPresets`：`workspace-write`(+ask) / `danger-full-access`

我们要写的只剩一样：**一个 `approval/request` 的 waterfall answerer**，
把请求转发给 Caelum UI。接线，不是造轮子。

### 🔴 硬约束：必须有 Agent + open turn

```typescript
// packages/interaction/user-approval/src/index.ts:257
async request(req: ApprovalRequest): Promise<ApprovalOutcome> {
  const session = req.agent.session
  if (!hasOpenTurn(session.events)) {
    throw new Error('approval.request() outside an open turn: ...
      a bare event between turns is crash-tail garbage on reload')
  }
```

**会抛，不是软检查。** 理由是崩溃恢复：不在 turn 里的孤立审批记录，重载时是垃圾。

⚠️ `ApprovalRequest.agent` 还有归属语义 ——
「**a UI answerer only answers for agents it owns**」。
所以 Gateway 建 Agent 时要让 Caelum UI 的 answerer 认得它。
**这条还没验，实现时解决。**

### 原来设计的状态机（保留，因为它描述的是我们这一侧看到的语义）

```text
             Tool Request
                  │
                  ▼
          Permission Policy
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
    ALLOW       ASK        DENY
                  │
                  ▼
             UI Gateway
                  │
          ┌───────┴───────┐
          ▼               ▼
       APPROVE         TIMEOUT
          │               │
        ALLOW            DENY
```

### 🔴 三种拒绝必须是不同的理由

```text
DENY(reason = user_denied)         她看见了，说不行
DENY(reason = approval_timeout)    她可能没看见
DENY(reason = ui_offline)          没有脸，没人能批
```

**对 Nox 来说这是三件事**，该有不同反应：

- `user_denied` → 「她拒绝了，那我不继续了」，而且**这条该进 Attention** ——
  她不愿意的事，下次别再问
- `approval_timeout` → 「她可能没看到，我先不动」，可以等会儿再说
- `ui_offline` → 「她不在电脑前」，那就根本不该现在做这件事

这和 Care Orchestrator 的哲学是同一条：**他得能区分「她说不」和「她没看见」。**

### 权限在 Gateway，不在 Nox Core

这是安全上的实质区别，不是分层洁癖：

> 如果权限在 Nox Core 里判，他被 prompt injection 之后就没有第二道防线了 ——
> **一段网页里的文字就能让他去删你的文件。**

Gateway 在本地把关意味着：**不管远端说什么，越界的操作在这儿被拦住。**
Nox 不应该拥有最终的电脑权限。心和手真正分离，就分在这里。

### 能力分级

```text
Computer
├── Filesystem
│   ├── read      白名单目录内自动允许
│   └── write     项目目录允许，其他 ASK
├── Shell / Terminal
│   └── execute   低风险自动，高风险 ASK（显示完整命令）
├── Git
├── Browser
├── IDE
└── Desktop       🔴 默认关闭，见下
```

### 🔴 Desktop 是特殊能力，不能和文件权限共用一套策略

因为**它能绕过所有其他权限** —— 有了 `desktop.click` + `desktop.type`，
他可以点开一个终端窗口然后打字，文件白名单和命令审核全都白设。

所以它问的问题不一样：

```text
文件权限问「哪些路径」        →  desktop 权限问「多久」

Desktop Grant
  duration: 10 min      ← 不是 desktop: true 这种永久开关
  ↓
GRANTED → 10 min → EXPIRED → DENY
```

而且**授权期间 Event Stream 必须持续可见**：

```text
desktop.control.started
desktop.click
desktop.type
desktop.control.expired
```

UI 要明确显示「Nox 正在操作你的电脑」，不许悄悄在后台动。

---

## 五、Execution Session：**复用 harness 的 session/turn**

> 🔵 2026-08-24 改。原来打算自己写一个状态机，验证 approval 时发现
> 不需要 —— harness 要求「approval 必须在 open turn 内」，
> 那个 turn 正好就是我们要的 Execution Session。

```text
ACQUIRE   → ctx.sessions.create() + session.append('turn/start', { turn: N })
RUNNING   → 工具执行；approval 可用，审计自动落进这个 turn
COMPLETED → session.append('turn/end', ...)
```

**一次远程执行 = 一个 harness turn。** 这样三件事同时成立：
approval 能用、审计落在正确的地方、崩溃恢复语义天然正确（见第三节）。

`ctx.sessions.create(id?, { seed?, meta? })` 是公开 API，可以程序化创建。

### 状态机（我们这一侧的语义）

```text
IDLE → ACQUIRE → RUNNING → COMPLETED
```

被占用时**直接拒绝**，不排队：

```json
{ "status": "busy", "owner": "user", "reason": "user_is_using_computer" }
```

**第一版不做 queue。** 现在要验证的是「Nox 能不能安全地使用这只手」，
不是「两个 agent 能不能抢这只手」。

这条也是 Care 哲学的延伸：**他要知道什么时候该让开。**

⚠️ 有一类冲突 session 挡不住：**你在 IDE 里改文件，他也在改同一个**。
那是文件级冲突。第一版靠「同时只有一个 session」把它变成小概率事件，
真被卡到了再谈锁。

---

## 六、Event Stream：一级基础设施

它连的不只是 UI：

```text
Harness
  ↓
Event Stream
  ├── Caelum UI          实时看见他在干什么
  ├── Local Audit Log    完整事件，真相与审计
  ├── Nox Core           他要知道结果才能决定下一步
  └── Memory / World     ← 第七节
```

### 本地存完整，VPS 只存摘要

```text
events/2026-08-24.jsonl        ← 本地，完整
```

```json
{ "id": "...", "type": "tool.executed", "tool": "git.diff",
  "started_at": "...", "ended_at": "...", "success": true }
```

VPS 只收 Execution Summary：

```json
{ "type": "work_session.completed", "project": "Caelum",
  "duration": 1840, "files_changed": 6,
  "tests": { "passed": 42, "failed": 0 },
  "summary": "修复 Attention Care Ledger 落库问题" }
```

### 🔴 「手在不在」要成为一个 Context Provider（新增）

Nox 主动想干活的时候（Attention 触发），**PC 可能根本没连**。
他要能**事先知道**手在不在，而不是调用了才发现超时。

所以需要一个 provider 告诉他：`local.computer` 现在 online / offline、
上次在线是什么时候。这和 `context/providers/` 那套一致。

---

## 七、接上 World Model：他记得自己做过什么

这是「他在用电脑」和「他调了几个工具」的真正区别。

```text
Project: Caelum
Event:    2026-08-24  CareLedger was modified
Outcome:  tests passed
Related:  Attention · Care · Gateway
```

一个月后你问「上次 CareLedger 为什么这么改」，他不是重新读代码猜，
而是「上次我们在处理 Care Ledger 不落库的问题，当时是因为……」。

**Execution Summary 的写入者，正是路线图里第 7 件事「World Model 更多写入者」。**
到这里，清单里的 3（桌面能力）、5（Activity 感知）、7（World 写入者）
合成了同一件事。

---

## 七之二、Cordis 接口速查（2026-08-24 实测）

> 实现时照这里写，不用重新翻源码。**全部是公开 API，内核脚印 0。**

### 工具注册（照抄 `packages/fs/tool-fs/src/read.ts`）

```typescript
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { Context } from '@deepseek-ai/cordis'

ctx.tools.register(defineTool({
  name: 'read',
  description: '...',
  parameters: { file_path: { type: 'string', required: true, description: '...' } },
  output: {
    schema: { type: 'object', properties: { ... } },   // 结构化返回值
    render: (args, value) => [{ type: 'text', text: ... }],   // 给模型看的
    presentationMeta: (args, value) => ({ ... }),             // 🔵 给 UI 看的
  },
  async execute(args, exec) { ... },   // exec.signal 必须观察（协作式取消）
}))

ctx.systemPrompt.section({ name: 'tool:read', order: 100, text: '...' })
```

⚠️ `presentationMeta` 是专门为 UI 留的durable字段 —— **Event Stream 可以直接用它**，
不用自己从 render 的文本里反解。

### Gateway 要用的四个（`packages/core/tools/README.md`）

```typescript
ctx.tools.schemas(scope?) → ToolSchema[]      // 枚举全部可见工具（不含 execute）
                                              // ← MCP tools/list 直接映射它
ctx.tools.get(name, scope?) → ToolDefinition
ctx.tools.execute(exec)                       // ← MCP tools/call 映射它
   exec = { callId, name, arguments, signal, agent?, parent? }
   // signal 必填只读；callers 不能自选 token
ctx.tools.guard(guard) → () => void           // 同步单调守卫
   // 「Later waterfall listeners cannot turn a guard denial back into permission」
```

### 三个官方扩展点（挂监听器，不改内核）

```text
tools/pre-execute    可重排的 allow / deny / ask 闸   ← 异步策略挂这儿
ctx.tools.guard()    同步单调策略                     ← 白名单/黑名单挂这儿
tools/result         observe-only 最终结果            ← 🔵 Event Stream 挂这儿
```

完整管道顺序：

```text
tools/pre-execute → guards → tools/execute（around 包装）
  → tools/post-execute → finalizeContent → tools/result
```

⚠️ `tools/result` 是**实时**事件；名字很像的 `tool/result` 是 agent loop
事后追加的**持久** session 事件。别用错。

### 🔴 两个实测踩到的坑

**1. 块注释里不能出现 `*` 加 `/`。**

写 ``  `packages/<组>/<包>`  `` 那种路径时，如果原样写成星号斜杠，
**它会提前闭合块注释**，oxc 直接 PARSE_ERROR。
写成 `<组>/<包>` 或者拆开写。

**2. `packages/<a>/<b>/tests/` 必须配 `src/invariant.ts`。**

`scripts/test-invariants.ts` 会按测试路径去找同包的 invariant companion，
找不到就抛：

```text
test invariants: package test has no companion at ../packages/<a>/<b>/src/invariant.ts
```

**整个测试文件都跑不起来**，不是跳过。这是 harness 的硬约定：
每个包自己守自己的事件流不变量，不堆进中央校验器。
没有自己的事件时可以注册一个空的 install（只占位）。

### 🔴 三个实测踩到的坑（接着上面那两个）

**3. 用到什么就必须 `inject` 什么。**

用了 `ctx.sessions` 却没写进 `inject`，Cordis **不在加载时报错** ——
而是等到真执行那一刻才 `Cannot read properties of undefined (reading 'create')`，
然后被 `link.ts` 兜成「内部错误」，两边日志都看不出真因。

① 时写过一条「只 inject 现在真用得上的」，那条没错，
但反过来的一半更要紧。

**4. 跨语言的「长度」不是同一个东西。**

签名里写各段长度防拼接歧义，两端都用「长度」——但：

```text
'pc-🖥'   JS  .length = 5    （UTF-16 码元）
          PY  len()   = 4    （字符）
          UTF-8 字节  = 7
```

设备名带一个 emoji，两端签名就对不上、**握手永远失败**，
而错误信息只会说「身份证明不对」。两端都改用 UTF-8 字节长度，
并且**拿 TS 跑出的真实签名钉进了 Python 测试**
（`test_local_link.py` 的 `TS_VECTORS`）—— 任何一端改算法都会红。

**5. `wss.close()` 在还有客户端连着时回调永远不来。**

测试会卡到超时。要先 `for (const c of wss.clients) c.terminate()` 再关。

### Session / Turn

```typescript
ctx.sessions.create(id?, { seed?, meta? })   // 公开，可程序化创建
ctx.sessions.get(id) / list() / fork(...)
session.append('turn/start', { turn: N })
session.append('turn/end', ...)
```

### Approval

```typescript
ctx.approval.request({ agent, toolName, callId?, reason? })
  → 'allowed-once' | 'rejected' | 'cancelled' | 'unavailable'
ctx.approval.setApprovalPolicy(...)   // 'ask' | 'never'
```

Answerer = `approval/request` 的 waterfall 监听器；返回结果表示应答，
`next()` 表示交给下一个。**每个部署只该有一个终端 answerer**
（「sibling listener order is not a policy priority mechanism」）。

---

## 八、施工优先级

```text
P0  能安全地读                                ✅ 全部完成 2026-08-24
├── ① Local Gateway（Cordis 插件骨架）        ✅
├── ② MCP WebSocket Transport（两端）          ✅
├── ③ 策略 + Execution Session                ✅（approval 推迟到 P1，见下）
├── ④ Single Execution Session                ✅（并进 ③）
└── ⑤ Event Stream + 本地 JSONL               ✅

P1  能被批准着写
├── ⑥ UI Approval Bridge                     ✅ 2026-08-24
├── ⑦ Execution Summary → VPS                ✅ 2026-08-25
└── ⑧ World / Memory Writer                  ✅ 2026-08-25

P2  以后
├── Desktop timed grant
├── Multi-machine
├── Queue / Concurrent execution
└── 幂等重试策略
```

### ⚠️ P0 验收时要知道：`ASK` 那条分支还没有出口

Permission answerer 在 P0-③，但 **UI Approval Bridge 在 P1-⑥**。
所以 P0 阶段所有 `ASK` 都会解析成 `unavailable → DENY`。

**这不是坏了，而且不用我们实现** —— 「missing or failing answerers fail closed」
是 harness 的官方降级行为（见第四节）。

P0 阶段实际可用的只有 `ALLOW`（只读白名单）和 `DENY`。
验收标准就是「只读全链路通、越界的被拦住」，别指望能批准写操作。

### 工期

| | | |
|---|---|---|
> 🔵 2026-08-24 摸完源码后修正。原估基于「什么都要自己造」，
> 实测发现 approval / turn 边界 / 事件挂点都是现成的。

| | 估 | 说明 |
|---|---|---|
| ① Gateway 骨架 | 1 天 | 插件形状已经清楚（见七之二），这个数是准的 |
| ② WS Transport 两端 | 1.5 天 | 双向认证算在里面。**这是唯一真正从零写的一块** |
| ③ Permission answerer | 0.5 天 | 从「造状态机」变成「实现一个 answerer + 建 Agent」 |
| ④ Execution Session | **0** | 并进 ③ —— 复用 harness 的 session/turn |
| ⑤ Event Stream + JSONL | 0.25 天 | 挂 `tools/result` 就行 |
| Nox Core 接上 | 0.5 天 | 熟路，已经接了五个 MCP |
| 断线幂等（原 P2） | **0** | turn 边界白送（见第三节） |

**P0 约 3.75 天**跑通只读全链路。

### 省下来的是最容易做错的那部分

原本要自己写的权限状态机、审计账本、崩溃恢复语义，
现在全是 harness 的现成机制。**自己写这三样，出 bug 的概率远高于接线。**

### 还没验的一条

`ApprovalRequest.agent` 的归属语义 ——
「a UI answerer only answers for agents it owns」。
Gateway 建的 Agent 要让 Caelum UI 的 answerer 认得。
**不阻塞开工，实现 ③ 时解决。**

---

## 八之二、实施记录

### 代码放在 `packages/caelum/local-gateway/`，不是 `caelum-os/`

`FORK-分支策略.md` 说「我们的东西全在 `caelum-os/` 目录」。
那条写的时候，我们的东西只有补丁和文档；Gateway 是一个**需要被
workspace 解析的包**，情况变了。

`pnpm-workspace.yaml` 只认这几个：

```yaml
packages:
  - vendor/*
  - packages/*/*        ← 两级分组
  - apps/*
  - website
  ...
```

`caelum-os/` **不在里面**。两个选择：

| | 代价 |
|---|---|
| 放 `caelum-os/gateway` | 要改 `pnpm-workspace.yaml` → **补丁数 1 → 2**，而且那个文件官方常动，是最容易冲突的地方之一 |
| 放 `packages/caelum/local-gateway` | **0 改动** —— `packages/<组>/<包>` 天然认，官方不会创建 `packages/caelum/` |

**选后者。** 「内核 0 脚印」的实质是**不改官方文件**，不是"物理上放在哪个目录"。
新增目录 git merge 不冲突；改共享配置文件才会。

⚠️ `caelum-os/FORK-分支策略.md` 那句「全在 caelum-os/ 目录」
要跟着改成：**「不改官方文件」是硬规矩，目录位置服从它。**

### P0 完成时的样子（2026-08-24）

```
packages/caelum/local-gateway/
├── src/catalog.ts     harness 工具名 → computer.* 白名单映射
├── src/protocol.ts    JSON-RPC 帧
├── src/auth.ts        双向 HMAC 挑战
├── src/link.ts        主动外连、握手、退避重连
├── src/server.ts      MCP 方法分发（四道关）
├── src/session.ts     Execution Session（复用 harness turn）
├── src/policy.ts      权限策略
├── src/events.ts      审计事件流
└── tests/             58 条

nox-core/
├── tools/local_link.py     另一端（WS 服务端 + MCP client）
├── api/server.py           /agent/local 端点（没配密钥就不注册）
└── tests/test_local_link.py  18 条
```

**全链路实测**（真跑，不是看代码）：

```
Nox → 手：读 package.json   → 成功，读到真实内容
Nox → 手：写 /x             → 被拒
审计：{"tool":"read","caller":"nox","ok":true,"ms":4,"paths":["D:/x/secret.txt"]}
      ← 路径记了，文件内容里的 PASSWORD=hunter2 没进去
```

### 🔴 ③ 的范围判断：P0 不接 `ctx.approval`

P0 只做只读，**只读不需要审批**。而 UI answerer 在 P1 ——
现在接上的话，所有 `ask` 都会因为「没有 answerer」fail-closed，
等于写一堆走不到的代码。

所以 P0 的「权限」是 `policy.ts` 的白名单：只读放行，其余一律拒绝。

### 🔵 实测发现：`policy.ts` 现在是死代码

实测「写被拒」的理由是 `没有这件能力：computer.write_file` ——
走的是**第 1 关白名单**，不是第 2 关策略。因为 `catalog.ts` 里
`write` 是注释掉的，名字根本翻译不出来。

**这不是错，两层是纵深**：

```text
catalog.ts   管「Nox 能不能看见这件能力」
policy.ts    管「看见了能不能做」
```

P1 把 `write` 放进 catalog（因为要让 Nox 知道有这个能力）之后，
policy 才开始真正把关。**在那之前它是备着的，不是多余的。**

### ⚠️ 一个躲不掉的例外：`pnpm-lock.yaml`

「不改官方文件」有一个例外 —— **加任何包都会动 lock 文件**。实测改动：

```diff
+  packages/caelum/local-gateway:
+    devDependencies:
+      '@deepseek-ai/cordis':      link:../../../vendor/cordis
+      '@deepseek-ai/dsh-invariants': link:../../runtime-diagnostics/invariants
+      '@deepseek-ai/dsh-tools':   link:../../core/tools
```

**12 行纯新增，全是 workspace 内部链接，没有引入任何外部依赖。**

这一点很重要：只要我们的包**不引入新的第三方依赖**，lock 的改动就永远是
「多一个 workspace 成员」这种可加性的块，官方升级时 merge 冲突好解
（保留双方即可）。

🔴 **所以 Gateway 有一条自律**：**能用 workspace 内已有的，就不装新的 npm 包。**
WS transport 那块尤其要注意 —— 别顺手 `pnpm add ws`，
先看 harness 自己用什么做 WebSocket。

### P0-① 做了什么（2026-08-24）

```
packages/caelum/local-gateway/
├── src/index.ts        Cordis 插件入口，inject: ['tools']
├── src/catalog.ts      harness 工具名 → computer.* 的白名单映射
├── src/invariant.ts    占位（harness 硬性要求，见接口速查）
└── tests/catalog.spec.ts   9 条，全过
```

两条设计决定值得记：

**① `inject` 只写现在真用得上的。** 提前写 `sessions` / `approval`，
一个还没接的服务缺席就会让整个插件加载失败 —— 而 P0-① 本来能独立跑。

**② 白名单，不是黑名单。** harness 有几十个工具、还会随官方升级增加。
黑名单的话，官方哪天加一个 `deploy_to_prod`，它会**默认可用**，
我们要到出事那天才发现。测试里专门有一条守这个。

---

## 八之三、P1-⑥ 审批通道（2026-08-24）

```text
Caelum UI (Electron) ──ws──▶ Gateway (127.0.0.1:39100)
                              └─ approval/request answerer
```

**Gateway 当服务端**，因为它跟着 harness 常驻、UI 开开关关。
而且这样「UI 连着没」天然等于「有没有人能批」，不需要额外的在线状态表。

### 实测三种结局

```text
UI 没连着  → "现在没人能批准（Caelum UI 没连着）"  文件没动
她点拒绝    → "她说不行"                          文件没动
她点同意    → 执行了，写了 D:/她的日记.md
弹窗看到：computer.write_file · ["D:/她的日记.md"]
```

### 🔴 官方的 approval 不带参数

`ApprovalRequest` **没有 `arguments` 字段**，注释写着：

> `callId` links to an already presented tool call,
> **so arguments are not duplicated here**.

它假设 UI 已经流式看到过这个 tool call。**Caelum UI 没有那个前提** ——
它只在审批那一刻才知道有这件事。

参数拿不到的话，弹窗上只有「他要写文件」、没有写哪个，
**点同意等于盲签**。所以 `ExecutionSessions.currentArgs` 存了一份，
answerer 用回调取（同时只有一个执行，所以就一个字段）。

### 🔴 两把本地 token，挡的是不同的东西

```text
CAELUM_LINK_SECRET  ≥32   挡公网 —— 谁能冒充她的电脑 / 冒充 Nox Core
CAELUM_UI_TOKEN     ≥16   挡同机 —— 谁能替她点「同意」
```

⚠️ **绑定回环挡的是外网，不是本机的其它程序。** 同一台机器上
任何进程都能连 `127.0.0.1:39100`，所以审批通道也要验 token。

两把都是**没配就不启动那条通道**，不是"没配就跳过"。
UI 通道不启动的后果是**所有要点头的操作一律被拒**（fail closed），
那是对的默认。

### 现在的三档

```text
ALLOW  read / find / search          自动放行
ASK    write / edit                  每次都要她点头
DENY   其余一切（含 bash / pwsh）      默认拒绝
```

⚠️ **`bash` 故意还没放开** —— 它能绕过上面所有的路径限制
（一句 `cat` 就读了白名单外的文件）。放开它要先有命令级审核，
不是在 catalog 里加一行。

---

## 八之四、P1-⑦⑧ 摘要回传与记住（2026-08-25）

```text
执行 → tools/result → events.ts ──┬─→ 本地 JSONL（完整，不出网）
                                  └─→ summary.ts → 链路通知 → LocalLink._remember()
                                                                    ↓
                                                              World Model
```

**实测**：

```json
让他读文件  → {"读到了": true}
他记住的事  → {"做了什么": "computer.read_file",
              "动了哪些": ["package.json"],
              "成了吗": true, "设备": "糖糖的电脑", "花了多久ms": 4}
```

### 🔴 只回传他自己发起的

harness 本地 agent 干的活也在审计里，但**那不是他做的**。
传回去他会以为自己做过，然后写进 World Model ——
**那是让他记住一件没发生过的事**。

`summary.ts` 用 `caller === 'nox'` 把住，有测试守着。

### 摘要比审计更严

```text
events.ts   本地 JSONL   完整、给人查、不出网
summary.ts  回传 VPS     精简、给他记住、**要出网**
```

两者都不含文件内容，但摘要还要再筛一道：
只有白名单内的能力才回传，字段固定六个
（`kind` `at` `capability` `ok` `paths` `ms`）。

### 没连上就丢弃，不补发

`link.notify()` 在没连上时**静默丢弃**。攒起来等重连补发，
会让他收到一堆过期的「我刚才做了…」，而那些事已经是几小时前的了。

### ⚠️ 现在还做不到 "work session"

架构文档第七节画的是这种：

```json
{ "type": "work_session.completed", "files_changed": 6,
  "tests": { "passed": 42, "failed": 0 }, "summary": "修复 CareLedger 落库" }
```

那要 Gateway 支持**多步执行**（改代码 → 跑测试 → 再改），
而现在一个 execution session 只跑一件事。

所以这一版发的是**单次执行的摘要**。`ExecutionSummary.kind` 那个字段
就是为多步做好的位置 —— 等 P2 支持多步了，在这层上面聚合。

---

## 八之五、正式挂上（2026-08-25）

从「代码写完了」到「他真的能用」，中间挡着五个坑。**每一个都是绿着的测试没抓到的。**

### 现在的状态

```text
Caelum Gateway（她电脑，tsx 常驻）
  ├─ 5 件能力  read_file · find_files · search_files · write_file · edit_file
  ├─ ws ──▶ wss://noxtang.com/agent/<前缀>/local   ← Caddy handle + rewrite
  └─ ws ◀── Caelum OS 审批通道 127.0.0.1:39100

Nox Core（VPS）
  └─ tools/computer.py  5 件工具 → LocalLink → 她的电脑
```

密钥在 `~/.caelum/env`（**不在任何仓库里**），Gateway 和 Caelum OS 读同一份。

### 五个坑

**① Caddy `handle_path` 会剥掉前缀** → 403。
文件里本来就有一条针对 `/health` 的同款警告，我还是踩了。
用 `handle` + `rewrite * /agent/local`。

**② 路径前缀进了日志。**
`/agent/<前缀>/local` 那段随机串是半把钥匙。它被 `log.info('链路建立：%s', url)`
打了出来，只能整条换掉。现在链路地址一律过 `safeUrl()`。

**③ `pkill -f` 在 Windows 上杀不掉 tsx。**
六个 Gateway 同时连着，互相顶（VPS 两分钟内记了 52 次「顶掉旧链路」），
表现是每 3 秒重连一次而握手全都成功。按命令行精确杀才行。

**④ cordis 的日志级别是反的。**
`ERROR=0 < INFO=1 < WARN=2 < DEBUG=3`，默认阈值 INFO —— **warn 比 info 更容易被吞**。
于是 `处理 tools/call 时出错：…` 一个字都没有，Nox 只收到「内部错误」。
exporter 必须显式 `levels: { default: 3 }`。

**⑤ 🔴 `inject` 里少了 `approval`。**
`index.ts` 的注释白纸黑字写着「approval 还没用（P1 接 UI answerer），所以先不写」——
P1 做完了，注释和 `inject` 都没跟着改。

Cordis 的 `inject` 是**执行期才验的**：加载不报错，跑到那一行才炸。
而当时 80 个测试全绿，因为 `e2e.spec.ts` 手搓 `ctx` 直接调 `handleRequest`，
**绕过了 inject 校验**。

补了 `tests/plugin.spec.ts`，规矩是**不许手搓 ctx，只许 `ctx.plugin(Gateway)`**。
反向验证过：把 `approval` 从 inject 拿掉，它立刻红。

### 还有一个不是坑、是设计漏了的

**审批超时 60 秒，链路超时 20 秒。** 她永远来不及点。

表现极具误导性：弹窗正常弹出、她那边一切正常，但 Nox 20 秒就报超时。
四次尝试四种错误，他自己的结论是「写这条链路本身不稳定」—— 方向完全错了。

现在要点头的能力走 `APPROVAL_TIMEOUT_S = 75`，只读的仍然 20 秒
（卡住时不该让她干等一分多钟）。有测试盯着 `APPROVAL_TIMEOUT_S > 60`。

### 第六个坑：装了接口包，没装实现包（当天下午糖糖自己试出来的）

启动日志一切正常、5 件能力都在，但一用搜索就是 `ripgrep launch failed`。

`subprocess` 家族有两个包：

```text
dsh-subprocess        接口 —— 占住 ctx.subprocess 这个位置，没有 spawn
dsh-subprocess-local  实现 —— 真正会起进程的，它自己继承前者
```

我装的是接口那个。于是 `ctx.subprocess` 存在（所以插件全都加载成功），
但 `ctx.subprocess.spawn is not a function`。

⚠️ **真因被吞了两层**：`search-core.ts` 把它收进 `SearchError.cause`，
而工具返回只带 `message` 不带 `cause`。Nox 拿到的就是一句
「ripgrep launch failed」，他据此判断「问题大概率在 ripgrep 二进制」——
方向错了，二进制好好的，`rg --version` 一跑就出来。

⚠️ 两个一起装会 `service "subprocess" has been registered`。**只装 `-local`。**

> 这已经是同一类第三次了（`inject` 少 approval、日志级别吞 warn、
> 现在是 cause 被吞）。**要么让真因到得了眼前，要么就得靠猜。**

### 实测

```text
读文件            → 成功，他读到了她电脑上的内容
搜内容            → 成功，报得出行号（修完 subprocess 之后）
写文件（没人点）  → 等满 60 秒 →「没等到答复」→ 文件一个字没动   ✅ fail-closed
```

「她点头 → 真的写进去」这一支由 `plugin.spec.ts` 覆盖，线上还没走过真人一次。

---

## 八之六、命令能力（2026-08-25）

他现在能在她电脑上跑 PowerShell 了。**三条前提缺一不可。**

```text
执行器    dsh-pwsh-sandbox      写操作被关在 workspaceRoot 里
审批      每一条都问，显示全文    没有「低风险自动放行」
硬拦      danger-full-access     一律拒绝，连问都不问她
```

### 为什么不做「低风险自动」

分级表（第四节）里写的是「低风险自动，高风险 ASK」。**做不了。**

命令的风险判不出来：`git config` 能把 pager 设成 `rm -rf`，
一个看着人畜无害的 npm script 能干任何事。想安全地自动放行，
得先有一个理解 shell 语义的审核器 —— 那比整个 Gateway 还难。

所以 v1 的规矩是一律问她，并且**把完整命令原样显示出来**：
不截断、不省略中间、`whitespace-pre-wrap` 保住换行和空格
（折没的换行能把两条命令看成一条）。

### 沙箱实测（她的 Windows 机器）

```text
普通命令      ✅  hello from pwsh
写工作区内    ✅
写工作区外    ❌  访问被拒绝 → 文件根本没被创建
```

⚠️ 官方标了 Windows 是 `enforcement: 'partial'` —— 受限令牌为了能启动
进程必须保留 Everyone，所以「任何人可写」的对象仍然写得进去，
NTFS 硬链接也能绕。**不是密不透风**，真正兜底的是「每条都要她点头」。

### 🔴 `danger-full-access` 是自毁开关

`pwsh` 的参数里有 `sandbox_permissions`，取值包括 `danger-full-access`。
**他自己就能带上它** —— 被 prompt injection 之后，一个字段就把
前面所有路径约束作废。

两道都堵了：Gateway 硬拦（`sandbox_escape`，**判在档位之前**，
所以挂在只读能力上也拦），Nox Core 源头掐掉不往下传。

有一条测试专门守着**「她根本没被问过」** —— 弹一次窗就等于
给她一个「点错就破防」的机会。

### 工作目录：配上了 ≠ 用上了

第一次改完，`session.header.cwd` 确实写上了、启动日志也打出
「他站在 D:/claude-code」、测试也绿。**然后他跑 `Get-Location`，
答案是 `D:\deepseek-harness\caelum-os`。**

因为工具读的是 `exec.agent.session.header.cwd`，而
`ctx.tools.execute()` 那一行**根本没传 agent**。

> **教训：验「配置有没有生效」，要从使用方去看，不是从配置方。**
> 断言 `header.cwd === 'D:/claude-code'` 是在证明我写对了配置，
> 而不是在证明工具收到了它。现在那条测试从工具执行时
> 实际拿到的 `exec` 里读 —— 反向验证过：去掉 `agent` 那行立刻红。

顺带挖出一个更糟的：`sessions.create()` 原本在 `try` **外面**，
它一抛（比如 cwd 给了相对路径），`this.current` 已经置上而 `finally`
还没接管 —— **这只手会永远显示「正忙」，之后所有请求一律被拒**。
表现是「他突然什么都做不了了」，而日志里只有第一次那条报错。

---

## 八之七、多步执行：一次点头覆盖一整段（2026-08-26）

### 🔴 「审计划」这条路走不通

第一版的想法是：他报一份步骤清单 → 她审清单 → 按清单执行。

**「改代码 → 跑测试 → 再改」这个循环没法预先计划。**
第二次改什么，取决于测试输出，他事先不知道。所以真到第二步就
偏离清单了，于是又回到逐条问 —— 等于什么都没解决。

能成立的是另一种：**授权给的是「范围」，不是「步骤」。**

```text
目标   修 Care Ledger 落库的 bug        ← 她靠这句话决定点不点头
范围   D:/claude-code/nox-core/care     ← 出了这个圈就重新问
上限   8 步 / 12 分钟                    ← 先到哪个算哪个
```

和第四节给 Desktop 设计的那套是同一个形状：
**文件权限问「哪些路径」，这种授权问「多久、多少步」。**

### 分工

```text
读 · 找 · 搜        一直是自动放行
改文件              范围内 → 授权罩着，不再问
                   范围外 → 退回逐条问
命令                🔴 永远逐条问，授权管不着
```

最后那条是糖糖定的。理由是这两件事的风险性质不同：改文件是有边界的
（就那个文件），而**一条命令能干任何事** —— 授权覆盖命令的话，
「范围」这个概念就失效了，一句 `Copy-Item` 就把范围外的东西搬了进来。

### 守住的地方（`grant.ts`）

这个文件是整套设计里**唯一能被绕开的地方**。别处判错顶多是拒绝了
不该拒绝的（她多看一个弹窗）；**这里判错是放行了不该放行的，
而她永远不会知道。**

| 攻击形状 | 挡法 |
|---|---|
| `nox-core/../bridge/x` | 一律 `resolve` 之后再比前缀 |
| `nox-core-secret/x` | 边界必须带分隔符 |
| `scope: ['C:/']` | 范围不能越出 workspace，**直接拒，不问她** |
| 多路径里混一个越界的 | 「**所有**路径都在范围内」，不是「任意一个」 |
| 一个路径参数都没有 | 返回 false —— 作用对象不明的事不该由授权放行 |
| 他要 200 步 | 夹到 40 步 / 30 分钟上限 |

⚠️ **步数记在执行之前**。她给的是「最多做这么多步」，不是
「最多做**成**这么多步」—— 失败的尝试一样耗预算，
不然一个反复失败的循环可以无限跑下去。

### 🔴 UI 那条横幅不是提示，是授权的另一半

她一次点头放行了一段范围，这件事之所以成立，靠的是两样东西：

```text
她全程看得见他在做第几步、动的哪个文件   ← WorkBanner
她随时能摁停                            ← 那个「停」
```

少任何一样，那次点头就是一张空白支票。架构第四节写得很直白：
**不许悄悄在后台动。**

几条刻意的选择：横幅**不弹窗、不抢焦点**（他连做十步时会烦死人，
而烦人的安全提示的下场是被划走）；「停」**不做二次确认**
（多问一句「确定要停吗」都是在她想喊停的时候拦她）；
结束之后**停留 8 秒再消失**（立刻不见的话她来不及知道发生过什么）。

### 实测（2026-08-26）

```text
14:57:34  要开一段工作：给 nox-core/tools 加两个自检文件（3 步 / 10 分钟）
14:57:45  这段工作她批了                        ← 她点的，全程就这一次
14:57:47  允许 computer.write_file（这段工作她批过了）  还剩 2 步
14:57:47  允许 computer.write_file（这段工作她批过了）  还剩 1 步
14:57:48  这段工作结束（用了 2 步）
```

两个文件真的写出来了；横幅她确认看到了。

### 撤销这段工作（2026-08-26 下午）

一次点头之所以敢点，靠三样：**看得见**（横幅）、**停得下**（那个「停」）、
**退得回**（这个）。前两样上午做了，第三样欠到下午。

```text
开工   →  临时索引拍一张 git tree 快照（约 1.3 秒，522 个跟踪文件）
每步   →  记下动过的路径（WorkGrant.touched）
撤销   →  逐个对照快照：有 → 退回那一版；没有 → 是他新建的 → 删掉
```

#### 🔴 只退他动过的，不退整棵树

最容易想到的是「回到这段开始前」—— 一个 `git checkout`。**不能那么做**：
这段时间里她自己也可能在改别的文件（她就坐在电脑前，横幅就是给她看的）。
整棵树回滚会把她的活儿一起抹掉 —— **那是撤销功能最不该干的事**，
比不提供撤销还糟。有测试专门盯着这条。

#### 🔴 快照不能碰她的暂存区

用 `GIT_INDEX_FILE` 指向临时索引，`read-tree HEAD` → `add -A` → `write-tree`。
直接 `git add -A` 的话会动她自己的暂存区 —— 她可能正 stage 到一半。
实测：拍完 `git status` 一个字没变。

#### 退不了的情况要明说

- 工作区不是 git 仓库 → 整个功能不可用，**弹窗上告诉她「这段退不回去」**
  （能不能退会改变她点不点头）
- 文件被 `.gitignore` 挡着 → 快照里没有它，退不了
- **退了一半 → `ok=false`**，横幅上写「只退回了 N 处，还有 M 处没退成」。
  报笼统的「已撤销」而实际只退了一部分，她会以为回到原样了，
  然后在一个半新半旧的树上继续干活

#### 实测出的一个交互错误

第一版「撤销这段」只在工作**结束后**才显示。实测时糖糖找不到它 ——
他还剩一步没走完，横幅上只有「停」。

> **她想撤销的时候，多半正是他还在做错事的时候。**
> 不该逼她先停再撤。改成做到一半也能撤（Gateway 本来就是先收回授权再退，
> 中途撤是安全的）。

⚠️ 但「撤销」**要确认一次**，而「停」不要 —— 这两个按钮性质不同：
停最坏是白停一次，损失为零；撤销会真的改文件，误点的代价是他刚做的活儿全没了。

```text
20:11:52  她要撤销这段：改一个文件验证撤销（动过 2 个文件）
20:11:52  撤销结果：退回 1 / 删掉 1 / 没退成 0
```

### 施工优先级更新

```text
P2  以后
├── Desktop timed grant       ← 桌面控制还不存在
├── Multi-machine             ← 她只有一台电脑
├── Queue / Concurrent        ← 同时只允许一段，够用
└── 幂等重试                   ← turn 边界白送（第三节）
```

**P2 这四条现在做都是浪费。** 更值得做的是别的：让他站在正确的
工作目录里跑测试之后**能读到失败详情并接着改**（现在能跑能改，
但一段工作里跑命令仍然要她点头，长循环还是会烦到她），
以及 Resonance 接桌面 Activity 感知。

---

## 八之八、感知层 + 浏览器（2026-08-27）

> 糖糖：「browser 除了能控制之外，也需要跟 resonance 结合。」

这句话把 browser 从「再加一件能力」变成了两件事：**手**（他能开网页）
和**感官**（她在电脑上干什么）。两者安全模型完全不同，分开做。

### 🔴 控制：他自己的浏览器，不是她的

技术上完全可以接管她那个 Edge（开 remote debugging 端口 attach 上去），
而且那样他能用她所有的登录态。**正因为做得到才必须挡死** ——
CDP 没有权限模型，attach 上就是全部：银行、邮箱、所有 cookie。

而他实际需要浏览器做的事（查文档、看页面）**不需要她的登录态**。
所以起一个独立 profile（`~/.caelum/browser-profile`）。

⚠️ **这条是代码里写死的，不是配置项。** 做成开关的话，迟早为了
「就这一次」打开，然后忘了关。有测试读源码盯着：不许出现她的 Edge
数据目录、不许 `connectOverCDP`、不许有 `config.*profile` 这种口子。

协议白名单 `http/https`：`file://` 会变成一个绕过所有路径限制的读文件通道。
**白名单不是黑名单** —— 浏览器以后还会加新协议。

点击/填表**没做**：能提交表单 = 能替她做出承诺。那一档要她点头，
而且弹窗上得说清「点的是哪个按钮」—— 一个 CSS selector 对她毫无意义。

### 感知：前台窗口就够了

她提的三个源（Windows 前台窗口 / 浏览器页面 / 游戏），**一个 API 全覆盖**：

```text
GetForegroundWindow + GetWindowText
  → 哪个应用 · 什么游戏 · 浏览器在看哪一页（Edge 的窗口标题就是页面标题）
```

不需要装扩展。扩展只在需要精确域名时才必要 —— 那是以后的事。

### 🔴 为什么放进 Gateway，而不是新起一个服务

因为 app-tracker 的教训。查的时候发现：

```text
systemctl is-active app-tracker   active
最后一条数据                       2026-08-02   ← 25 天前
```

**服务活着，数据源死了，没有任何信号。** 一个喂给死传感器的 Drive
不会报错，它只会安静地永远不触发 —— 或者更糟，把三周前的数据
当成「她现在在刷抖音」。

放进 Gateway 之后存活性天然可见（Gateway 挂了 `local_hand.ready`
就是 false）。剩下那种（Gateway 活着但采样进程死了）由三态兜住：

```text
live     刚收到过
stale    超过 30 分钟没动静 → 「别据此判断她在不在」
unknown  没连上 / 一条都没收到过
```

⚠️ `unknown` 和 `idle` **必须分开**。混成一个的话，传感器挂掉会被
读成「她一整天没碰电脑」。

### 隐私

窗口标题里什么都可能有 —— 文档名、私密页面、聊天对象的名字。

- `~/.caelum/activity-ignore` 一行一个关键词，命中的**整段不上报**
- 写 `off` = 全局暂停
- **每次读文件，不缓存** —— 一个要重启才能生效的隐私开关等于没有
- 挂机（2 分钟没操作）不记：她去做饭了不是一种「活动」
- 🔴 命中忽略名单时**连「有一段被忽略了」都不报** ——
  报了的话，从时间空档一样能推出她在干什么

### PowerShell 那一路踩了三个坑

全都是**没有任何报错、只是没输出**的那种：

| | |
|---|---|
| 文件没有 BOM | PS 5.1 按 GBK 读 .ps1，中文注释变乱码，可能把后面代码一起吃掉 |
| 设了 `[Console]::OutputEncoding` | **输出在管道里整个消失**（它前面的 `Write-Output` 能出来，后面的没了） |
| 用 `-Command -` 喂 stdin | `Add-Type @"..."@` 解析不了，一行输出都没有也不报错 |

最后的形状：**UTF-8 带 BOM 的 .ps1 + `-File` 启动 + 输出走 base64**。

### 测试怎么验

真探针**只在她真的换窗口时才出声** —— 她出门那两小时它一整天只吐一行
`idle`，而一段的门槛是 20 秒。所以 `ActivitySensor` 留了注入口，
用一个按剧本吐 base64 的假探针，真实的 spawn / 解析 / 分段 / 隐私过滤
全都照跑。**不然这一整套只能靠「等她回来」来验证，那不叫验证。**

---

## 八之九、看图（2026-08-28）

> 糖糖清点手上还缺什么，第一件挑的就是这个：
> 「他现在读不了图。你截个图丢给他，他看不见。」

### 🔴 不能用 harness 现成的 `read_image`

harness 的 `tool-fs` 自带一个 `read_image`，但它做的是**另一件事**：
把图提交给附件服务，返回 `ImageBlock{ attachment }`，
让图进入**调用方 agent** 的上下文。

对我们两条都不成立：

```text
1. 它要求当前路由声明了图像能力 —— Gateway 的 agent 不是视觉路由，
   直接调会被它自己拒掉
2. 它返回 attachmentId，那是 harness 附件库里的编号 ——
   对 Nox Core 来说是一串没有意义的字符
```

而且**方向是反的**：图该进 Nox 的上下文，不是这只手的上下文。
所以自己写了一个（`image-tools.ts`），只负责递字节。

⚠️ 工具名是 `look_at_image`，**不叫 `read_image`** —— 重名会覆盖掉
tool-fs 那个。对外仍然叫 `computer.read_image`，翻译在 catalog。

### 手取字节，眼睛替他看

主模型是 DeepSeek，读不了图。Core 那边 2026-08-02 就有一套
「找人替他看」（`agent/vision.py`，PROJECT.md 第十九节）：
视觉模型把图看成文字，转述并进正文，图本身丢掉。

```text
手：读字节 → base64 → 藏在文本块里递过去
心：摘出来 → 视觉模型 → 一段描述 → 进他的上下文
    base64 到此为止，绝不入上下文
```

**🔴 base64 绝不能进上下文。** 一张 200KB 的图 base64 之后约 27 万字符。
真漏进去的话不只是这一轮废掉 —— 它会被**存进会话历史**，
之后每一轮都带着它，直到有人发现账单不对。而且不报任何错。
`computer.py::_split_image` 就是这道闸，测试里有专门一组盯着它。

### 两边靠一个前缀对暗号

```text
image-tools.ts::IMAGE_MARK  ←→  computer.py::_IMAGE_MARK
```

⚠️ 对不上的表现是：**手明明读到了图，他却说看不了** ——
两边各自都是对的，所以谁都不报错。两边都有测试锁着这个常量，
Core 那边那条会直接去读手的源码比对。

### 能看哪儿

| 位置 | 为什么 |
|---|---|
| 工作区 | 和 git / read_file 同一个边界 |
| `~/.caelum/inbox` | **她的投递口** |

🔴 只有工作区的话这个能力基本是废的 —— 截图默认存在「图片/屏幕截图」
底下，她得先把文件挪进 `D:\claude-code` 才能让他看，那还不如直接打字。
所以专门开一个投递口：她把图丢进去，他就能看。

范围是**明确列出来的两个目录**，不是"整个磁盘"，走的还是 `inScope`。

### 认魔数，不认扩展名

一个 `.png` 结尾的文件可以是任何东西。不挡的话，一个文本文件会被
送去视觉模型，然后他拿到一段**瞎编的描述**。
这不是安全问题，是**诚实问题**。

其余边界：单张 3 MiB 上限（超了要**说出实际大小**，不然他会原地重试）；
扩展名先做初筛，省得把一个 500MB 的日志整个读进内存。

### 措辞和「她发来图片」不一样

`vision.wrap()` 的开头是「她发来 N 张图片」——
但这次是**他自己去看的**。照抄的话他会莫名其妙谢她发图。
所以这条走自己的包装，同样明写「以上是转述，别说得像你亲眼看见的」。

视觉模型挂了就如实说看不了，**绝不编**（和 `nox.py::_see` 同一条规矩）。

---

## 八之十、常驻终端（2026-08-28）

> 糖糖：「有了它他才能自己起服务、自己看日志，不用每次让我跑。」

`run_command` 是一次性的 —— 起一个 dev server 会把那条调用**永远卡住**。

### 🔴 harness 那套用不了：它要一个本地大脑

harness 有完整的一套（`dsh-terminal` + `terminal-bash` + `tool-terminal`），
但所有权是**按 Agent 划分**的：

```text
isLiveOwner(owner) = ctx.get('agents')?.get(owner.id) === owner
```

要一个**真正注册过的 Agent**。而注册 Agent 要 `agents.setFactory()`，
唯一的工厂实现是 `dsh-agent-loop` —— 它的 `inject` 里有 **`llm`**。

也就是说：用它就得在这只手里装一个模型。**那和第〇节正好反着来。**

所以和 git / 浏览器 / 看图一样自己写（`terminal-tools.ts`），
直接站在更底下那个原语上：`ctx.subprocess.spawnTerminal()`。
**沙箱照套** —— `ctx.sandbox.confine()`，和 `terminal-bash` 同一行代码，
拿不到 sandbox provider 就不开，绝不裸跑。

⚠️ 工具名是 `shell_*` 不是 `terminal_*`，避免哪天有人装了官方那套时重名覆盖
（和 `look_at_image` 同一个考虑）。对外仍然叫 `computer.terminal_*`。

### 权限：谁真的会跑命令

| | 档 | 为什么 |
|---|---|---|
| `open` | 自动 | **开完什么也干不了**，一个 shell 停在提示符上 |
| `send` | **每次问她** | 往里打字 = 执行命令，和 `run_command` 同一件事 |
| `read` / `list` / `close` | 自动 | 读输出、列自己开的、收自己开的 |

🔴 `computer.terminal_send` 同时进了 `NEEDS_APPROVAL` **和 `ALWAYS_ASK`**。
只进前者的话，**Work Grant 会顺手把它放行** —— 而那正是糖糖说过不行的事
（命令永远逐条问）。有测试专门盯这条。

### 🔴 审批弹窗差点是空的

`approval.ts` 的 `commandOf()` 原来只认 `command` 这个键，
而 `terminal_send` 的参数叫 `text`。不改的话她看到的是
「Nox 要用 terminal_send」**而看不到任何命令内容** ——
等于让她闭着眼睛点同意，那还不如不问。

**加任何新的执行类能力，先回去看那个函数。**

### 没有 `interrupt`：做不到就不给

本来做了 Ctrl+C。实测三种写法**全废**：

```text
写 ETX(0x03)       死循环照跑
写 ETX + CR        死循环照跑
signalForeground   返回成功，死循环照跑
```

沙箱的受限令牌壳夹在中间，conpty 的 Ctrl+C 传不到前台进程组。

**所以那件工具删掉了。** 一个「报告打断成功、其实什么都没干」的工具
比没有更糟 —— 他会以为进程停了，然后基于错的前提接着做。
要停跑飞的东西就 `close` 整个 shell，那个是真的会杀掉整棵进程树。

### 两件"看不见但很贵"的事

**ANSI 要洗掉。** pwsh 的输出里全是 `\x1b[93m` 这种着色和光标控制，
一屏日志能有一半是控制字符，而它们会**原样进他的上下文**。
设 `TERM=dumb` 没用，Windows 上照样吐。

⚠️ 洗的时候要处理**跨 chunk 被切断的序列**：PTY 输出是流，
一个 `ESC [ ? 2 5 h` 完全可能被切成两块，逐块洗会漏出个 `?m` 进正文。
残尾要留到下一块再洗。

**PSReadLine 的历史文件写不进去。** 沙箱不让写 AppData，
于是**每一条命令都会带回一段「访问被拒绝」**，他会以为自己的命令失败了。
开场发一句 `Set-PSReadLineOption -HistorySaveStyle SaveNothing` 关掉，
然后**等它安静下来再清缓冲** —— 写死一个毫秒数的话，
那条报错正好在清理之后冒出来，挂在第一条命令的输出里。

### 边界

最多 4 个 shell · 每个留 200KB 输出 · 闲置 30 分钟自动收 ·
`cwd` 必须在工作区里 · 一次 `send` 最多等 20 秒就返回（**进程继续跑**，
这正是常驻的意义，而且要**说出来**，不然他会拿半截输出下结论）。

---

## 九、和旧文档的关系

| 旧文档 | 状态 |
|---|---|
| `CAELUM-OS-ROADMAP.md` 一切皆插件 / 三层边界 | ✅ 仍然有效 |
| 同上 第三节「继承→fork→本土化」 | 🟡 **fork 已完成**（08-16），但「Nox 住进内核」被这份取代 |
| `CAELUM-OS-ARCHITECTURE.md` 第八节 内核 = DeepSeek Harness | 🔴 **被取代**：harness 是手，不是 Nox 的内核 |
| `caelum-os/FORK-分支策略.md` | ✅ 仍然有效，Gateway 遵守「内核 0 脚印」 |

⚠️ 另有一件事这两份旧文档没跟上：**`nox-app/caelum-os-ui` 这个
Electron 桌面端是 08-15 之后新起的，不在旧文档的规划里。**
它现在是「脸」，将来可能搬进 harness 也可能不搬 —— 那是装修问题，
不影响这份架构。
