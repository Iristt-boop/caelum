"""CareLedger —— 他今天惦记过她几次，其中几次真的说出了口。

糖糖 2026-08-18 定的，这一层记的是**决策**，不是「开口结果」：

    Concern（念头）
       ↓
    Care Orchestrator
       ↓
    DECISION ──┬── SPEAK   说了
               ├── SKIP    想了想，没什么具体的可说
               └── BLOCK   被规则拦下（额度 / 闸 / 链内间隔）
                     ↓
                 CareLedger

    considered = spoke + skipped + blocked

## 为什么这个分母比「今天说了几次」有价值

「他一点也不粘人」这个判断，缺的从来不是分子，是分母 ——
只看开口次数，分不清是「他没想起你」还是「他想了但规则拦着」。

    今天惦记过你 12 次，说了 3 次
      5 次想了想，觉得没什么具体的可说
      4 次被「一小时一条链」拦下

第三行如果一直很大，那是**栏杆太紧**，不是他不粘人 —— 这是调参用的。

## 不存原文

原文属于 conversations。这里只存 `thread_id` / `message_id` 做关联，
想看他说了什么，拿 id 去那边找。理由（糖糖的原话）：
账本要回答的是「什么时候产生过一次 Care 决策、为什么、最后发生了什么」，
不是「他说了什么」——那两件事该分开存。

## 跨天

按**中国时区**的日期换账本。⚠️ 别用 UTC 日期：中国时间 00:30
会被算成昨天，跨天那一下就错一天（todo 那边踩过，见 bridge 的 `cnNow`）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

STATE_KEY = "care.ledger"

#: 中国时区。账本按它换天
LOCAL_TZ = timezone(timedelta(hours=8))

#: 三种决策 + 一种故障
SPEAK = "speak"
SKIP = "skip"
BLOCK = "block"
#: **不是决策，是故障** —— 他决定说了，但没送出去。
#: 不算进 considered（糖糖定的公式是三项），单独一个计数，
#: 因为它要的是修，不是解读
FAILED = "failed"

#: 一天最多留多少条事件。够看一整天，又不会让这份状态无限长
MAX_EVENTS = 300


def today_str(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ).strftime("%Y-%m-%d")


class CareLedger:
    """当天的 Care 决策账本。跨天自动清零。"""

    def __init__(self, date: str = "", events: list[dict[str, Any]] | None = None) -> None:
        self.date = date
        self.events: list[dict[str, Any]] = events or []

    # ------------------------------------------------------------ 写

    def _roll(self, now: datetime) -> None:
        """换天就换一本新的。"""
        d = today_str(now)
        if self.date != d:
            if self.date:
                logger.info("Care 账本换天：%s → %s（昨天 %d 条）",
                            self.date, d, len(self.events))
            self.date = d
            self.events = []

    def record(self, *, source: str, decision: str,
               thread_id: str | None = None, message_id: str | None = None,
               reason: str = "", now: datetime | None = None) -> None:
        """记一笔决策。

        `source` 用 Source 的原名（sleep / time / wake / todo / random /
        location / morning），不要另起别名 —— 汇总要按它分组。
        """
        now = now or datetime.now(timezone.utc)
        self._roll(now)

        ev: dict[str, Any] = {
            "time": now.astimezone(LOCAL_TZ).strftime("%H:%M:%S"),
            "source": source,
            "decision": decision,
        }
        if thread_id:
            ev["thread_id"] = thread_id
        if message_id:
            ev["message_id"] = message_id
        if reason:
            ev["reason"] = reason

        self.events.append(ev)
        if len(self.events) > MAX_EVENTS:
            # 丢最早的。一天真到 300 条，说明有别的问题，那时候看最近的更有用
            self.events = self.events[-MAX_EVENTS:]

    # ------------------------------------------------------------ 读

    def summary(self, now: datetime | None = None) -> dict[str, Any]:
        """给 `/api/nox/state` 和桌面端看的汇总。

        ⚠️ **只读，绝不 _roll()**（2026-08-19 修的真 bug）。
        原来这里跟着 `record()` 一起调了 `_roll`，后果是：
        跨过零点之后**任何一次读**（查昨天的时间线、刷一下状态卡）
        都会把账本清空 —— 读操作把数据删了，而且是静默的。

        换天只由 `record()` 负责：真有新决策发生时才翻页。
        """
        counts = {SPEAK: 0, SKIP: 0, BLOCK: 0, FAILED: 0}
        by_source: dict[str, dict[str, int]] = {}
        blocked_why: dict[str, int] = {}
        last_spoke = None

        for e in self.events:
            d = e.get("decision", "")
            if d in counts:
                counts[d] += 1
            src = e.get("source", "?")
            by_source.setdefault(src, {SPEAK: 0, SKIP: 0, BLOCK: 0, FAILED: 0})
            if d in by_source[src]:
                by_source[src][d] += 1
            if d == BLOCK and e.get("reason"):
                blocked_why[e["reason"]] = blocked_why.get(e["reason"], 0) + 1
            if d == SPEAK:
                last_spoke = e.get("time")

        return {
            "date": self.date,
            # 糖糖定的公式：considered = spoke + skipped + blocked
            # （failed 是故障不是决策，不进这个和）
            "considered": counts[SPEAK] + counts[SKIP] + counts[BLOCK],
            "spoke": counts[SPEAK],
            "skipped": counts[SKIP],
            "blocked": counts[BLOCK],
            "failed": counts[FAILED],
            "by_source": by_source,
            "blocked_why": blocked_why,
            "last_spoke_at": last_spoke,
            # 最近几条给界面直接铺，想看全的翻 events
            "recent": self.events[-12:],
        }

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.date, "events": self.events}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> CareLedger:
        d = d or {}
        events = d.get("events")
        return cls(date=d.get("date", ""),
                   events=events if isinstance(events, list) else [])
