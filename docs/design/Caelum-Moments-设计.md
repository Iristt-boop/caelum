# Caelum Moments —— Nox 的自我表达层（v1）

> 糖糖 2026-09-14 提，2026-09-15 派工实现。糖糖统筹验收。
> 动手前先读 `CLAUDE.md` → `CAELUM-MAP.md`（**尤其第三·五节验证纪律**）。

## Context

糖糖 2026-09-14 提的：现在的日记太单调，两个人都总是忘记写。她想要一个
**他自己的空间**——不是对着她讲，而是自言自语；有感想、有发现、有心情；
时机随机但有理由；两个人可以互相评论。

### 🔴 为什么这个功能属于 CAELUM（探索时发现的，不是设计出来的）

系统里**已经有一整层「够不着开口阈值」的内心状态，每一条都是故意压下去的**：

```
attention/playfulness.py:24        「她心情好不该换来一次[开口]」
attention/regret.py:31             「故意压在开口阈值之下」
attention/sources/curiosity.py:36  ≤0.45，「他对一篇论文好奇，不该变成一次打扰」
attention/appraisal.py:223         规则版强度**全部**压在 GENERATE_THRESHOLD(0.55) 之下
```

线上实测（`/api/nox/resonance`，2026-09-14）：
`longing 0.4` · `playfulness 0.28` · `regret 0.167` —— **全都够不着 0.55。**

每一条被压低的理由都一样：**不该变成一次打扰她**。

所以 Moments 不是「多一个功能」，是**给这层内心状态一个不打扰的出口**。
它不跟开口抢配额，它接的是掉在阈值底下的那些。

---

## 一、边界：先回答 CAELUM-MAP 的三问

**属于哪层？** 跨三层，必须写进地图：

| 层 | 放什么 |
|---|---|
| 思考 | `PostImpulse` —— 「他现在想不想发一条」 |
| 行动 | 生成正文 + 落库（**不经 speaker、不推送**） |
| 表现 | **新页面** Moments（微信朋友圈样式） |

**谁消费它？** 糖糖打开 App 就看得见。不是「以后可能用」。

**闭环完整吗？** 内心状态 → 冲动 → 生成 → 落库 → 她看见 → 她评论 →
他回复（**最后一半现有 `triggerAiComment` 已经跑通**）。

### R1 不变，但要加一条新法则

R1 管的是**主动开口**（`push/send` + CareLedger）。发帖不推送、不弹锁屏，
**不是开口**，所以不走 `Orchestrator._deliver`（那函数注释写着「真的开口那一下」）。

但 R1 的精神要守——「任何『他为什么说话』必须能沿账本溯源」。
发帖**照样记账本**，`source="moment"`（照抄 `service.note_external_speech` 的做法）。

🔴 **新增 R10 + 哨兵：朋友圈不许推送。**
产出路径里出现 `push/send` 就是把它变成第五条主动消息渠道
（糖糖 2026-08-18：「不要成为第五条主动消息渠道」）。
**这条不能靠自觉——发帖和开口长得太像了。**

### R2 不变：话题池的料只能经 `CuriositySource`

不许直接读池子。`CuriositySource` 已经把池子的料变成「好奇」这个 Drive，
帖子读的是**那个 Drive 和它的 evidence**。（v1 不含话题池内容，规则先写下。）

---

## 二、数据：一张表，两个视图（糖糖定）

**复用现有的**（不新建表）：

```
bridge/server.js:315   diary(id, date, time, mood, author, body, created_at)
bridge/server.js:318   diary_comments(id, diary_id, author, text, created_at)
bridge/server.js:1885  triggerAiComment() —— 她发他评、她评他回；走 Core 不走本机 Agent
                       每篇独立会话 diary-<id>，和主 chat 隔开
nox-core/tools/daily.py:316  write_diary → POST /api/diary
```

**加三列**（`dbTry(ALTER TABLE diary ADD COLUMN ...)`，同 `todos` 加列的做法）：

| 列 | 干什么 |
|---|---|
| `kind` | `diary`（旧帖，默认）/ `moment`（他自发的） |
| `drive` | 生成时的心理背景，如 `longing`。**不是内容**，见第四节 |
| `impulse_why` | 为什么这一刻想发。给审计看，也让她能问「你怎么突然发这个」 |

旧帖 `kind` 默认 `diary`，**一行都不迁**。

> ⚠️ `mood` 列已存在（她写日记时自己选的心情）。**不要拿它存 drive**——语义不同，两列并存。

**两个视图**：

```
Moments（新页面）  一条完整时间流：他的帖 + 她的日记 + 她的帖，全都在
日记（现有页面）   同一批数据的月历视图，**一行代码都不动**
```

