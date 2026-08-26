"""上下文压缩 —— 把最老的一批已完成对话压成结构化摘要。

为什么要有它（2026-08-14 糖糖提的需求）：
- 原来 `history_limit=40` 按**条数**截断：40 条 ≈ 8k-12k token，
  遇到超长消息（工具结果/代码）会爆预算，遇到短消息（"晚安"）浪费窗口。
  而且一截断，40 条之前的事 Nox 完全不知道（除非去翻 OB）。
- 改成 **token 预算驱动**：快接近上限时，把最老一批压成 summary，
  最近窗口保留原文。历史消息**不删除**（库里永远有），只是进 prompt 时
  被 summary 替代。

summary 不是流水账（"糖糖说了A，然后Nox说了B"），是未来对话真正
需要知道的状态：Topic / Decisions / Current state / Open threads / Facts。

关键约束：
- 压缩用 utility 模型（便宜，deepseek-v4-flash），不影响主模型缓存前缀
- 压缩是**异步**的（不阻塞糖糖的请求）；压缩期间新消息照常进，下次再压
- 库里原文永不删除 —— summary 只是 prompt 层视图，load_full 仍可取全部
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.llm import Message, Turn
from context.token_budget import estimate_tokens

logger = logging.getLogger(__name__)

#: summary 消息的固定头。放在历史最前，让 Nox 知道这段对话早前发生过什么。
#: 必须是常量字符串 —— 它也是缓存前缀的一部分（跟在 dynamic_system 之后），
#: 变了会让 summary 之后的 history 全部错位（DeepSeek 自动前缀匹配）。
SUMMARY_HEADER = "【以下是这段对话早前部分的摘要，用于补充最近对话的上下文，不是现在正在说的：】\n"

#: summary 生成 prompt。核心是「提取未来需要知道的状态」，不是复述。
SUMMARIZE_SYSTEM = """你是 Nox 的会话摘要器。把一段对话压缩成结构化摘要。
这不是复述，是提取**未来对话真正需要知道的状态**。只保留：
- Topic: 这轮聊了什么主题（一句话）
- Decisions: 双方做出的决定、结论
- Current state: 当前进行中的状态（正在做什么、她提到要做什么）
- Open threads: 未解决、待继续的话题
- Facts: 提到的具体事实（日期、数字、偏好），不带评价

忽略：寒暄、重复、情绪波动、一次性的操作细节（比如"帮我开个灯"这种
当时就完成了的动作，结果不重要）。

如果已经有旧摘要，把它和新对话合并：旧摘要里仍然成立的内容保留，
被新对话推翻/更新的内容替换，不要两段重复。

