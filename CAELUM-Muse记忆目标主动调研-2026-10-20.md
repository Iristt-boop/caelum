# Muse 的记忆 / 目标 / 主动开口 —— 调研（2026-10-20）

> 起因：糖糖深度用了几天 Meta Muse，觉得它的 personal agent 做得好，Caelum 的记忆和主动开口差得远；她说自己看到的片面，要我上网细查。
> 前作：`CAELUM-云电脑Agent调研-2026-10-14.md`（GLM 写、我核过）只查了「云电脑」那一面，记忆只有一句「194 表 Postgres」。这份专查**记忆、目标、主动、Feed**。
> 可信度标注：**官方** = Meta / OpenClaw 官方文档；**拆解** = 第三方拆 VM 的技术文章（单一来源）；**报道** = 媒体；**二手** = 第三方指南（可能不准）。

---

## 〇、一句话结论

Muse 的「舒服」不是某个功能做得好，是**三件事分开了**：

1. **记忆分三层，而且「关于你」那层是被改写的，不是被追加的**——了解到新东西，改那一行；每晚有一次「做梦」式的整理，把当天的流水合并进去、把过时的划掉。
2. **「目标」是一个独立的东西**——你说一句话，它判断要不要立项；一个目标一个文件夹，带计划、自己的定时任务、工作笔记，能活几天到几个月。
3. **主动开口有高门槛、而且是结构性的防唠叨**——定期醒来的那一下默认是「没事就不说」（回 `NO_REPLY`），提示词里明写「不要从以前的聊天里推断或重复旧事」；按事件触发的提醒自带冷却、次数上限、过期时间。

Caelum 现在三样都挤在一起：记忆是一锅、没有「目标」、主动开口靠计时器 + 配额。

---

## 一、Muse 是什么（只说和这次有关的）

