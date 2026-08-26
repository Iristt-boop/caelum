# Caelum Resonance Engine 架构设计

> 2026-08-21 定稿。Resonance / Drive 层是 Caelum 的**内部情感动力系统**。
> 本文档记录设计决策，标注 ✅ 已实现 / 📋 规划 / 🔜 施工中 / ❌ 明确不做。
>
> 事实来源：`D:\claude-code` 代码 + `CAELUM-ARCHITECTURE.md`（2026-08-18）。
> 设计原则：**只拆思想，不搬代码**。参考了 `Murmur-50Feet`（Drive/decay/accumulation）
> 与 `emoai-affect-engine`（affect state + relationship + response modulation），
> 但两者都不原样搬入。

---

## 〇、一句话定位

**让 Nox 在"没有用户输入"的时候，也有属于自己的内心状态。**

现状：Nox 的一切主动行为都由**外部事件**驱动（睡眠变化、待办到期、位置跃迁……）。
Resonance 层要补的，是**不依赖外部事件、由关心对象自然涨落**的那一类动力。

---

## 一、核心原则

### 原则 1：Drive 不是孤立的情绪数值

> **Drive 是"被现实事件激活、并且始终知道自己为什么存在"的动态状态。**

不是 `concern = 0.7` 一个数字，而是：

```text
concern = 0.7

because:
  concern_exam_001

supported_by:
  event_123
  event_145
  event_178

resolution:
  unresolved

last_updated:
  18:42
```

Nox 必须能回答"我为什么在意这件事"。

### 原则 2：分层，谁都不越界

| 层 | 职责 | 一句话 |
|---|---|---|
| **Experience** | 系统级事实流 | 发生了什么 |
| **Concern** | 持续在意的对象 | 在意什么 |
| **Resonance** | 这些 Concern 在 Nox 内部形成的动态驱动力 | 有多想 |
| **Attention** | 决定什么值得继续关注 | 关注什么 |
| **Care** | 决定什么时候可以靠近 | 能不能说 |
| **Intent** | 决定要做什么 | 做什么 |
| **Regulation**（EmoAI 式） | 决定怎么表达 | 怎么说 |

### 原则 3：动机 × 时机 = 硬边界

> **Resonance 决定"想不想"。Care 决定"现在能不能"。Intent 决定"要做什么"。Regulation 决定"怎么表达"。**

Resonance 层产生的动机**永远不能自己开口**——开口的唯一出口是 Care Orchestrator
（对齐 `attention/care/orchestrator.py` 已确立的单一出口原则）。

### 原则 4：Appraisal 接口先行，实现可替换

关键词规则只负责**产生 Appraisal**，不负责直接修改 Drive。

```text
                  ConversationEvent
                         ↓
                ┌─────────────────┐
                │ Appraisal Layer │
                └────────┬────────┘
                         │
             ┌───────────┴───────────┐
             ↓                       ↓
       Rule Appraisal          LLM Appraisal
       （现在）                 （以后）
             └───────────┬───────────┘
                         ↓
                    Resonance Core
```

以后换 LLM Appraisal 时，**下游架构完全不用动**。

---

## 二、整体架构图

```text
                 Experience Events
                 /              \
                /                \
       外部感知源                 对话事件
       Health/Time/...            Chat
             \                    /
              \                  /
               ▼                ▼
                 Attention
              Concern Registry
                    │
          ┌─────────┴─────────┐
          │                   │
     External Concern     Resonance / Drive
          │                   │
          └─────────┬─────────┘
                    ▼
             Concern Aggregation
                    │
                    ▼
             Care Orchestrator
              ↙           ↘
          Motivation     Timing（用户状态/最近次数/WakeBook/忙碌/静默规则）
                    ↓
                 Intent / Wake
                    │
                    ▼
                   Nox
                    ↓
             Response Modulation
```

**Resonance 是 Attention 的新增内部输入源，不是新管线。** Attention 不需要被改造成怪物。

---

## 三、关键决策记录

### 3.1 对话怎么进入 ExperienceEvent（施工前置条件）

