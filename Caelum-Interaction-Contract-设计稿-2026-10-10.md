# Caelum Interaction Contract（交互契约）设计稿 · 2026-10-10

> 配置层 **P0.5**。优先级高于继续做主动性 —— 输出层不干净，记忆 / 电话 / UI / 工具都会被污染，**他越强污染越快**。
> 方向是糖糖 10-10 定的：**不要靠人记规则，要让规则成为数据**（和配置层同一个思想）。

## 一、为什么要做

10-10 17:29 他该调 `send_voice_message` 却把参数写成了正文：

```
[语音条 softly] Okay okay, no more teasing…
[中文对照] 好啦好啦，不逗你了…
```

这条进了会话历史，之后他照着写：一条传成 9 条，还传进了电话（TTS 把「[语音，轻轻地] 中文」念了出来）。

同一类事故此前已修过三次，每次一块补丁、散在三处：

| 日期 | 漏出来的 | 补在哪 |
|---|---|---|
| 09-06 | `[开心]` | `MoodTagFilter` + `extract_text_tags` |
| 10-04 | `[!send_meme] 晚安` | `FAKE_CALL_RE` + `_rescue_fake_calls` |
| 10-10 早 | `[表情包] 举爪开心` | `_MEME_LABELED_RE` + 人设加一句 |
| 10-10 晚 | `[语音条 …] / [中文对照]` | —（本稿） |

数据（10-05 20:40 起 430 轮）：手册 `guide_read` 0 次 —— **写给他看的规则他不读**。所以不再加 prompt。

## 二、契约是数据：长在工具注册上

```python
ToolSpec(
    name="send_voice_message",
    ...,
    output_contract=OutputContract(
        markers=["[语音条", "[语音]", "[语音，", "[中文对照]"],   # 伪输出长什么样（行首结构）
        parse=parse_voice_artifact,   # 伪输出 → 工具参数；解析不出就 None
        rescue=True,                  # 能不能替他补调（看第三节）
    ),
)
```

新增 `send_music` / `send_image` / `send_sticker`：**只在注册时写契约，核心逻辑不改。**

## 三、触发条件（三条同时成立才算伪输出）

1. **疑似工具格式**：assistant 正文里有**行首**的契约 marker（不是任意位置的关键词）
2. **当前上下文允许该工具**：这一轮的 loop 里注册了它（电话 / 场景没有这个工具就不算）
3. **模型没有对应 tool_call**：这一轮的工具调用里没有它

只扫**他的输出**，从不扫她的消息 —— 她打 `[笑哭]` 永远只是她的话。

结果分三种：
- 三条成立 + 能解析 + `rescue=True` → **经 loop 唯一的 `_execute` 补调**（闸门、上限、名单校验全复用），正文剥掉
- 工具已经真调过（重复描述）→ 只剥正文，不再补调
- 解析不出 / `rescue=False` → 只剥，记 WARNING（「他以为发了，其实没有」看得见）

## 四、三个出口

| 出口 | 现在 | 改成 |
|---|---|---|
| **流式上屏**（`MoodTagFilter`） | 认 `[表情包` / `[!` 等写死的前缀 | 从契约注册表取 marker，整行吞 |
| **收尾**（`nox.py` chat / chat_stream） | 四步各管各 | loop 结束时统一 `apply_contracts`，nox.py 只剩情绪和舞台标签 |
| **电话** | TTS 念流式 text，事后靠 `stripVoiceTags` | 流里分 **`spoken`（给 TTS）/ `display`（给字幕）** 两个字段；契约伪输出、中文行不进 `spoken` |

## 五、历史只存干净的 + `history_sanitizer`

- 落库前已经是剥干净的正文（第三节）；真发出去的附件在给模型的历史里只是一句中性说明（「（发了一条语音）」），**不留可模仿的样板**
- `scripts/history_sanitizer.py`：用**同一份契约注册表**扫存量 —— `sessions.db`（他看的历史）和 `nox-bridge.db`（她看的聊天）
  - 默认 dry-run 出报告；`--apply` 前整库备份；按 id 记改动，可回滚
  - 以后换库、迁移会话，跑一遍就不会把旧污染带回来

## 五·五、C2 时查出来的：污染的放大器是会话历史成批重写

- 主会话 10617 行里只有 7130 行不重复。根因：sync 竞争（一轮拿开头快照跑十几秒，期间主动消息
  先落库，收尾用旧快照 sync 找不到锚点 → 整段重写，10-07 起一批 180+ 条）+ 锚点能撞上这一轮的新回复
  （连着两轮一样的话，如 [SKIP]，整轮不落库）。坏样板因此在他眼里出现 7 次，一轮输入 8.5 万 token
- 已修（`0301707`）：这一轮永远不当锚点、只补库尾没有的部分、快照过期扔缓存；比对去日期戳
- 更正：伪输出**不是**搬家后才有 —— 10-09 晚就有 `[表情包] …` / `[语音：…]`（东京上）；
  17:29 只是换了一种更显眼的写法

## 六、分期（每期独立可回滚、故意改坏验证）

> **进度（10-10 晚）**：C1 ✅ `c9baab9` 上线（线上实测：他照写伪语音条 → 契约替他补发真语音）·
> C2 ✅ `0301707` 上线并执行：sessions.db 10991→8858 行（删 2122 条成批重复 + 剥 14 条 + 删 3 条整条伪输出
> + 2 个 test 会话）、bridge 改 12 删 2；备份 + journal 在 N150 `/root/backups/sanitizer/20261010-195832/`，
> 回滚命令见那里的输出；再扫一遍 0 处 · C3 ⬜
>
> ⚠️ 清洗脚本在仓库根 `scripts/history_sanitizer.py`、N150 上是 `/root/history_sanitizer.py`（它要改 bridge 的库，
> 放进 nox-core 会被边界哨兵 R3 拦）；`--apply` 必须先停 nox-core + bridge —— nox-core 一直挂着 sessions.db 的写事务，外部写会 locked

| 期 | 范围 | 碰哪 |
|---|---|---|
| **C1** | `OutputContract` + 注册表 + `apply_contracts` 进 loop；语音条 / 表情两份契约；旧的三处补丁改成读注册表 | nox-core |
| **C2** | `history_sanitizer` + 清 10-10 的 9 条 | nox-core 脚本 + 两个库 |
| **C3** | 通话 `spoken` / `display` 分流 | nox-core 流事件 + bridge 透传 + nox-app engine |
| 后续 | 音乐卡 / 图片 / 订单卡等补契约（只注册） | 各工具文件 |

开关：`NOX_CONTRACTS=off` 退回旧路径。
