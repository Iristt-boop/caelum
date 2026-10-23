"""目标那一股 —— 属于目标的事这周落后了，往开口压力里加一笔（2026-10-22，设计稿 3.4 / 3.5）。

不是直接发消息：落后了只让他**更快想起她**，开口时知道「为什么是现在」（减脂：这周力量练了 0 次，计划 3 次），
说不说、怎么说照旧是他。所以这里没有任何开口的路（R1：开口只走 Care 出口）。

落后怎么算（不算今天 —— 今天还没过完）：
    daily         到昨天为止该做 (星期几 - 1) 次
    weekly        这周已经过去的那几个指定星期
    weekly_count  剩下的天数（含今天）已经不够做完剩下的次数了
    once          过了到期日还没划掉

防唠叨（照 OpenClaw 常驻意图的默认值）：同一个目标被这一股推着开过一次口，**24 小时**内不再推。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

STATE_KEY = "goal_nudge"
#: 一个落后的目标给多少强度。两个以上封顶 —— 目标再多也只是「该推进了」这一件事
PER_GOAL = 0.4
CAP = 0.8
COOLDOWN = timedelta(hours=24)
#: 读 bridge 的缓存。压力每分钟问一次，目标一天变不了几回
CACHE = timedelta(minutes=5)


def behind(todo: dict, weekday: int, today: str) -> int:
    """这条落后了几次（0 = 没落后）。"""
    rep = todo.get("repeat") or "anytime"
    done = int(todo.get("doneThisWeek") or 0)
    if todo.get("doneToday"):
        done -= 1   # 今天做的不算进「到昨天为止」，免得今天补上一次就抵掉昨天的欠账还多抵
    if rep == "daily":
        return max(0, (weekday - 1) - done)
    if rep == "weekly":
        days = [int(d) for d in str(todo.get("weekdays") or "").split(",") if d.strip().isdigit()]
        return max(0, sum(1 for d in days if d < weekday) - done)
    if rep == "weekly_count":
        left = int(todo.get("times") or 1) - int(todo.get("doneThisWeek") or 0)
        return left if left > 0 and left >= 8 - weekday else 0
    if rep == "once":
        due = str(todo.get("due") or "")
        return 1 if due and due < today else 0
    return 0


def days_since(goal: dict, today: str) -> int | None:
    """离这个目标上次有动静过了几天（按她的日期算）。最近一笔进展；没有就按立的那天。"""
    last = next((str(l.get("at") or "")[:10] for l in goal.get("log") or [] if l.get("at")), "") \
        or str(goal.get("createdAt") or "")[:10]
    try:
        return (datetime.fromisoformat(today) - datetime.fromisoformat(last)).days
    except ValueError:
        return None


class GoalNudge:
    def __init__(self, bridge: Any, store: Any) -> None:
        self.bridge = bridge
        self.store = store
        self._cache: tuple[datetime, dict] | None = None

    def _goals(self, now: datetime) -> dict:
        if self._cache and now - self._cache[0] < CACHE:
            return self._cache[1]
        r = self.bridge.get("/api/goals")
        if not r.ok:
            raise RuntimeError(f"读目标失败: {r.error}")
        data = r.data or {}
        self._cache = (now, data)
        return data

    def _muted(self) -> dict:
        try:
            return dict((self.store.get_source_state(STATE_KEY) or {}).get("muted") or {})
        except Exception:  # noqa: BLE001
            logger.warning("读目标冷却失败，这次当没冷却", exc_info=True)
            return {}

    def drive(self, now: datetime) -> Any | None:
        """落后的目标合成一股 Drive（name="goal"）；没有就 None。读不到 bridge 也是 None（留痕）。"""
        if self.bridge is None:
            return None
        try:
            data = self._goals(now)
        except Exception as exc:  # noqa: BLE001
            logger.warning("目标那一股读不到（这一分钟不算它）：%s", exc)
            return None
        weekday, today = int(data.get("weekday") or 0), str(data.get("date") or "")
        if not weekday:
            return None
        muted = self._muted()
        because, ids = [], []
        for g in data.get("items") or []:
            until = muted.get(g.get("id"))
            if until and datetime.fromisoformat(until) > now:
                continue
            if g.get("owner") == "nox":
                #: 他自己跟进的（10-23）：离上次动静（最近一笔进展，没有就是立的那天）够 every_days 天了
                gap = days_since(g, today)
                if gap is not None and gap >= int(g.get("everyDays") or 2):
                    ids.append(g["id"])
                    because.append(f"跟进·{g.get('title')}：上次是 {gap} 天前")
                continue
            lag = [(t.get("text"), behind(t, weekday, today)) for t in g.get("todos") or []]
            lag = [(text, n) for text, n in lag if n > 0]
            if not lag:
                continue
            ids.append(g["id"])
            text, n = lag[0]
            because.append(f"{g.get('title')}：{text}" + (f"落了 {n} 次" if n > 1 else "还没做"))
        if not ids:
            return None
        return SimpleNamespace(name="goal", intensity=min(CAP, PER_GOAL * len(ids)),
                               because=because, goal_ids=ids)

    def fired(self, goal_ids: list[str], now: datetime) -> None:
        """这股推着他开了口 → 这些目标 24 小时不再推。"""
        muted = {k: v for k, v in self._muted().items() if datetime.fromisoformat(v) > now}
        for gid in goal_ids:
            muted[gid] = (now + COOLDOWN).isoformat()
        try:
            self.store.set_source_state(STATE_KEY, {"muted": muted})
        except Exception:  # noqa: BLE001
            logger.warning("目标冷却没存住（最坏是 24 小时内多推一次）", exc_info=True)