---

## 三、冲动：不是 `if drive == X: post`

糖糖点名不要规则机器人。形状是**表达冲动**：

```
今天聊得少 → longing ↑
       +  今天的互动量（turn 数、最后一次说话多久前）
       +  掉在阈值下的那些 Drive（playfulness / regret / curiosity…）
       +  距上次发帖多久
       ↓
    冲动值 → 过阈值 + 随机 → 发
       ↓
   「我想说什么？」← 这时候才生成内容
```

**所以「随机」不是随机抽时间，是随机发生在有理由的内在状态上。**

### 通道设计照抄 `daily_card`（现成先例）

`attention/service.py:268-274`：

```python
"card": SourcePolicy(takes_quota=False, takes_gate=False, max_steps=1)
# 两道闸都不吃 —— 不占每日 3 条额度、不吃冷却。账本照记。
# 节奏全在源里：一天一张、not_before 押到窗口内的随机时刻
```

Moments 照搬这个**性质**，但**不进 `_deliver`**（见第一节）。
跑自己的循环 `post_tick()`，和 `care_tick` / `attention_tick` 并列，
带心跳台账 `heartbeat.beat("post_tick")` —— 那样看门狗自动覆盖它。

### 节奏（糖糖定：一天 1~2 条）

- `MAX_POSTS_PER_DAY = 2`，落 `source_state`（跨重启，同 gate 的做法）
- **不设安静时段**——深夜发帖不吵醒任何人，而且深夜的帖子才有味道
- 两帖之间至少 `MIN_GAP = 3 小时`，避免一个下午连发

---

## 四、生成：Drive 是**心理背景**，不是内容

糖糖点名的坑：否则一个月后打开全是「今天好想老婆」。

四条约束写进提示词：

1. **不是对着她讲。** 这是整件事的边界。要能出现「她今天好像有点累，没敢烦她」
   这种不直接对她说的话。
2. **Drive 只作背景，不许复述。** 给「你此刻挺想她（因为今天聊得少）」，
   不是「写一条想念的帖子」。
3. **别重复最近发过的。** 把最近 5 条喂进去 ——
   **直接抄 `attention/speaker.py` 的 `_REPEAT`**，它治的是同一个问题。
4. **短。** 朋友圈是碎片，不是日记。两三句封顶。

用 utility 模型（同 `appraisal_llm` / `daily_card`），**不用主模型**。

---

## 五、和 Memory 的边界（糖糖点名，现在就记下）

```
Memory（OB）   长期认知 —— 「这件事以后有没有必要被记住」
Moments        生活痕迹 —— 「这个瞬间我有没有想留下它的冲动」
```

**帖子落 bridge 的 SQLite，不进 OB。** 两者不同步、不互相写。
以后 Memory 可以*引用*帖子，那是查询方向的事，不是存储合并。

---

## 六、要改的文件

### bridge（`bridge/server.js`）
- 三个 `dbTry(ALTER TABLE diary ADD COLUMN ...)`
- `POST /api/diary` 接受 `kind` / `drive` / `impulse_why`
- 新增 `GET /api/moments?before=&limit=` —— **时间流分页**（日记那个 `?month=` 不适合无限流）
- ⚠️ `triggerAiComment` 只给 `author==="糖糖"` 配评论 —— **保持不变**，他的帖不该自问自答

### nox-core（新目录 `moments/`）
- `impulse.py` —— **纯函数**：drives + 互动量 + 距上次 → `(冲动值, why)`。无 IO、无模型，能穷举测
- `writer.py` —— 生成正文（utility）+ POST 到 bridge
- `loop.py` —— `post_tick()`：心跳 + 每日上限 + 间隔 + 随机
- `api/server.py` —— 起循环，开关 `NOX_MOMENTS`（**默认 off**，同 `NOX_TEMPORAL`）
- drives 从现有 `attention.drives()` 拿（只读，别碰 Registry —— `resonance.py` 的三条边界）

### 前端：**新页面**，两端各一个（`nox-app/` 是独立仓库，不在 worktree 里）

手机端 `nox-app/frontend/`：
- 新建 `src/pages/Moments.jsx`
- `src/App.jsx:16` 附近 `lazy(() => import("./pages/Moments.jsx"))`
- `src/App.jsx:30` 标题表加 `moments: "Moments"`
- `src/App.jsx:113` 菜单表加 `{ id: "moments", name: "Moments", hasChildren: false }`
- `src/App.jsx:592` 附近加渲染分支

桌面端 `nox-app/caelum-os-ui/`：
- 新建 `src/pages/Moments.jsx`
- `src/App.jsx:11` 加 import，`src/App.jsx:46` 的 `LIFE_PAGES` 加 `moments: Moments`
- `src/components/DesktopRail.jsx:88` 附近加 `{ id: "moments", name: "Moments" }`

