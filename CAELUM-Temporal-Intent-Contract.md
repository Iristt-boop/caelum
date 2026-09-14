# Temporal Intent Contract（第二层设计页）

> **状态：设计稿，等糖糖审形状。一行代码都还没写。**
>
> 上游：`CAELUM-时间模型审计-2026-09-14.md`（F5）
> 上一层：`nox-core/temporal.py`（第一层，2026-09-14 已上线）

---

## 〇、核心原则（糖糖 2026-09-14 定，一句话）

> **Temporal Intent 描述「她说的时间关系是什么」；
> Temporal Resolver 决定「这个关系在当前时间轴上具体落在哪里」。**

这一句划掉了所有含糊地带。下面五节只是把它拆开说清楚。

---

## 一、术语：谁负责什么，不许互相顶班

审计里最致命的一条是「一个 `datetime` 同时被当成四种东西」。
所以第二层开工前先把名字钉死——**每个词只干一件事**。

| 术语 | 是什么 | **不是**什么 | 谁产出 |
|---|---|---|---|
| `message_time` | 这句话是什么时候**说的** | 不是事情发生的时间 | 系统（`messages.created_at`） |
| `reference_time` | 解析相对表达时的**锚点** | 不是「现在」，见第四节 | 系统注入 |
| `intent` | 她这句话表达的**时间关系** | 不含任何绝对日期 | **LLM** |
| `intent.kind` | 关系的**形状**（day_offset / weekday_next / …） | 不是类型分类学，是**解析器的分派键** | LLM |
| `slot` | 一天中的**时段**（上午/下午/晚上/深夜） | **不是具体几点**，见第五节 | LLM（可选） |
| `deadline` | 「在……之前」，一个**上界** | 不是「在……发生」 | LLM（包住一个 intent） |
| `resolved_*` | 解析出来的绝对值 | **LLM 永远不填** | **Resolver** |

> ⚠️ `type` 这个词**整份契约里不用**。
> 它在现有代码里已经被 `ExperienceEvent.type` / `Observation.type` 占了
> （指的是 sleep_duration / weight 这类），再拿来指时间关系必然串味。
> 时间关系一律叫 `kind`。

---

## 二、LLM 的输出范围：一条红线

**LLM 只负责识别语言关系。**

它**不负责**（任何一条越界都是 bug，不是"顺手帮忙"）：

- ❌ 知道今天是几号
- ❌ 计算任何绝对时间
- ❌ 填 `resolved_date` / `resolved_at` / 任何 `resolved_*`
- ❌ 决定「周五」是这周还是下周（那是歧义，见第五节）

**它的输入里不该出现当前日期。** 这不是靠提示词约束，是靠**不给**——
提示词里没有今天几号，它就填不出日期来。

理由不是洁癖：模型算日期会错，而且**错得没有痕迹**。
一旦 `resolved_date` 是模型填的，下游就再也分不清
「她真的说了 9 月 15 日」和「模型算错了」。
关系错了看得出来（「明天」听成「后天」很显眼），日期错了看不出来。

```
她：明天去练腿
  │
  ├─ LLM   → {"kind": "day_offset", "n": 1}
  │          ↑ 它只知道「她说的是下一天」
  │
  └─ Resolver(reference_time=2026-09-14T14:00+08:00)
             → resolved_date = 2026-09-15
```

---

## 三、第一版语法（**封闭集合**，不边写边扩）

**没在这张表里的，一律 `unresolved`。** 不许 parser 里长出表外的分支——
那正是「平行实现反复长出来」的起点。

| `kind` | 字段 | 例子 | 解析成 |
|---|---|---|---|
| `day_offset` | `n: int` | 今天(0) / 明天(+1) / 昨天(-1) / 前天(-2) / 后天(+2) | `date` |
| `weekday_next` | `weekday: 1-7` | 下周三 / 周五 | `date`（歧义见第五节） |
| `month_end` | — | 月底 | `date`（当月最后一天） |
| `duration` | `hours` \| `minutes` \| `days` | 两个小时后 / 十分钟后 | `datetime` |
| `deadline` | `before: <intent>` | 周五之前 | 上界 `datetime` |
| `slot` | `morning/afternoon/evening/late_night` | 晚上 / 上午 | **修饰符，不单独成型** |

### 组合规则

`slot` 是**修饰符**，只能挂在算得出 `date` 的 kind 上：

```json
"昨天晚上"  →  {"kind": "day_offset", "n": -1, "slot": "evening"}
"明天上午"  →  {"kind": "day_offset", "n": 1,  "slot": "morning"}
```

`deadline` 是**包装器**，里面装一个别的 intent：

```json
"周五之前"  →  {"kind": "deadline",
                "before": {"kind": "weekday_next", "weekday": 5}}
```

### 解析结果的形状

```json
{
  "kind": "day_offset", "n": 1, "slot": null,
  "resolved": {
    "precision": "date",                  // date | datetime | slot | none
    "date": "2026-09-15",
    "at": null,                           // precision=datetime 时才有
    "range": null,                        // slot / deadline 时是一个区间
    "unresolved_reason": null             // 解析不了时**必须**填，见第五节
  }
}
```

`precision` 是给下游用的：Todo 要 `date` 就够，
纸条（`wakeup.wake_at`）需要 `datetime`，拿到 `slot` 精度时它该**拒绝**而不是自己补一个时刻。