没有的内容写"无"。用中文。输出要紧凑，不要空行。
"""

#: 压缩后 summary 的最大字符数（约 3000 token 的宽松上限，中文 0.6 token/字
#: 的话 ≈ 5000 字符；取 4000 留余量，超了由模型自己收敛）
MAX_SUMMARY_CHARS = 4000


@dataclass
class CompactPlan:
    """一次压缩的切分结果。"""

    #: 要被压缩掉的"最老一批"（进 summary 的那段）
    compressible: list[Message]
    #: 保留原文的最近窗口
    recent: list[Message]
    #: 压不压：false 表示没超预算，不需要动
    should_compact: bool
    #: 说明（日志用）
    reason: str = ""


def plan_compaction(
    full_history: list[Message],
    recent_window_tokens: int,
    budget_tokens: int,
    current_text: str = "",
) -> CompactPlan:
    """决定要不要压缩、切哪一段。

    full_history 是**全部**历史（load_full 的结果，时间正序）。
    规则：
    1. 估算全部历史 token；没超过预算（减去当前消息预留）就不压。
    2. 超过则：从尾部往前保留 recent_window_tokens 的原文窗口，
       窗口之前的部分全部进 summary。
    3. 至少保留一条可压缩的（compressible 非空）才压 —— 窗口占了全部
       说明消息本身太短，不值得压。
    """
    if not full_history:
        return CompactPlan([], [], False, "空历史")

    total = estimate_tokens(full_history)
    # 预留：当前消息 + 输出 ≈ 6k（主模型 max_tokens 16000 的 40% 左右，
    # 具体值不精确没关系 —— 压缩只需要"快接近上限"的粗判断）
    reserve = max(4000, int(16000 * 0.4))
    threshold = budget_tokens - reserve
    if total + estimate_tokens([Message(role="user", text=current_text)]) <= threshold:
        return CompactPlan([], full_history, False, f"未超预算（{total} ≤ {threshold}）")

    # 超预算：尾部保留原文窗口
    recent = full_history[-len(tail_within(full_history, recent_window_tokens)):]
    compressible = full_history[: len(full_history) - len(recent)]

    if not compressible:
        return CompactPlan([], recent, False, "窗口已占满全部历史，没有可压缩的")

    return CompactPlan(
        compressible, recent, True,
        f"超预算：{total} > {threshold}，压 {len(compressible)} 条 → summary",
    )


def tail_within(messages: list[Message], budget: int) -> list[Message]:
    """从尾部往前取 ≤ budget 的原文窗口（局部实现，避免循环依赖）。"""
    from context.token_budget import tail_within_budget
    return tail_within_budget(messages, budget)


def render_segment(messages: list[Message]) -> str:
    """把要压缩的消息段渲染成给摘要模型的文本。"""
    lines = []
    for m in messages:
        role = "糖糖" if m.role == "user" else ("Nox" if m.role == "assistant" else m.role)
        if m.text:
            lines.append(f"{role}：{m.text.strip()}")
        elif m.tool_calls:
            names = ", ".join(tc.name for tc in m.tool_calls)
            lines.append(f"Nox(调用工具 {names})")
        elif m.tool_results:
            lines.append("(工具结果)")
    return "\n\n".join(lines)


def build_summarize_messages(
    old_summary: str | None,
    segment: list[Message],
    max_chars: int = MAX_SUMMARY_CHARS,
) -> list[Message]:
    """组装摘要模型的输入消息。

    old_summary 存在时参与合并（迭代式压缩：旧摘要 + 新段 → 新摘要）。
    """
    user_parts = []
    if old_summary:
        user_parts.append(f"【旧摘要（之前压缩的结果，需要和新内容合并）】\n{old_summary}")
    user_parts.append(f"【本次要压缩的对话段】\n{render_segment(segment)}")
    user_parts.append(
        f"【约束】输出总长不要超过 {max_chars} 字符。"
        "按 Topic / Decisions / Current state / Open threads / Facts 分节，"
        "没有的内容写'无'。"
    )
    return [
        Message(role="system", text=SUMMARIZE_SYSTEM),
        Message(role="user", text="\n\n".join(user_parts)),
    ]


def summarize(
    adapter,
    old_summary: str | None,
    segment: list[Message],
    max_chars: int = MAX_SUMMARY_CHARS,
) -> str | None:
    """用 utility 模型把一段对话压成摘要。

    失败返回 None（调用方处理：这次不压，下次再试）。
    返回的摘要已经过预算校验（超了截断到 MAX_SUMMARY_CHARS）。
    """
    msgs = build_summarize_messages(old_summary, segment, max_chars)
    try:
        turn: Turn = adapter.complete(
            msgs, [],
            system=None,
            depth="low",
            max_tokens=4096,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("压缩调用失败: %s", exc)
        return None

    if turn.stop_reason in ("error", "refusal") or not turn.text:
        logger.warning("压缩未产出内容: %s", turn.stop_reason)
        return None

    text = turn.text.strip()
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[:MAX_SUMMARY_CHARS]
    return text


def maybe_compact(
    store,
    adapter,
    session_id: str,
    recent_window_tokens: int,
    budget_tokens: int,
    current_text: str = "",
) -> bool:
    """压缩入口：判断 + 执行一次压缩。返回是否真的压了。

    调用方负责异步（不阻塞用户请求）和异常兜底。
    """
    try:
        full = store.load_full(session_id)
        plan = plan_compaction(full, recent_window_tokens, budget_tokens, current_text)
        if not plan.should_compact:
            logger.debug("会话 %s 无需压缩: %s", session_id[:8], plan.reason)
            return False

        old = store.get_summary(session_id)
        summary = summarize(adapter, old, plan.compressible)
        if not summary:
            logger.warning("会话 %s 压缩失败，保留现状", session_id[:8])
            return False

        store.set_summary(session_id, summary)
        logger.info(
            "会话 %s 已压缩 %d 条 → summary %d 字符 (recent %d 条原文)",
            session_id[:8], len(plan.compressible), len(summary), len(plan.recent),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        # 压缩是增强不是主线 —— 任何失败都不该影响对话
        logger.warning("压缩异常（会话 %s）: %s", session_id[:8], exc)
        return False