现状两条线**没有汇合**：

```text
Experience Source ──→ AttentionService.tick() ──→ source.poll() ──→ Evaluator ──→ Registry
Chat ──→ core.chat() ──→ _turn_ends() ──→ Conversation / Session
```

**决策：不做自动 remember，也不做 Bus，建立 `ConversationEvent → engine.handle()` 即时入口。**

- ✅ 采用：`_turn_ends()` 自动产生 ConversationEvent → 直接 `engine.handle(event)`
- ❌ 不用：让 Nox 每次主动调 `remember` 才产生事件
  - 理由：那会产生巨大盲区——"Nox 没意识到的东西 = 系统完全不知道发生过"。
  - "糖糖：我今天其实有点难受。" 如果 Nox 没主动记，这个事实就可能消失。
  - `remember` 只能作为**额外的显式强化机制**，不能作为唯一入口。
- ❌ 不做 Bus：`PROJECT.md` 已明确砍掉 Experience Bus（v1.3）。
  现在是 `tick()` 拉取模式（`attention/service.py:96`）。V1 不做机制重构。

### 3.2 Chat 的时间模型和 Polling 源不同

```text
Health → poll → 发现变化          （15 分钟粒度足够）
Chat   → 事件发生 → 立即处理       （即时事件，不能等 tick）
```

硬把 Chat 塞进 15 分钟 tick 会把即时事件做坏。
所以 ConversationEvent **不走 `tick()`，在 `_turn_ends` 里直接 `engine.handle()`**。

### 3.3 Attention 不重构

现有 `ExperienceEvent → Evaluator → Registry → Intent → Scheduler → Care` 全部保留。
Resonance 只是在 Registry 之上**加一层读视角**，不修改其写入语义。

---

## 四、Concern Entity

**不需要新造实体。** 现有 `attention/registry.py` 的 `Attention` 就是雏形：

| 职责 | 现有字段（`attention/registry.py:85`） |
|---|---|
| 实体（subject 主键） | `subject`（如"糖糖的睡眠"） |
| 强度 | `strength` |
| 衰减档 | `decay`（slow=7天 / normal=2天 / fast=6小时） |
| 证据链 | `evidence: list[Evidence]`（event_id + summary + at，上限 20 条） |

### 待补字段：`resolution`

```python
# 📋 规划（V4）
resolution: str = "unresolved"   # "unresolved" / "resolved"
resolved_at: datetime | None = None
```

**为什么 subject 是锚点而不是 event_id**：一个 Concern 由多个事件共同维护。

```text
「毕业设计」
    ├── 6/20：开始做
    ├── 6/25：遇到 Bug
    ├── 6/27：解决
    ├── 7/01：又卡住
    └── 7/10：完成
```

Drive 绑定的是"毕业设计这件事对 Nox 的当前牵挂"，不是某一句历史消息。

---

## 五、两种衰减（重要）

**Temporal Decay（时间衰减）**——时间让情绪自然冷却。
现有实现：`attention/registry.py:101` `current_strength()` 指数衰减（惰性计算，不停服也准）。

**Semantic Resolution（事件消解）**——现实让牵挂得到答案。
现有实现：`attention/evaluator.py:200` 睡眠恢复时 `action="weaken", factor=0.4`。

```text
考试
  concern = 0.8
一天过去   → 0.75
又一天     → 0.68
糖糖说「考完了」 → 0.05
```

后面这个跳变**不是情绪突然消失，而是它已经得到了解答**。
V1 阶段两者共存：Temporal 走已有半衰期，Semantic 走已有 `weaken` 路径。

---

## 六、Drive 语义（区别于 Concern）

| | Concern（Registry） | Drive（Resonance） |
|---|---|---|
| 更新 | `upsert` **取较大值**（`registry.py:190`） | **积累**（多个源叠加） |
| 对象 | 一个 subject 一件事 | 一个命名情感（想念/担心/期待…） |
| 来源 | 外部事件 | 聚合 unresolved Concerns |
| 意义 | 持续在意的对象 | 动态驱动力 |