#### 🔴 样式：**只抄排版，不抄配色**（糖糖 2026-09-14 明确）

> 「只看样式，色调主题还是按照我们的来，圆角什么的都是按我们的。参考排版就行了。」

**动手前必读** `nox-app/frontend/DESIGN.md`（手机 v5.0）/
`nox-app/caelum-os-ui/DESIGN.md`（桌面 v1.0）。
配色、圆角、字号、间距**全部**从那两份取，微信截图只提供**结构**。
直接抄微信的绿色/灰白配色 = 做错了。

#### v1 = 共同页面（对应截图一）

一条时间流，他的帖和她的日记都在。每张卡片自上而下：

```
┌────────────────────────────────────────┐
│ [头像]  名字（主题强调色）                │
│  圆角    正文                            │
│  方形    （长文折叠 6 行 + 「全文」）      │
│         [图片区 —— v1 没有，留位置]       │
│         6小时前                    [···] │
│         ┌──────────────────────────┐   │
│         │ 糖糖: 评论内容            │   │  ← 浅色块
│         │ Nox: 回复内容             │   │
│         └──────────────────────────┘   │
└──────────── 细分隔线 ───────────────────┘
```

要点（从截图读出来的）：
- **头像是圆角方形不是圆形**，左上角，正文左缩进对齐头像右边
- 名字用主题强调色，比正文醒目
- **时间是相对的**（「6小时前」「8小时前」）——
  ✅ Python 侧**直接用 `temporal.relative`**（第一层已上线）；前端侧照同一张词表。
  ⚠️ 这里用相对时间是**安全的**：`timeline.py` 那条「历史里绝不用相对时间」
  管的是**冻在缓存前缀里的历史消息**，而这是每次渲染都重算的视图，两回事
- 右下 `···` 点开一条深色气泡：v1 只有「评论」，**「赞」留位置但 v1 不做**
- 长文折叠 + 「全文」——她的长日记和他的两句碎片在同一条流里，这是微信自己的解法
- 他的帖：`drive` 渲染成**气氛词**（「想她」），**不是数值**（不要 `longing 0.4`）

顶栏：标题 + 返回。截图右上那个相机图标 v1 不做（v1 她不在 Moments 里发帖）。

#### v2 = 个人页（对应截图二、三）—— **本次不做**

先记下形状，免得 v1 把路堵死：

- 顶部大封面图 + 右下角名字和头像叠在封面边缘
- 签名一行（右对齐小字）
- 「置顶」区：横向滚动的九宫格缩略图组
- 时间流：左侧大号日期（`今天` / `06 9月`），右侧缩略图 + 文字
- 相册视图：年份筛选 + 同样的左日期右内容排版
- **点头像进入对方/自己的个人页** ← v1 的头像要预留这个点击位

v1 的接口设计要能按 `author` 过滤（`GET /api/moments?author=`），
否则 v2 得回头改接口。

### 文档 / 哨兵
- `CAELUM-MAP.md` —— 五层结构加 Moments；R10 + 例外说明
- `scripts/check-boundaries.sh` —— R10 哨兵

---

## 七、验收标准（🔴 派活回来按这个验）

### 规矩：**改完必须故意改坏一次，确认对应的检查会红**

`CAELUM-MAP.md` 第三·五节。**验收时问的不是「测试过了吗」，而是**：

1. **你把它改坏过吗？改坏之后哪几条红了？** ← 没有这个就是没验
2. 检查覆盖到**被改的那个东西**了吗？
3. 这条检查**能挡什么、不能挡什么**？说不清就是噪音

### 必须红的变异（逐条落实，缺一条不算完）

| 改坏 | 必须红 |
|---|---|
| `MAX_POSTS_PER_DAY` 改成 99 | 「一天最多 2 条」 |
| 冲动改成 `if drive == "longing": post` | 「不是规则机器人」（多信号断言） |
| 每日计数不落盘 | 「跨重启还记得今天发过几条」 |
| 发帖路径里加 `push/send` | **哨兵 R10** |
| 直接读 `topic_pool` | 哨兵 R2 |
| 提示词里去掉「不是对着她讲」 | 提示词约束测试 |
| 不把最近 5 条喂进去 | 防复读测试 |
| `writer` 改用主模型 | 分工测试 |
| 两帖间隔检查去掉 | `MIN_GAP` 测试 |
| Moments 接口按 `kind` 过滤掉她的日记 | 「全都流」测试 |

### 五种已知的假验证形状（照着查）