- Meta 2026-09-08 发布的个人 agent，每人一台云端 Linux 虚拟机（文件系统、终端、浏览器），模型 Muse Spark。**官方** [ai.meta.com/muse](https://ai.meta.com/muse/)
- 界面：一个主聊天 + 侧聊天，外加 **Feed、Ideas、Goals、Library** 几个页面。**报道** [BuildFastWithAI](https://blog.buildfastwithai.com/muse-personal-ai-agent-review)
- 它的文件布局和开源项目 **OpenClaw** 几乎一样（SOUL.md、MEMORY.md、memory/ 日记…），Meta 承认「不是巧合」但说代码不是拿来的。**报道** [TechCrunch](https://techcrunch.com/2026/09/22/meta-admits-muses-likeness-to-openclaw-isnt-a-coincidence/)
  → 所以下文凡是 Muse 没公开的机制，**拿 OpenClaw 的官方文档当参考实现**，并标明。

---

## 二、记忆

### 2.1 Muse 自己公开的

| 文件 | 装什么 | 谁写 |
|---|---|---|
| `Soul.md` | 核心原则、边界、性格（对话方式） | 用户可直接改，也可让它改 |
| `Identity.md` | 名字、「物种」、气质、一句话签名 | 同上 |
| `Memory.md` | **关于你的事实**：偏好、兴趣、生平 | 它从聊天里记；用户可改可删；可以叫它「忘掉」某件事 |

**官方** [Meta 帮助：管理 Muse 数据](https://www.meta.com/help/artificial-intelligence/2225571704857152/) · **二手** [agent-tune](https://agent-tune.com/guides/what-is-meta-muse)

它理解你的四个来源：你直接说的、它观察到的偏好和规律、连接的应用（日历邮件…）、文件记忆。

### 2.2 拆开 VM 看到的（**拆解**，单一来源：[Rohan Adwankar](https://rohanadwankar.github.io/posts/sandbox2.html)）

```
/home/hatch/
├── SOUL.md  IDENTITY.md  USER.md  MEMORY.md  AGENTS.md  TOOLS.md
├── PROACTIVE_PREFERENCES.md        ← 「每天编今日刊前整份读一遍」
├── memory/                         ← 按日期的日记 + bank/{world,experience,opinions}.md
├── workspace/cron.d/{minutely,hourly,daily,weekly,…,runonce}
├── workspace/goals/<目标名>/{GOAL.md, briefs, crons, agent_notes, files, references}
└── workspace/{feed, scheduler}
```

另有一个 194 张表的 Postgres：`memory.*`（entries / **claims** / entry_attributes / embeddings，384 维向量 + 全文检索）、`goals`、`feed`、`ideas`、`scheduler`、`health`、`self_improvement`……
还有一条离线的**自我改进流水线**：评判 → 反思 → 修复 → 提炼 → 泛化，「离线给自己的行为打分、改写」。

> `bank/{world, experience, opinions}` 出自 OpenClaw 的一份**实验性**研究稿（不是它默认的做法）：世界事实 / 我做过的事 / 带置信度的看法分开存，每条看法带证据指针、随证据涨落。**官方（实验稿）** [OpenClaw Workspace Memory v2](https://openclaws.io/docs/experiments/research/memory/)

### 2.3 参考实现：OpenClaw 的记忆分层（**官方** [memory-architecture](https://docs.openclaw.ai/concepts/memory-architecture)）

| 层 | 文件 | 谁写 | 怎么更新 | 什么时候进对话 |
|---|---|---|---|---|
| 指令 | AGENTS.md 等 | 只有人 | 原地改 | 每次都在 |
| **核心（精选）** | `MEMORY.md`、`USER.md` | 夜间整理；用户要求 | **整理时重写、原地改** | 开场就在，**有字数预算** |
| 经历 | `memory/YYYY-MM-DD.md`、聊天原文 | 干活时；压缩前的「抢救」 | 只追加 | **不开场注入**，要用时检索 |
| **前瞻** | 常驻意图（SQLite）、定时任务 | intent 工具；定时器 | 状态机：待命→上膛→触发 | **只在触发时** |
| 复盘 | `DREAMS.md` | 夜间整理 | 追加 | 从不注入，只给人看 |

关键规则（原文要点）：
- **`USER.md` 是用户模型**：稳定偏好、说话方式、人际关系、在做的项目；每条写成「总是 / 从不 / 倾向」的指令，带日期和「生效 / 已被取代」。**偏好变了就原地改写那一条，从不追加**。
- **`MEMORY.md` 是精选的事实**，「不是聊天记录、不是日记、不是穷尽的档案」。
- **夜间整理（dreaming）**：先按规则打分（相关度、频率、来源多样性、新近、反复出现、信息量），过线的再交给一次**不带工具**的模型调用，决定**合并**哪些、**取代**哪些；写之前留一份「改之前的样子」在 DREAMS.md 里给人看。整理后不许超过字数预算，也不许一次丢掉太多旧条目。
- **出身闸门**：外部内容、系统提示词出身的候选**结构性地进不了核心**；定时任务、心跳、子 agent 那些会话里产生的东西也不往核心升。
- **压缩前抢救**：上下文要被压缩之前，先跑一个看不见的轮次，让它把还没落盘的要紧事写进当天日记。**官方** [memory](https://docs.openclaw.ai/concepts/memory)

### 2.4 报道里的「夜间分析」（**报道** [TIME](https://time.com/article/2026/10/06/meta-muse-ai-agent-privacy/)，Meta 未否认）

- 每个 Muse 每晚分析当天和用户的对话。
- 推断用户的目标，**包括没说出口的**；也推断行为规律，例：「这个用户晚上 10 点后对简短的提醒反应更好」。
- 记住「梗、印象深的说法、共同语境，让关系有连续感」。
- ⚠️ 也有我们**不该学**的：给用户的联系人建「关系档案」（谁和谁有矛盾、结盟），连没用 Muse 的人也被画像；各台 VM 之间去名字后共享「说服技巧」。

---

## 三、目标（Goals）

- **从聊天里长出来**：你说一个大的意图，它判断值得长期跟，就立一个目标；目标页和聊天同步，在哪边推进都行。**官方** [How We Designed Muse](https://introducing.muse.ai/)
- **一个目标一个文件夹**：`GOAL.md` + 简报 + **这个目标自己的定时任务** + 工作笔记 + 文件 + 参考资料。「目标工人」各自有派活和通知的步骤。**拆解**
- **分类**：健康 / 关系 / 财务 / 职业 / 兴趣 / 效率 / 其他。**状态**：「等你说想改变什么」（还缺信息）/ 进行中。立项时问清「目标是什么、什么算进展」，拆成几步，**提醒节奏由用户选**。**二手**（一个照抄 Muse 的开源项目的需求单：[hushh #7251](https://github.com/hushh-labs/hushh-research/issues/7251)）
- **后台推进**：按时间或按事件；进展来自用户自己报（称体重）或连接的数据（日历、邮件、健康）。
- 测评实例：「从零跑马拉松」→ 24 周计划，并提醒「跨度太大，先问问医生」。**报道** [NoCode.MBA](https://www.nocode.mba/articles/meta-muse-ai-review)

糖糖用到的三件（浇水每三天提醒、减脂训练 + 到点问训练和饮食、每日新闻推送）在 Muse 里**都是同一种东西**：一个目标 + 它自己的触发规则。

---

## 四、主动开口

### 4.1 Muse 的原则（**官方** [How We Designed Muse](https://introducing.muse.ai/)）

> 「主动消息的门槛必须高：它得真的有用、值得打断你。默认档对大多数人是对的；你随时可以让你的 Muse 关掉、调少、调多。」

- 后台做完事，**它自己判断这个结果值不值得通知你**：只有「真有新东西」或「需要你拿主意」才发。**报道** [TestingCatalog](https://www.testingcatalog.com/meta-introduces-muse-as-a-proactive-personal-agent/)
- 有活动日志（做了什么、拿了什么权限），敏感动作出确认卡。
- 参照：Meta 2025 年给聊天机器人定的「主动回访」规矩——只回访**用户先发起过**的对话、14 天内用户问过至少 5 个问题、**不发重复消息**。**报道** [TechCrunch 2025](https://techcrunch.com/2025/07/03/meta-has-found-another-way-to-keep-you-engaged-chatbots-that-message-you-first/)

### 4.2 参考实现：OpenClaw 的三种「主动」（**官方**）

| 机制 | 什么时候醒 | 醒了做什么 | 防唠叨 |
|---|---|---|---|
| **心跳** [heartbeat](https://github.com/openclaw/openclaw/blob/main/docs/gateway/heartbeat.md) | 默认每 30 分钟，可设活跃时段 | 读一份**很短的检查单**（HEARTBEAT.md），有事才说 | 默认提示词原文：「**不要从以前的聊天里推断或重复旧事。没什么需要注意的，就回 NO_REPLY。**」；只回「OK」的会被吞掉 |
| **定时任务** [automations](https://docs.openclaw.ai/automation/cron-jobs) | 准点 | 在**独立会话**里干一件事 | — |
| **常驻意图** [standing intents](https://docs.openclaw.ai/concepts/standing-intents) | **某件事发生时**（她提到某个关键词等），不是钟点 | 把这条意图悄悄塞进那一轮的上下文 | **结构性**：默认冷却 24 小时、最多触发 3 次、90 天过期、一轮最多带 3 条；匹配走规则，**不调模型** |

糖糖举的「问了猫砂比价 → 过一会儿提醒 8 点下单前要记得的事」，就是「一个小目标 + 一个准点任务」。

---

## 五、Feed 与 Ideas

- **Ideas**：根据「你的目标、你的规律、聊天里学到的」生成建议；刚上手时当提示，平时在 Ideas 页，知道你的目标时会主动分享。**官方** [How We Designed Muse](https://introducing.muse.ai/)
- **Feed / 今日刊**：编之前整份读 `PROACTIVE_PREFERENCES.md`（主动推送的偏好）。**拆解**（这一块公开细节最少）
- 糖糖观察到的「聊到绿植 / 健身，Feed 就推相关内容」= 兴趣来自关于她的档案和目标，不是固定来源。

---

## 六、对照 Caelum 现状（2026-10-20 实查）

| | Muse / OpenClaw | Caelum 现在 | 证据 |
|---|---|---|---|
| 我是谁 | Soul.md + Identity.md | 人设提示词 + OB 里 22 条**钉选桶**（世界观、求婚、哲学判决等事件混在里面） | 10-18 导出的前缀 |
| **关于她** | **USER.md / Memory.md：一份被改写的档案** | **没有**。散在几百个记忆桶里；`relation_book`（别问 / 上心 / 约定）是最接近的一小块 | — |
| 经历 | 日记只追加、按需检索 | OB 动态桶，每天**新增** 6~8 条 | 10-20 00:35 一次写进 6 条（夜间归档），03:33 两条梦 |
| 夜间整理 | 合并 + 取代，有预算，留改前版本 | `ombre-brain-consolidate`（每晚 00:31，09-23 上线、live）：白天每轮抽的一句话候选 → 夜里按主题合成**最多 6 段**叙事记忆 → 逐段「相似就合并、否则新建」。只防**今天**的重复（跳过今天他亲手存的）和几乎一样的句子；**跨天的同一件长期事实，换个说法就长新桶** | 「她在记热量」10-13 / 10-16 / 10-19 各一条；2.5D 形象 10-15 / 10-16 各一条；**梦一晚两条**：`attention/dream.py` 调的是 `grow`，OB 会把一段话拆成多条（内容一条、感受一条） |
| **目标** | goals/ 一个目标一个文件夹，带计划和自己的触发 | **没有**。零件散着：待办、纸条（到点叫醒他）、长任务循环、话题池 | — |
| 主动开口 | 高门槛；心跳默认「没事不说」；事件意图带冷却和次数上限 | 十几条链 + 随机惦记计时器（20~90 分钟）+ 每日配额；手里没具体的事也要「想想说点什么」 | 10-19 腰疼后每条都问腰；10-17 一天惦记 27 次 |
| Feed | 兴趣来自档案和目标 | 话题池从固定来源捞 | — |
| 自我改进 | 离线流水线 | Growth Loop（P1 生效、P2/P3 shadow，P6「他的习气」未做） | — |

---

## 七、能借鉴的 / 不照搬的

**借鉴**
1. 记忆**三层**，而且「关于她」那层**改写不追加**、有字数预算、改前留底给她看。
2. 夜间整理从「把今天拆成几条追加」改成「**先更新档案；档案里装不下的新事件才追加**」，合并和取代由一次不带工具的整理调用决定。
3. **目标**作为一等公民：从聊天里长出来，一个目标一份文件（计划、节奏、进展、它自己的触发），在页面上看得见、改得动。
4. 主动开口**默认没事不说**；事件提醒自带冷却 / 次数 / 过期（「每句都问腰」就是缺了这个）；她能说「调少点 / 调多点」。
5. 定时器里的会话、主动开口产生的东西**不往核心记忆升**（防止他把自己说过的话当成关于她的事实）。

**不照搬**
- 我们是陪伴不是助理：**「想你了」这种没具体事由的开口要留**，只是要轻、要少、不复读同一件事。
- 不推断「她没说出口的目标」去替她立项——可以**提议**，她点头才立（和配置层 P5「我提议、你点头」同一个口径）。
- 不给她的朋友建关系档案；不做跨用户的「说服技巧」学习。

---

## 八、建议的方向（等她拍板再写设计稿）

| 顺序 | 做什么 | 为什么排这儿 |
|---|---|---|
| 1 | **记忆重组**：「关于糖糖」档案（改写型）+ 夜间整理改成「先更新档案、再追加新事件」+ 梦一晚一条 | 地基：目标要读写她的情况，主动开口和 Feed 都要从档案里取 |
| 2 | **目标层**：从聊天里提议立项 → 她点头 → 计划 + 节奏 + 触发；现有纸条 / 长任务 / 待办能收进来的收进来 | 解决「我发起 → 执行 → 结束」没有闭环 |
| 3 | **主动开口重排**：目标和事件驱动为主，「想你」为辅且轻；事件提醒带冷却和次数上限 | 依赖目标层；也顺带解决复读 |
| 4 | **Feed**：兴趣从档案和目标里来 | 依赖 1、2 |

**要她定的**
1. 「关于糖糖」档案她要不要能直接看、直接改（Muse 是可以的）？
2. 目标是「他提议、她点头才立」，还是像 Muse 那样他判断了就立、她事后能删？
3. 「想你」类的无事由开口，她希望一天大概几次？（这个数字以后由她说了算，而不是我们来回调闸门）

---

## 来源

- 官方：[Muse 产品页](https://ai.meta.com/muse/) · [How We Designed Muse](https://introducing.muse.ai/) · [Meta 帮助：管理 Muse 数据](https://www.meta.com/help/artificial-intelligence/2225571704857152/)
- OpenClaw 官方：[Memory architecture](https://docs.openclaw.ai/concepts/memory-architecture) · [Memory overview](https://docs.openclaw.ai/concepts/memory) · [Standing intents](https://docs.openclaw.ai/concepts/standing-intents) · [Heartbeat](https://github.com/openclaw/openclaw/blob/main/docs/gateway/heartbeat.md) · [Automations](https://docs.openclaw.ai/automation/cron-jobs) · [Workspace Memory v2（实验稿）](https://openclaws.io/docs/experiments/research/memory/)
- 拆解：[Rohan Adwankar：Muse VM 拆解](https://rohanadwankar.github.io/posts/sandbox2.html)
- 报道 / 测评：[TIME：Muse 在给你建档](https://time.com/article/2026/10/06/meta-muse-ai-agent-privacy/) · [TestingCatalog](https://www.testingcatalog.com/meta-introduces-muse-as-a-proactive-personal-agent/) · [TechCrunch：和 OpenClaw 的相似](https://techcrunch.com/2026/09/22/meta-admits-muses-likeness-to-openclaw-isnt-a-coincidence/) · [TechCrunch 2025：主动发消息的聊天机器人](https://techcrunch.com/2025/07/03/meta-has-found-another-way-to-keep-you-engaged-chatbots-that-message-you-first/) · [NoCode.MBA 测评](https://www.nocode.mba/articles/meta-muse-ai-review) · [BuildFastWithAI 测评](https://blog.buildfastwithai.com/muse-personal-ai-agent-review)
- 二手：[agent-tune：Muse 的文件](https://agent-tune.com/guides/what-is-meta-muse) · [hushh-research #7251（照抄 Muse 的 Goals 需求单）](https://github.com/hushh-labs/hushh-research/issues/7251)

> 没查实的：Muse 目标的 `GOAL.md` 具体字段、`feed` 的生成规则、`PROACTIVE_PREFERENCES.md` 的内容（拆解文章只给了路径）；Muse 是否真的用 OpenClaw 式的「常驻意图」（推断，未证实）。网上没找到用户抱怨 Muse 主动消息太多的帖子（Reddit / 推特这次没接上，没法查）。