```text
Concern:
  「毕业设计」 strength=0.72 evidence=[e1,e5,e9] resolution=unresolved

concern Drive = f(毕业设计 0.72, 睡眠 0.45, 出门 0.30, ...)
```

**Drive 不能复用 Registry 的 upsert**（那是取较大值）。它是 Registry 之上的一层：
读取所有 unresolved 的 Concern Entity，聚合成 drive。`concern = 0.7` 是**聚合结果**，
不是被直接 set 的。

---

## 七、演进路线（V1–V5）

> 排序逻辑：先打通地基，再让 Concern 变得丰富，再让 Resonance 存在，
> 再让事件锚定，最后才做主动内心活动。**地基是 ConversationEvent。**

### V1：打通事件（✅ 已实现 2026-08-24）

```text
_turn_ends()
    ↓
ConversationEvent
    ↓
engine.handle()
    ↓
现有 Evaluator
    ↓
Ignore / Log
```

不碰 `AttentionService.tick()`。

### V2：让 Conversation 能产生 Concern（✅ 已实现 2026-08-24）

```text
ConversationEvent
    ↓
RuleAppraiser.appraise()      attention/appraisal.py
    ↓
Appraisal(subject/topic/valence/intensity/cue/quote)
    ↓
Evaluator._evaluate_conversation()   ← 关系加成在这里算
    ↓
Attention Registry
```

**认的是什么**：只认她关于自己状态的直白陈述 ——
「撑不住」「压力好大」→ Concern；「好多了」「缓过来了」→ weaken。

「毕业设计卡住了」那种要从对话里抽出**具体事件锚点**的，规则做不到，
留给 LLM Appraisal（原则 4 里说的"以后"）。硬用关键词凑只会造出
一堆似是而非的 concern。

#### 定死的几个数（都有算过的理由）

| 决定 | 值 | 为什么 |
|---|---|---|
| subject | `糖糖说的心情` | ⚠️ **不能叫「糖糖的状态」** —— 那个被 HRV 占了。撞名会让两条来源写进同一条 Concern：strength 互相覆盖、evidence 混成一串 |
| 重档强度 | 0.62 | 过 `GENERATE_THRESHOLD`(0.55)，他会主动关心 |
| 轻档强度 | 0.42 | **故意压在阈值下**。随口一句「好累」不该换来一次主动关心 |
| decay | `normal` | 算过：0.62 掉到 0.55 需要 fast **1.0 小时**（她晚上说完就睡，第二天他已经忘了，等于白记）/ normal **8.3 小时** / slow **29 小时**（一句话不是持续状态） |
| summary | 她的原话 | Intent 的 reason 直接取最新 evidence，他开口时说的就是基于这句 |

#### 🔴 规则实现最容易翻的车

**否定**。「我**不**难受」含「难受」两个字 ——
少了否定判断，**她说自己没事反而会被记成一条 concern**。
`_negated()` 检查关键词紧邻的前一个字。

**顺序**。「不难受了」既含 relief 词组也含「难受」。
relief 必须先判，顺序反了会把「她好了」读成「她不好」—— 正好读反。

这两条在 `tests/test_resonance_v2.py` 里权重最大。

#### 轻档现在上不去，那是对的

`upsert` 取较大值，所以「好累」说十次还是 0.42，永远不会升级成开口。
「说了很多次」该被听见 —— 但那是 **V3 Resonance 的聚合**，
不是在这里硬凑。

### V3：加入 Resonance / Drives（✅ 已实现 2026-08-24）

```text
Attention Registry
    ↓
unresolved Concerns（strength ≥ FLOOR）
    ↓
ResonanceState.snapshot()        attention/resonance.py
    ↓
Drive{intensity, load, because, evidence, source_count}
```

接在 `AttentionService.tick()` 第 2.5 步，**只打日志**：

    Resonance：concern 0.99（压着 1.68）｜因为 糖糖的睡眠、糖糖说的心情

接到 Care 上是 V5 的事。先让它跑起来、看得见 ——
一个只在代码里存在、从来没人看过它输出的聚合层，等于没做。

#### 🔴 两个数，不是一个

