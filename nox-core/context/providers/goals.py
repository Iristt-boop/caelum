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


def _mentioned(goal: dict, text: str) -> bool:
    """她这句话里有没有提到这个目标：目标名，或者它落进 Todo 的那几件事（≥2 个字才算，防单字乱撞）。"""
    words = [str(goal.get("title") or "")] + [str(t.get("text") or "") for t in goal.get("todos") or []]
    return any(len(w) >= 2 and w in text for w in words)


class GoalsProvider(BaseContextProvider):
    name = "goals"
    section = "user"
    #: 写路径登记 `context.wrote("goals")`，所以可以缓存久一点
    ttl = timedelta(minutes=30)

    def __init__(self, bridge: Any = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.bridge = bridge

    def get_state(self, turn: Turn | None = None, force_refresh: bool = False) -> dict[str, Any]:
        """「她提到了」（10-23，设计稿 3.3 的第三种触发）：目标列表照旧缓存，**这一句话里提没提到**每轮现看。
        不调模型 —— 照 OpenClaw 常驻意图，按目标名和它落进 Todo 的那几件事的字面对。"""
        state = super().get_state(turn, force_refresh)
        text = (turn.text if turn is not None else "") or ""
        if not text or not state.get("items"):
            return state
        hit = [g for g in state["items"] if _mentioned(g, text)]
        return {**state, "mentioned": hit[:MAX_GOALS]} if hit else state

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        if self.bridge is None:
            return {"items": []}
        r = self.bridge.get("/api/goals")
        if not r.ok:
            raise RuntimeError(f"读目标失败: {r.error}")
        return {"items": list((r.data or {}).get("items") or [])}

    def render(self, state: dict[str, Any]) -> str:
        items = (state or {}).get("items") or []
        hers = [g for g in items if g.get("owner") != "nox"]
        mine = [g for g in items if g.get("owner") == "nox"]
        out = []
        if hers:
            parts = []
            for g in hers[:MAX_GOALS]:
                w = g.get("week") or {}
                prog = f"（这周 {w.get('done', 0)}/{w['planned']}）" if w.get("planned") else ""
                parts.append(f"{g.get('title')}{prog}")
            more = f" 等 {len(hers)} 个" if len(hers) > MAX_GOALS else ""
            out.append("【你在跟她的目标】" + "、".join(parts) + more)
        if mine:
            #: 他自己记着要跟进的（10-23）—— 提醒他的，不是提醒她的
            out.append("【你自己记着要跟进的】" + "、".join(
                f"{g.get('title')}（隔 {g.get('everyDays') or 2} 天问一声）" for g in mine[:MAX_GOALS]))
        for g in (state or {}).get("mentioned") or []:
            #: 她这句提到了它 → 把计划和最近的进展摊开给他（只这一轮）
            bits = []
            plan = [p.get("step") for p in g.get("plan") or [] if p.get("status") != "done"]
            if plan:
                bits.append("还没做完的计划：" + "、".join(plan[:3]))
            for t in (g.get("todos") or [])[:3]:
                bits.append(f"{t.get('text')}（{t.get('when')}）")
            last = (g.get("log") or [])[:2]
            if last:
                bits.append("最近：" + "；".join(f"{str(l.get('at'))[5:10]} {l.get('text')}" for l in last))
            out.append(f"【她刚提到的「{g.get('title')}」】" + ("；".join(bits) or "刚立，还没有进展"))
        return "\n".join(out)
