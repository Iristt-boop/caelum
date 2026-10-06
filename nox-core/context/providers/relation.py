"""你们之间的事 —— 关系状态账本进他每轮的上下文（2026-10-06）。

账本在 `attention/relation_book.py`：约定 / 别问 / 上心（她在聊天卡片上点了 Keep 的）+ 气氛（他自己判断，3 天淡掉）。
CAELUM-MAP 三问之二：谁消费它 —— 记下来不给他读，就是又一个「算出来了没人看」。

三条纪律照抄 UnderstandingProvider：没有就什么都不说；只给状态不写台词；
外加一条：**别问清单是「别主动问」，她自己提起照样接** —— 不然他会在她主动聊吃饭时装哑巴。
"""

from __future__ import annotations

import logging
from typing import Any

from context.base import BaseContextProvider, Turn

logger = logging.getLogger(__name__)


class RelationProvider(BaseContextProvider):
    name = "relation"
    section = "user"
    #: 本地库一次查询，便宜；她点了 Keep 下一轮就该看到
    volatile = True

    def __init__(self, attention_ref: Any, **kw: Any) -> None:
        super().__init__(**kw)
        #: 取值函数：attention 在 `_build_attention` 里才造出来（同 UnderstandingProvider）
        self.attention_ref = attention_ref

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        attention = self.attention_ref() if callable(self.attention_ref) else self.attention_ref
        book = getattr(attention, "relation_book", None) if attention is not None else None
        if book is None:
            return {"text": ""}
        try:
            return {"text": book.describe(turn.now)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("读关系账本失败，这轮不给他这段：%s", exc)
            return {"text": ""}

    def render(self, state: dict[str, Any]) -> str:
        text = (state or {}).get("text") or ""
        if not text:
            return ""
        return "【你们之间的事】照着相处就好，别念给她听\n" + text
