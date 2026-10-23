"""目标里「到点我去做点什么，再来告诉你」（2026-10-23，她：「那把『到点我去做点什么再来告诉你』做了」）。

目标可以带一件到点的事（bridge `goals.routine`）：{"at": "09:00", "weekdays": "1,...,7", "task": "搜今天的 AI 新闻，挑一两条讲给她"}。
到点了这里**不自己干活**，只往唤醒本里放一张任务纸条（`WakeBook.add_routine`）——
醒来那一套是现成的（`attention/waker.py` 的任务 prompt）：他拿着工具先把事做了，再决定说什么、推给她。

    每次心跳（15 分钟）看一眼：今天是那几天之一 + 过了那个时刻 + 今天还没做过 → 放纸条
    过了时刻太久（服务停了一上午）就不补了：9 点的新闻下午三点再推没意思

一天一次的账记在 attention.db 的 source_state（键 goal_routines）。读不到 bridge 留痕跳过，不炸心跳。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from temporal import to_local

logger = logging.getLogger(__name__)

STATE_KEY = "goal_routines"
#: 过了时刻多久之内还补（心跳 15 分钟一跳，留足余量；再晚就算错过了）
LATE_OK = timedelta(hours=2)


def due(goal: dict, now_local: datetime, fired: dict) -> bool:
    r = goal.get("routine") or {}
    if not r.get("at") or not r.get("task") or goal.get("status", "active") != "active":
        return False
    if str(now_local.isoweekday()) not in str(r.get("weekdays") or "1,2,3,4,5,6,7").split(","):
        return False
    today = now_local.date().isoformat()
    if fired.get(goal.get("id")) == today:
        return False
    hh, mm = (int(x) for x in str(r["at"]).split(":"))
    at = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return at <= now_local <= at + LATE_OK


def why_of(goal: dict) -> str:
    r = goal.get("routine") or {}
    return (f"【目标·{goal.get('title')}】{r.get('task')}"
            "（这是她要的：到点你去做，做完把值得说的告诉她；真没什么值得说的就别硬说）")


class GoalRoutines:
    def __init__(self, bridge: Any, store: Any) -> None:
        self.bridge = bridge
        self.store = store

    def poll(self, now: datetime, book: Any, session_id: str | None) -> list[Any]:
        """到点的放纸条。返回这次放了哪些纸条。"""
        if self.bridge is None or not session_id:
            return []
        r = self.bridge.get("/api/goals")
        if not r.ok:
            logger.warning("读目标失败，到点的事这轮不看：%s", r.error)
            return []
        local = to_local(now)
        try:
            fired = dict(self.store.get_source_state(STATE_KEY) or {})
        except Exception:  # noqa: BLE001
            logger.warning("读到点的事的账失败，这轮不放（宁可晚一跳也别一天放两遍）", exc_info=True)
            return []
        made = []
        for g in (r.data or {}).get("items") or []:
            if not due(g, local, fired):
                continue
            made.append(book.add_routine(session_id, why_of(g), now))
            fired[g["id"]] = local.date().isoformat()
        if made:
            self.store.set_source_state(STATE_KEY, fired)
        return made