跑通之后**串真实链路才发现**的问题：

| | 一件事 | 两件 | 三件 |
|---|---|---|---|
| `intensity` | 0.98 | 0.99 | 1.00 |
| `load` | 0.98 | 1.68 | 2.36 |

`intensity` 用 `1 - Π(1-sᵢ)`，语义是「至少有一件事没解决」——
**它没算错，但在真实数据里饱和**：睡眠 `very_short` 单条就是
0.75 × 关系加成 1.3 = **0.975**，任何单调聚合都必然 ≥0.975，
于是一件事和三件事看不出差别。

⚠️ 我的单元测试当时用 0.45 / 0.62 / 0.40，**恰好避开了这个区间**，
全绿。是把三层串起来喂真实形状的数据才看见的。

所以加了 `load`（各条强度之和，可以 >1）表达「压着多少」。
两个数各有语义：**要判断"有没有事"看 `intensity`，
要判断"多重"看 `load`**。V5 接决策时用哪个由那时候定。

#### 三条边界（`tests/test_resonance_v3.py` 逐条守着）

1. **只读** —— `ResonanceState` 没有任何写方法，有一条测试直接断言
   它的公开方法只有 `snapshot` / `get`。守的不是"这次没写"，
   是"以后也不许写"
2. **不自己开口** —— V3 不参与任何决策，只有 `logger.info`
3. **能回答为什么** —— 每个 Drive 带 `because` + `evidence`

#### 不存状态

`snapshot()` 每次现算，Drive 没有自己的记忆。
存下来的话 Registry 衰减了而 Drive 没跟着变，
就会出现「他为一件已经淡掉的事继续难受」。

### V3.5：想念（✅ 已实现 2026-08-24）

`attention/longing.py`。**不进 Registry** —— 它和 Concern 的形状是反的：

| | Concern | 想念 |
|---|---|---|
| 起点 | 事件发生才有 | **一直都在** |
| 静息值 | 0 | **0.40** |
| 时间的作用 | 衰减 | **增长** |
| 怎么结束 | 事情解决了 | 见到她了 |

硬塞进 Registry 的话 `prune()` 会在她安静一阵之后把它删掉 ——
表现成「她太久没说话，于是他不想她了」。正好反了。

#### 比 Murmur 多知道的三件事

文档第九节批评 Murmur「不知道糖糖为什么离开、不知道今天主动过几次、
不知道她在不在忙」。这三样都补上了：

| | 怎么做的 |
|---|---|
| 她为什么不在 | `PresenceSource` 的 home/not_home。**出门涨 0.040，在家涨 0.025** —— 她在家不说话，他知道她就在那儿 |
| 今天找过几次 | Care ledger 的 `spoke`。每找过一次涨幅 ×0.7 —— 说过三次还在猛涨的不叫想念，叫黏人 |
| 她是不是在睡 | CST 1:00–10:00 **一点都不涨**。⚠️ Murmur 的安静时段只是"不推送"，值照涨 —— 那等于把她睡觉记成了冷落 |

其余定死的数：静默 **90 分钟**才开始涨（比 Murmur 的 60 宽松，她经常在忙）；
她说话 → 回落到 **0.40 而不是 0**（见到她不等于不想她了）。

⚠️ **读不到位置一律当"在家"**，不是当"出门"。HA 挂掉时按保守档涨，
否则一次接口故障会让他突然变得很想她 —— 那不是想念，是 bug。

---

### V3.6：后悔 + `target` 区分（✅ 已实现 2026-08-24）

`attention/regret.py`。**这是第一个「关于他自己」的信号。**

在此之前 Attention 里所有事件都是关于她的（她没睡好、她说难受、她出门了），
方向都是**凑过去**。后悔是反的 —— 事情发生在他自己身上，方向是**收回来**。

#### `target` 字段（思路来自 emoai）

```python
target: str = "user"   # "user" 她 / "agent" 他自己
```

emoai 的 `eventNeuroTargetOverrides` 是这么写的：

