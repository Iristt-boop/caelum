"""`topics_browse` —— 他自己翻池子。

文档概述里那句话的工具化：「即使没有直接推给他，他也会自己主动去
池子里翻翻，看看最近外面有什么值得继续挖的」。

## 只读，不标 surfaced

surfaced 的语义定稿是「**被选用**」——Care 拿这条料开了口才记。
他翻过不算：翻了不聊是常态，翻了就算看过的话，账本很快就没有
「池子里还有什么新的」这回事了。他要是聊了，那是他自己说的，
话题的生命周期交给 TTL 和人工决策去管。

## ⚠️ 工具定义是缓存前缀的一部分

注册顺序要排在所有工具的最后、`remind_myself` 之前（同 reading.py
的注释）。插进中间，13K 静态前缀整段作废。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from agent.llm import ToolSpec

logger = logging.getLogger(__name__)

BROWSE_SPEC = ToolSpec(
    name="topics_browse",
    description=(
        "翻翻话题池：Scout 从外面捞回来、还没人聊过的东西（AI / 科学 / "
        "书影音 / 边角料）。你想主动找她开个话头、或者自己想看看最近"
        "有什么值得看的，用这个。返回里带 topic id——想聊哪条就在消息里"
        "自然地带出来，别念清单、别报菜名。"
    ),
    parameters={
        "type": "object",
        "properties": {"limit": {"type": "integer", "description": "看几条，默认 5"}},
    },
)


def make_handlers(pool: Any) -> dict[str, Any]:
    def browse(args: dict) -> str:
        if pool is None:
            return "话题池没启用。"
        try:
            limit = int(args.get("limit") or 5)
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(limit, 20))

        try:
            topics = pool.store.open_topics(
                datetime.now(timezone.utc), include_surfaced=False, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return f"池子读不出来：{exc}"

        if not topics:
            return ("池子现在是空的（Scout 六小时抓一轮，"
                    "好料还在路上或者已经过期了）。")

        lines = [f"池子里 {len(topics)} 条没人聊过的："]
        for t in topics:
            src = f"（来源：{t.source_title}）" if t.source_title else ""
            lines.append(f"· id={t.id} | [{t.category}] {t.hook}{src}")
        lines.append("（翻过不记账；真拿哪条开了口，那才算数。）")
        return "\n".join(lines)

    return {"topics_browse": browse}


def register_all(loop, pool: Any) -> None:
    """⚠️ 排在 remind_myself **之前** —— 那个必须是最后一个，有测试盯着。"""
    loop.register(BROWSE_SPEC, make_handlers(pool)["topics_browse"])  # type: ignore[arg-type]
