"""小课堂那一股 —— 今天的卡备好了还没讲，往开口压力里加一笔（设计稿 2.5，她 10-24 定「交给开口压力」）。

和 goal_nudge 并列、不合并：那股是「她的事落后了」，这股是「我今天欠她一张课」。
所以这里也没有任何开口的路（R1：开口只走 Care 出口），只给压力一股劲 + 「为什么现在」的那句。

压力每分钟问一次 `drive()`，顺手把一天的账做了（都幂等）：

    06:00 起        每个 daily 的学习目标备一张（失败 30 分钟后再试，一天最多 3 次）
    一整天          他回的话里出现了这张卡的要点（key）→ 算讲了（不管是被压力推着开的口，
                    还是她正聊着他顺着讲的）；不靠他自觉调工具记（Haiku 10-09 嘴上记了没调的病）
    讲完 30 分钟    她的第一句话记下来（回的是别的也照记）
    14:00–21:00     还没讲 → 给压力一股；推着开过一次口还没讲 → 60 分钟后再给
    21:00 后        没讲的作废，不补
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from lessons import generate as gen
from temporal import to_local

logger = logging.getLogger(__name__)

GEN_HOUR = 6
#: 什么时候给压力那一股（她的本地时间）。21 点后就是夜里了，同旧小课堂
WINDOW = (14, 21)
INTENSITY = 0.5
RETRY_GAP = timedelta(minutes=30)
MAX_TRIES = 3
#: 推着开过口但没讲 → 隔多久再推
REOFFER_GAP = timedelta(minutes=60)
REPLY_WINDOW = timedelta(minutes=30)
#: 读 bridge 目标的缓存（同 goal_nudge）
CACHE = timedelta(minutes=5)

_SYSTEM_PREFIXES = ("（系统提示", "(系统提示", "[系统", "系统提示")


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


class LessonDrive:
    def __init__(self, lessons: Any, bridge: Any, sessions: Any, utility: Any = None) -> None:
        self.lessons = lessons
        self.bridge = bridge
        self.sessions = sessions
        self.utility = utility
        self._goals_cache: tuple[datetime, dict] | None = None
        #: goal_id → (date, 试了几次, 上次什么时候)。进程内：重启最多多试几次
        self._tries: dict[str, tuple[str, int, datetime]] = {}
        self._offered_at: datetime | None = None

    # ------------------------------------------------------------ 压力那边调的

    def drive(self, now: datetime) -> Any | None:
        local = to_local(now)
        today = local.date().isoformat()
        try:
            self._housekeep(now, local, today)
        except Exception:  # noqa: BLE001
            logger.warning("小课堂记账出错，这一分钟跳过", exc_info=True)
        if not (WINDOW[0] <= local.hour < WINDOW[1]):
            return None
        if self._offered_at is not None and now - self._offered_at < REOFFER_GAP:
            return None
        try:
            cards = self.lessons.pending(today)
        except Exception:  # noqa: BLE001
            logger.warning("小课堂读不到今天的卡，这一分钟不算它", exc_info=True)
            return None
        if not cards:
            return None
        because = [f"「{c['title']}」{c['body']}（要点 {c['key']}，讲的时候把它原样说出来）" for c in cards]
        return SimpleNamespace(name="lesson", intensity=INTENSITY, because=because,
                               card_ids=[c["id"] for c in cards])

    def fired(self, now: datetime) -> None:
        """这股推着他开了口。讲没讲看他回的话（_detect_told），这里只记「推过了」，60 分钟内不再推。"""
        self._offered_at = now

    # ------------------------------------------------------------ 一天的账

    def _housekeep(self, now: datetime, local: datetime, today: str) -> None:
        yesterday = (local - timedelta(days=1)).date().isoformat()
        n = self.lessons.expire_through(today if local.hour >= WINDOW[1] else yesterday)
        if n:
            logger.info("小课堂：%d 张没讲成，作废（不补）", n)
        self._detect_told(now, today)
        self._detect_reply(now)
        if GEN_HOUR <= local.hour < WINDOW[1]:
            self._ensure_cards(now, today)

    def _detect_told(self, now: datetime, today: str) -> None:
        for c in self.lessons.pending(today):
            key = c["key"].lower()
            for m in self.sessions.messages_between(c["generated_at"], _iso(now)):
                if m.get("role") == "assistant" and key in (m.get("text") or "").lower():
                    self.lessons.mark_told(c["id"], _dt(m["created_at"]))
                    logger.info("小课堂：讲了「%s」（要点 %s）", c["title"], c["key"])
                    break

    def _detect_reply(self, now: datetime) -> None:
        for c in self.lessons.awaiting_reply():
            told = _dt(c["told_at"])
            end = told + REPLY_WINDOW
            her = next((m for m in self.sessions.messages_between(c["told_at"], _iso(min(now, end)))
                        if m.get("role") == "user"
                        and not (m.get("text") or "").startswith(_SYSTEM_PREFIXES)), None)
            if her is not None:
                self.lessons.set_reply(c["id"], (her.get("text") or "")[:200])
                logger.info("小课堂：「%s」讲完她回了", c["title"])
            elif now >= end:
                self.lessons.set_reply(c["id"], "")

    def _ensure_cards(self, now: datetime, today: str) -> None:
        lines = self.lessons.lines("daily")
        if not lines:
            return   # 没有每天的学习目标 → 不出卡（她 10-24 定）
        goals = self._active_goals(now)
        if goals is None:
            return
        for line in lines:
            goal = goals.get(line["goal_id"])
            if goal is None or self.lessons.card_on(line["goal_id"], today) is not None:
                continue   # 目标停了 / 删了 / 今天已经有了
            d, n, last = self._tries.get(line["goal_id"], (today, 0, None))
            if d != today:
                n, last = 0, None
            if n >= MAX_TRIES or (last is not None and now - last < RETRY_GAP):
                continue
            self._tries[line["goal_id"]] = (today, n + 1, now)
            recent = self.lessons.recent(line["goal_id"], gen.DEDUP_WINDOW)
            card = gen.generate(self.utility, goal, line, recent, [c["key"] for c in recent])
            if card is None:
                continue
            self.lessons.add_card(line["goal_id"], today, card, now)
            logger.info("小课堂：「%s」今天这张备好了：%s（要点 %s）", goal.get("title"), card["title"], card["key"])

    def _active_goals(self, now: datetime) -> dict | None:
        if self.bridge is None:
            return None
        if self._goals_cache and now - self._goals_cache[0] < CACHE:
            return self._goals_cache[1]
        r = self.bridge.get("/api/goals")
        if not r.ok:
            logger.warning("小课堂读不到目标（今天的卡这一分钟不备）：%s", r.error)
            return None
        goals = {g["id"]: g for g in (r.data or {}).get("items") or [] if g.get("id")}
        self._goals_cache = (now, goals)
        return goals