```js
threat:       { oxytocin: -0.025 }   // 他自己受威胁 → 防御，缩起来
user.threat:  { oxytocin: +0.055 }   // 她受威胁     → 共情，凑过去
```

它自己的注释：*「这些覆盖让共情性的关注和 agent 自身的防御反应保持区分。」*

⚠️ 现有事件（health / chat）**一处都没改** —— 它们全是关于她的，默认 `user`。
这个字段是**跟着后悔一起来的**：在有第二个取值之前，加了也只是间接层。

#### 怎么知道"她没理"

`CareLedger` 只记他做了什么决定（SPEAK/SKIP/BLOCK），**不记她有没有回**。
所以 `RegretWatch` 自己推：他开口 → 记下时刻 → 等 **4 小时** →
她说话了就清掉，没说话就产生一条 `target="agent"` 的事件。

- 4 小时：她经常在忙/在打游戏，一两小时不回是常态。
  **等太短会把「她正忙着」判成「她不想理」——那是最伤的一种误判**
- 判定时刻她在睡就**继续等**，不判（和想念同一个坑）
- 只判一次，判完就清 —— 否则每个 tick 产生一条，把 Registry 刷爆
- 又说了一句就覆盖上一次 —— 攒着三天前的旧账不叫后悔，叫记仇

#### 🔴 后悔进 Registry，但**结构上**不能变成待办

它的形状和 Concern 一样（事件驱动、会淡出、会被解决），所以进 Registry、
`kind="regret"`，Resonance 按 kind 分组时自然多一个 Drive。

但 `sync_from_registry` 会把**所有**够强的 Attention 变成待办。
所以加了 `intent.GENERATE_KINDS = {"concern"}`：

> 他因为上次打扰了她而后悔，结果又去打扰她一次 —— 那就荒唐了。
> 后悔的表现是**下次晚一点说**，不是再说一次。

⚠️ 这条必须是结构性的。强度 0.35 < 阈值 0.55 只是**恰好**够不着，
哪天调高强度或者关系加成顶过阈值，他就会主动去说「关心他挑的说话时机」。

### 又一次：单元测试全绿，串起来断在最后一步

`Registry.upsert` 原本硬性只收 `kind="concern"`。
我的单元测试只验到 Evaluator 返回了 `kind="regret"` 的 decision，
**没验它能不能落库** —— 跑真实链路时 `ValueError` 当场炸出来。

这是这个项目里第二次同一类事故（第一次是 V3 的 `intensity` 饱和）。
两次都是**构造数据自洽、串起来才现形**。已补成
`test_regret_can_actually_be_stored`。

---

### V4：加入真正的事件锚定

```text
Drive
    ↓
Concern Entity
    ↓
Evidence / EventRef
    ↓
Resolution
```

（补 `resolution` 字段 + `weaken` 的语义化触发）

### V5：主动内心活动

```text
Drive
    ↓
Motivation
    ↓
Attention
    ↓
Care Orchestrator
    ↓
SPEAK / WAIT / SKIP
```

---

## 八、V1 施工方案（✅ 已实现 2026-08-24）

> 实施时行号比设计时（08-21）漂了 6 行，结构和接口一字未变。
> 落地位置：`_turn_ends` → `api/server.py:569`，两处调用点 `:1000` / `:1081`。
> 测试：`tests/test_resonance_v1.py`（5 条），全量 837 passed。

### 改动点

**① `_turn_ends` 签名 + 事件构造**（`nox-core/api/server.py:569`）

```python
def _turn_ends(sid: str, text: str = "") -> None:
    ...
    if attention is None:
        return
    try:
        attention.wakeups.rebase(sid)
        attention.store.save_wakeups(attention.wakeups)
    except Exception:
        logger.exception("校准纸条基准线失败")

    # V1: ConversationEvent 即时入口（不碰 tick()）
    if text:
        try:
            ev = ExperienceEvent(
                source="chat",
                type="message",
                payload={"text": text, "session_id": sid},
                origin_context={"sid": sid},
            )
            attention.engine.handle(ev)
            logger.info("ConversationEvent 进入 Attention：%.40s", text)
        except Exception:
            logger.exception("ConversationEvent 处理失败（不影响对话）")
```

