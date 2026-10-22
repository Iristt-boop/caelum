"""GoalsProvider —— 你在跟她的哪几个目标（2026-10-22，设计稿第三节）。

一行就够：名字 + 这周的进展。他要推进哪个再用 goal_update；细节在 Todo 里（todo 那一栏）。
没有目标就是空串，一个字不占（所以能进每轮名单）。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from context.base import BaseContextProvider, Turn

logger = logging.getLogger(__name__)

#: 最多列几个。同一轮最多带 3 个目标（设计稿 3.3，照 OpenClaw 常驻意图）
MAX_GOALS = 3


class GoalsProvider(BaseContextProvider):
    name = "goals"
    section = "user"
    #: 写路径登记 `context.wrote("goals")`，所以可以缓存久一点
    ttl = timedelta(minutes=30)

    def __init__(self, bridge: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.bridge = bridge

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        if self.bridge is None:
            return {"items": []}
        r = self.bridge.get("/api/goals")
        if not r.ok:
            raise RuntimeError(f"读目标失败: {r.error}")
        return {"items": list((r.data or {}).get("items") or [])}

    def render(self, state: dict[str, Any]) -> str:
        items = (state or {}).get("items") or []
        if not items:
            return ""
        parts = []
        for g in items[:MAX_GOALS]:
            w = g.get("week") or {}
            prog = f"（这周 {w.get('done', 0)}/{w['planned']}）" if w.get("planned") else ""
            parts.append(f"{g.get('title')}{prog}")
        more = f" 等 {len(items)} 个" if len(items) > MAX_GOALS else ""
        return "【你在跟她的目标】" + "、".join(parts) + more