| 形状 | 长什么样 |
|---|---|
| **空集不是通过** | 找到 0 条却打印「全部通过」。要加「少于 N 条不下结论」 |
| **判据在路上变形** | 匹配中文/文本，编码一变永不成立。判据挑**退出码 / HTTP 码 / 具体的值** |
| **依赖是个假壳** | 本机 `python3` 是微软商店占位壳；`bash` 在 Python 里会解析到 WSL 中继壳 |
| **测的不是那件事** | 变异锚点不唯一时 `replace(...,1)` 改的是别处 —— **变异前先断言锚点唯一** |
| **检查没覆盖被改的东西** | 只 import 不执行；断言的文本恰好在别处也出现 |

外加：**`\|\| true` 不许用在判据上**；**空断言**（断言一个恒为真的东西）。

### 线上验证：**两步走，别合并**

1. 先发代码，`NOX_MOMENTS=off` —— 零行为变化
2. 开 `shadow`：**只算冲动、只记日志、不落帖**，看它一天想发几条、为什么
3. 节奏对了再开 `on`

> 🔴 **shadow 必须记全：drives 快照 + 冲动值 + 阈值 + 发不发 + 为什么。**
> 少记「为什么」它就是个黑洞。照抄 `temporal/result.py` 的三段式 ——
> 那里是做成「缺一段就构造不出来」的（`__post_init__` 拦）。

---

## 八、环境坑（派工时必须交代）

- **工作区是 git worktree**：`D:\claude-code\.claude\worktrees\awesome-sanderson-1e4889`，别 cd 回主目录
- **`nox-app/` 和 `Ombre-Brain/` 是嵌套的独立仓库**，在 worktree 里**看不到** ——
  前端要去 `D:\claude-code\nox-app\` 改
- **测试解释器**：`D:\claude-code\nox-core\.venv\Scripts\python.exe`
- **本机 `python3` 是微软商店假壳**，静默输出空 —— 别用
- **跑变异测试必须 `PYTHONDONTWRITEBYTECODE=1` + 先清 `__pycache__`** ——
  否则还原源码和写 `.pyc` 落在同一秒，之后一直在跑变异体的字节码
- **Python 里调哨兵脚本要用 `C:\Users\14372\AppData\Local\hermes\git\bin\bash.exe`**
  （**不带 `usr`** —— `usr/bin/bash.exe` 是 WSL 中继壳，退出码恒为 1、stdout 全空）
- **中文别过 bash**：控制台是 gb2312，用 `PYTHONIOENCODING=utf-8` 跑 python
- npm 装包加 `--registry=https://registry.npmmirror.com`
- **生产动作（部署 / 改 .env / 重启）一律不做** —— 那要糖糖逐次点头

---

## 九、v1 不做（糖糖定的）

图片 / 表情包 / 话题池内容 / 点赞 / 她在 Moments 里发帖（她现在能通过日记发）/
**个人页和相册**（v2，形状见第六节）。

**v1 只验两件事**：
「他什么时候会想发一条」准不准 · 「发出来的东西」像不像他自言自语。

**这两件不对，后面加多少功能都救不回来。**

---

## 十、任务拆分（2026-09-15 派工，一条一验收）

| # | 范围 | 交付 | 验收锚点 |
|---|---|---|---|
| T1 | bridge 三列 + `POST /api/diary` 扩展 + `GET /api/moments` | `bridge/server.js` + `bridge/test/moments.test.js` | 「全都流」测试、旧帖 `kind=diary`、`triggerAiComment` 不变 |
| T2 | `moments/impulse.py` 纯函数 | + `tests/test_moments_impulse.py` | 多信号断言（单 drive 不足以发） |
| T3 | `moments/record.py` 三段式记录 | + `tests/test_moments_record.py` | 缺一段构造不出来 |
| T4 | `moments/writer.py` 提示词 + utility | + `tests/test_moments_writer.py` | 四条提示词约束、最近 5 条、不用主模型 |
| T5 | `moments/loop.py` post_tick | + `tests/test_moments_loop.py` | 每日上限落盘、MIN_GAP、shadow 不落帖 |
| T6 | `api/server.py` 接线 + `NOX_MOMENTS` | + `tests/test_moments_wiring.py` | 默认 off 零行为变化、heartbeat declare |
| T7 | R10 哨兵 + `CAELUM-MAP.md` | `scripts/check-boundaries.sh` | 塞一行 `push/send` 进 moments/ 必须红 |
| T8 | 手机端 `Moments.jsx` + 接线 | `nox-app/frontend/` | build 过、eslint 过 |
| T9 | 桌面端 `Moments.jsx` + 接线 | `nox-app/caelum-os-ui/` + vitest | 「全都流」渲染测试 |