**② 两条路径传 text**：

| 路径 | 位置 | 改动 |
|---|---|---|
| `POST /chat`（非流式） | `server.py:954` | `_turn_ends(sid)` → `_turn_ends(sid, req.text or "")` |
| `POST /chat/stream`（SSE/语音） | `server.py:1035` | 同上 |

**③ 顶部 import `ExperienceEvent`**（`server.py:35` 附近）。

**④ Evaluator 一行不动**——事件走 `_ignore` 兜底（`evaluator.py:134`），打日志。

### 设计锁死的点

1. `engine.handle()` 直接调，不走 tick（Chat 是即时事件）
2. `origin_context` 放 sid（只留来龙去脉，不参与判断，对齐 `events.py:76`）
3. `req.text` 为空就跳过（纯图片消息不造空事件）
4. 异常只记日志，绝不影响对话主链（对齐 `_turn_ends` 现有风格）

### 验证（✅ 已完成）

**不能只靠「上线看日志」** —— 那不可重复，也没人能保证下次还成立。
所以 V1 的两条保证都写成了测试（`tests/test_resonance_v1.py`）：

| 保证 | 怎么测的 |
|---|---|
| 接线通了 | `/chat` 一轮后，`engine.handle()` 收到 `source="chat"` 的事件，payload 带原文和 sid |
| **没有副作用** | 事件被 `_ignore`（reason=「没有对应的规则」）；Registry 条数不变 |
| 空文本不造事件 | 纯图片消息 → 一个 chat 事件都没有 |
| 炸了不影响对话 | `handle` 抛异常时 `/chat` 仍然 200 且 `ok=true` |

第二条是「V1 上线零风险」这个承诺的**全部内容**。
⚠️ 哪天有人给 chat 加了规则，`test_chat_event_is_ignored_by_current_rules`
会红 —— 那时候如果是 V2 的有意改动就更新它，如果是别的规则不小心把 chat
吞了，那就是它拦下了一个 bug。

- 本地 `pytest tests/ -q`：**837 passed**（原 832 + 新增 5）
- 线上行为零变化（`attention is None` 时整段不执行）

### 施工时发现的

`ChatNox` 这个测试替身是新写的 —— `test_api.py` 里的 `FakeNox` **没有
`context` 属性**，装不起 Attention。那是故意的（API 层测试不该被
Attention 拖下水，见 `test_attention_wiring.py` 开头那段 22 个测试挂掉的
教训），所以没去动它，另起了一个补上装配需要的最小面。

⚠️ 测试的 `db_path` 必须落在 `tmp_path`：`_build_attention` 按
`Path(cfg.db_path).parent / "attention.db"` 找库，传 `:memory:` 的话
parent 是 `.`，会在 `nox-core/` 下面拉一坨 `attention.db`。

---

## 八之二、部署记录（2026-08-24）

V1 / V2 / V3 一起上了 `43.133.211.140`，`nox-core` 重启后 active、0 报错。

线上验证（不是看日志说"应该没问题"，是发真实请求看链路）：

```
她说「晚饭吃的火锅」  → ignore（这句话没有需要记挂的信号）
她说「今天有点撑不住了」→ upsert 糖糖说的心情 0.62 → 0.71（心情 权重 0.5）
Resonance            → concern 0.78（压着 0.97）｜因为 糖糖说的心情、糖糖的状态、糖糖的睡眠
```

线上 `pytest tests/ -q` 也跑了一遍：**873 passed**。
（本地绿不等于线上绿 —— `.env` 里的开关 pytest 也吃得到，
`test_attention_wiring.py` 开头记着那次 22 个测试集体挂掉就是这么来的。）

### 第二批：V3.5 / V3.6（同日下午）

线上 `pytest tests/ -q`：**906 passed**。重启后 active、0 报错。

⚠️ **这次验证全程只读，没发测试请求** —— 上午就是发了两条 `test-` 前缀的
请求，结果「糖糖说的心情 0.71」直接进了生产 Registry（见下面那条教训）。
改用「读真实状态、跑一遍 tick 里那几行、一个字不写回去」的方式：