---

## 四、`reference_time` 只能是 `message_time`

**规定死，由系统注入，LLM 不输出。**

为什么不是「现在」：这句话可能是十分钟前说的（后台理解层是异步跑的，
`appraisal_llm` 就在回应之后才抽取）。拿"现在"当锚，
她 23:58 说的「明天」会在 00:02 被解析成后天。

这不是假想——它和 `speaker._humanize` 原来那个按小时差算的 bug
是**同一个形状**：拿错了锚点，而且不报错。

```
reference_time := message_time      # messages.created_at，带时区
时区           := temporal.CST      # 第一层已经收成一份
```

> 重放和补算也靠它：拿历史消息重跑理解层时，锚点跟着消息走，
> 结果可复现。用"现在"的话，同一条消息今天和明天解析出不同答案。

---

## 五、歧义：宁可不解析，不许偷偷猜

**这一节是整份契约里最要紧的。**

第一版的立场：**语义不足 → `precision: "none"` + 填 `unresolved_reason`，
把这件事交回给他（或者让他问她一句）。**

Resolver 不许替模型猜，理由和第二节同源：**猜错了没有痕迹**。

### 三个具体判例

**① 「周五去」= 这周五还是下周五？**

→ **不解析。** `unresolved_reason: "weekday_ambiguous"`

不采用「取最近的未来周五」这种默认。看起来合理，但今天是周四时
「周五」是明天，今天是周六时「周五」多半指下周——
**同一个词在一周里指向不同，而系统无从知道她心里是哪个。**

「**下**周三」有明确的「下」字，那不歧义，走 `weekday_next`。

**② 「月底」有没有具体时刻？**

→ 解析到 `date`（当月最后一天），`precision: "date"`，**不给时刻**。

下游要时刻的自己决定怎么办。纸条拿到 `date` 精度应该拒绝，
而不是默认补个 23:59——那个 23:59 是系统编的，不是她说的。

**③ 「晚上」是 18:00、20:00 还是只代表 slot？**

→ **只代表 slot。** `precision: "slot"`，给一个区间 `[18:00, 23:00)`，
**不给点**。

时段边界已经在 `temporal.SLOTS` 里按她的作息定死了（第一层）。
把区间收成一个点是纯粹的编造。

### 一条兜底规矩

> **`unresolved_reason` 为空 ≠ 解析成功。**
> 判据永远是 `precision != "none"`。
>
> 这是本项目「空集不是通过」那条的时间版本：
> 一个什么都没解析出来的结果，长得和"没有时间表达"一模一样。
> 所以**两者必须分开**：没有时间表达 → 根本不产出 intent；
> 有时间表达但解析不了 → 产出 intent + `precision: none` + 原因。

---

## 六、这套语言给谁用

第一版只接**一个**消费方，验完再铺开（新能力三问之二：谁消费它）。

```
Temporal Intent
      │
      ├─ Todo        ← 第一版只接这个
      │   「今天不去，明天再去」→ next_intervention = resolved.date
      │   （对应审计 F8：不标完成，只改下次介入时间）
      │
      ├─ Attention   ← 以后：Intent 的 deadline（F9）
      ├─ Memory      ← 以后：记忆按 event_time 检索，不是按写入时间
      └─ Speaker     ← 已经不用自己算了（第一层 temporal.relative）
```

**先不接 Memory。** 记忆那边现在靠 `timeline.relativize()` 在正文里补相对日期，
那是个能用的权宜之计；动它要连带回答「记忆按哪个时间检索」，
那是另一场审计。

---

## 七、开工前还没定的（等她拍）

1. **谁来产出 intent** —— 挂在现有的 `appraisal_llm`（省一次调用、但它现在是
   `shadow` 模式），还是单独一次轻量调用？
   挂上去的话，**shadow 不写 Registry 会不会连 temporal 一起吞掉**要先确认。
2. **`weekday_next` 的「下周三」跨周边界** —— 周日说「下周三」，
   是 3 天后还是 10 天后？（我倾向也判歧义，但这条她可能有直觉）
3. **第一版要不要真接 Todo**，还是先只落日志观察（像理解层那样跑一周 shadow）。
   考虑到今天刚被「shadow 挡在上游」坑过一次，**要跑 shadow 就得先确认它有出口**。

---

## 八、验收标准

按 `CAELUM-MAP.md` 第三·五节：**改完必须故意改坏一次。**

这件事上必须红的检查（写代码时逐条落实）：

| 改坏 | 必须红 |
|---|---|
| 让 LLM 的输出里带上 `resolved_date` 并被采信 | 「resolved_* 只能由 Resolver 填」 |
| `reference_time` 换成 `now()` | 「23:58 说的明天，00:02 解析仍是同一天」 |
| 「周五」默认取最近的未来周五 | 「歧义必须 unresolved」 |
| 「晚上」收成一个时刻 | 「slot 精度不给点」 |
| `precision: none` 时下游照样用 `date` | 「空解析不许当成功」 |
| parser 里加一个表外的 kind | 「封闭集合」 |

⚠️ 最后一条最容易假绿：加一个表外分支**不会让任何现有测试红**。
要挡它得反过来断言——**枚举出允许的 kind 集合，多一个就红**。
