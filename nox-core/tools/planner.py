"""`daily_summary` 工具 —— 让他自己能去看「今天什么情况」。

## 为什么要有这个工具，Context 不是已经会自动注入了吗

会，但**要她说对关键词**。`classify_context()` 里「今天怎么样」能拉到
health+todo，「早安」原本一个都拉不到（已在 router/intent.py 里补上）。
关键词永远补不全 —— 她可能说「今天干点啥好呢」「我是不是该出门」。

有了工具，判断权就回到他手上：他觉得这句话需要知道今天的情况，就自己去拿。

## 和自动注入的分工

  自动注入   便宜、快、每轮都在，但靠关键词猜，只给当轮相关的那几项
  这个工具   一次拿全（睡眠+待办+天气+家里），他主动要才花这个钱

所以工具描述里写清楚了「已经知道就别重复调」—— Context 里已经有的东西，
再调一次纯属浪费。
"""

from __future__ import annotations

import json
import logging

from agent.llm import ToolSpec
from context.registry import ContextProviderRegistry
from planner.daily import build_brief

logger = logging.getLogger(__name__)


DAILY_SUMMARY_SPEC = ToolSpec(
    name="daily_summary",
    description=(
        "一次拿到今天的全部情况：现在几点、她昨晚睡得怎么样、今天有什么要做的、"
        "天气、家里的状态。\n"
        "什么时候用：她说「早安」「今天怎么样」「我今天该干嘛」，"
        "或者你想主动关心她今天的安排时。\n"
        "注意：如果这些信息已经在你的上下文里了，就别再调一次 —— "
        "这个工具会真的去打健康服务、GitHub 和天气 API。\n"
        "include_memory=true 会额外捞一遍最近的记忆，慢大约 7 秒，"
        "只在她明确想聊「最近怎么样」时才开。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "include_memory": {
                "type": "boolean",
                "description": "是否捎带最近的记忆检索，默认 false（慢 7 秒）",
            }
        },
    },
)


def make_handlers(registry: ContextProviderRegistry) -> dict:
    def daily_summary(args: dict) -> str:
        include_memory = bool(args.get("include_memory"))
        brief = build_brief(registry, include_memory=include_memory)

        # 空简报是真实情况，不是错误 —— 一个 Provider 都没注册的开发机上
        # 就是这样。如实说，别编。
        if not brief.text.strip():
            detail = "、".join(brief.missing + brief.unavailable) or "没有任何数据源"
            return (
                f"今天是 {brief.date} {brief.weekday} {brief.clock}，"
                f"但我这儿拿不到别的情况（{detail}）。"
            )

        parts = [f"今天是 {brief.date} {brief.weekday}，现在 {brief.clock}。", brief.text]

        # 缺了什么必须说出来，否则他会把「没拿到睡眠数据」当成「她睡得挺好」
        if brief.unavailable:
            parts.append(
                f"（这几项这次没拿到：{'、'.join(brief.unavailable)}，"
                f"别拿旧印象补，需要就直接跟她说没查到）"
            )
        if brief.stale:
            parts.append(f"（{'、'.join(brief.stale)} 是缓存的旧数据，说的时候提一句）")

        logger.info(
            "daily_summary: %d 项在场，缺 %d 项，旧数据 %d 项",
            len(brief.states), len(brief.unavailable), len(brief.stale),
        )
        return "\n".join(parts)

    return {"daily_summary": daily_summary}


def register_all(loop, registry: ContextProviderRegistry) -> None:
    """注册 daily_summary。

    ⚠️ 工具定义是静态前缀的一部分（见 tools/daily.py:238）——
    加这一个会让 12K 前缀变一次，缓存冷一轮，之后恢复。
    """
    handlers = make_handlers(registry)
    loop.register(DAILY_SUMMARY_SPEC, handlers["daily_summary"])  # type: ignore[arg-type]