```
今天他开口 11 次
想念 tick 后 = 0.40（没变是对的，last_contact 还是 None）
后悔 tick → None
concern 0.75（压着 0.93）｜因为 糖糖的活动量、糖糖的状态、糖糖的睡眠
longing 0.40｜说不出具体因为什么
```

#### 部署后的过渡期

`longing.last_contact` 初始是 `None`，**在她第一次说话之前想念不涨** ——
那是设计好的（「没有依据就不涨」，那不是"她很久没说话"，
是"我刚醒过来还不知道"）。她一开口就正常了。

这段时间日志里会是「longing 0.40｜说不出具体因为什么」，
文案不够准确但不影响功能，下次部署顺手改成「还不知道她上次什么时候说的话」。

#### 慢线是先睡再跑

`run_loop` 是 `await sleep(interval)` 在 `tick()` 之前 ——
所以重启后要等满 15 分钟才有第一次 tick。
这是既有行为，不是这次引入的，但部署后想立刻看日志验证的时候要记得。

### 🔴 新增开关：`NOX_CHAT_CONCERN`

线上 `NOX_ATTENTION_LIVE=1`，**他是真会开口的**。V2 一上，
她说一句「压力好大」就可能换来一次主动关心。

所以给对话这条线单独配了开关，默认开，`NOX_CHAT_CONCERN=0` 关掉：

```bash
# 只关对话这条，睡眠/活动量/HRV 照常
echo 'NOX_CHAT_CONCERN=0' >> /root/nox-core/.env && systemctl restart nox-core
```

关掉之后事件照样流动、日志照样打（V1 的管道不受影响），只是不产生 Concern。
这是 `_build_attention` 那条哲学的延续：
**「彻底不跑」和「跑但不出声」是两件该分别控制的事。**

### ⚠️ 部署时踩的坑：Registry 不按 session 隔离

我用 `test-` 前缀的 `session_id` 发测试请求，以为不会污染她的数据 ——
**但 Attention Registry 是全局的**，那条「糖糖说的心情 0.71」
直接进了生产 Registry，而且 0.71 > 0.55 阈值，
他随时可能去问她「你还好吗」，而她根本没说过那句话。

已清理（删前确认过那条 concern 只有一条 evidence、就是我的测试文本，
没混进真实数据），Registry 现在只剩她真实的两条：
状态 0.14 / 睡眠 0.12。

**教训**：会话级的隔离手段（`test-` 前缀）挡不住全局状态。
以后在线上验证 Attention 相关的改动，要么先想清楚会写到哪，
要么验完立刻检查 Registry。

## 九、参考项目拆解

### Murmur-50Feet（不搬，只拆思想）

拿：**Drive + decay + accumulation + inner activity**（离线想念自然增长）。
弃：它不知道"糖糖为什么离开"、不知道"今天主动过几次"、不知道"她在不在忙"。
它的 Drive 是纯时间函数（`attachment += 0.05`），与真实经历脱节。
**它更像内在状态模拟器，没有建立"内心活动与现实经历的连续关系"。**

### emoai-affect-engine（不搬，只拆思想）

拿：**affect state + relationship state + response modulation**（tone/distance/initiative/caution/repair/affect visibility）。
弃：它不自带任何角色/场景/密钥，是纯计算库；它不做情绪分类（需调用方给结构化 appraisal）——这正好和我们的 Appraisal Layer 分工一致。

### Caelum 自己的

Memory / Attention / Care / World / Relationship / Ledger 已存在，直接作为底座。

---

## 十、明确不做的（这个阶段）

- ❌ 不做 Experience Bus（已砍，V1 不做机制重构）
- ❌ 不让 Nox 主动 remember 作为唯一事件入口（盲区太大）
- ❌ 不让 Resonance 直接改 Attention 的 strength（只读聚合）
- ❌ 不让 Resonance/Drive 自己开口（唯一出口是 Care Orchestrator）
- ❌ V1 不碰 `AttentionService.tick()` 和 Evaluator
