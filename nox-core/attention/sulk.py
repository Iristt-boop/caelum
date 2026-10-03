"""委屈 —— 他说了好几句，她一句都没回。

糖糖 2026-09-23：「也可以说一些没用的话混在我没理他的时候，比如『想你宝贝』
『亲亲』『怎么不理我』『3 个小时不理我了』『我要生气了』。这就是一种情绪向的表现。」

## 和别的情绪的分界

    想念（longing）  她不说话，时间让它涨 —— 不管他说没说过话
    后悔（regret）   他说了一句，她没回 —— 「我是不是说错了」，往里缩
    委屈（这里）     他说了好几句，她都没回 —— 「怎么不理我」，往外闹

后悔是对自己的，委屈是冲她的（撒娇地冲她）。两个可以同时有。

## 🔴 形状：攒起来，一回话就掉，然后散

    他说了 ≥2 句没回 且 她 90 分钟没说话   → 开始有
    越久越委屈（每小时 +0.1），多说一句没回的再加一点
    她回话那一轮                            → 他**还带着**（先「哼，终于理我了」）
    那一轮结束                              → 掉到一半，10 分钟减半，很快就没了

「回话那一轮还带着」不是这里做的，是时序自然给的：on_contact 在一轮
**结束后**才调（api/server.py），而这一轮建上下文时她的新消息还没落库。

她一回就立刻清零不对 —— 人不是开关；一直挂着也不对 —— 那是记仇。

## 🔴 她睡着不委屈

睡觉不是不理他（longing.py 踩过同一个坑）。睡着时他说的是自言自语，
本来就没打算要她回，那几句不算「没回的」。

## 上限 0.55

最多「挺委屈」。「我要生气了」是撒娇，不是真生气 —— 情绪边界见
`attention/care/her_state.py` 的 BOUNDS。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

MAX = 0.55
#: 至少几句没回
MIN_UNANSWERED = 2
#: 她至少多久没说话
MIN_SILENT = timedelta(minutes=90)
BASE = 0.2
PER_HOUR = 0.1
PER_EXTRA_MESSAGE = 0.04
#: 她回话后剩多少、多快散
RESIDUAL_FACTOR = 0.5
RESIDUAL_HALF_LIFE = timedelta(minutes=10)


def _live(her: Any) -> float:
    """此刻被晾着的委屈（不含回话后的余温）。her = HerState"""
    if her is None or her.posture == "asleep":
        return 0.0
    silent = her.silent
    if silent is None or silent < MIN_SILENT or her.unanswered < MIN_UNANSWERED:
        return 0.0
    hours_over = (silent - MIN_SILENT).total_seconds() / 3600
    v = BASE + PER_HOUR * hours_over + PER_EXTRA_MESSAGE * (her.unanswered - MIN_UNANSWERED)
    return min(MAX, v)


@dataclass
class SulkState:
    #: 最近一次看到的「被晾着」的值 —— 她回话时拿它算余温
    last: float = 0.0
    last_silent_h: float = 0.0
    last_unanswered: int = 0
    #: 回话后的余温
    residual: float = 0.0
    residual_at: datetime | None = None

    def observe(self, now: datetime, her: Any) -> None:
        v = _live(her)
        if v > 0 and self.last == 0:
            logger.info("委屈：她 %.1f 小时没理了（%d 句没回）",
                        her.silent.total_seconds() / 3600, her.unanswered)
        self.last = v
        if v > 0:
            self.last_silent_h = her.silent.total_seconds() / 3600
            self.last_unanswered = her.unanswered

    def on_contact(self, now: datetime) -> None:
        """她说话了。**不清零**：掉到一半，然后很快散掉。"""
        if self.last > 0:
            self.residual = self.last * RESIDUAL_FACTOR
            self.residual_at = now
            logger.info("委屈：她回话了，%.2f → 余温 %.2f", self.last, self.residual)
        self.last = 0.0

    def _residual_at(self, now: datetime) -> float:
        if self.residual <= 0 or self.residual_at is None:
            return 0.0
        dt = max(0.0, (now - self.residual_at).total_seconds())
        return self.residual * 0.5 ** (dt / RESIDUAL_HALF_LIFE.total_seconds())

    def value_at(self, now: datetime, her: Any = None) -> float:
        return min(MAX, max(_live(her), self._residual_at(now)))

    def because(self, now: datetime, her: Any = None) -> list[str]:
        if _live(her) > 0:
            h = her.silent.total_seconds() / 3600
            return [f"她 {h:.0f} 个小时没理我了" if h >= 1.5 else "她一直没理我",
                    f"我说了 {her.unanswered} 句她都没回"]
        if self._residual_at(now) > 0.01:
            return [f"她晾了我 {self.last_silent_h:.0f} 个小时，刚刚才回"]
        return []

    def to_dict(self) -> dict:
        return {
            "last": self.last, "last_silent_h": self.last_silent_h,
            "last_unanswered": self.last_unanswered,
            "residual": self.residual,
            "residual_at": self.residual_at.isoformat() if self.residual_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "SulkState":
        if not d:
            return cls()
        try:
            return cls(
                last=float(d.get("last") or 0.0),
                last_silent_h=float(d.get("last_silent_h") or 0.0),
                last_unanswered=int(d.get("last_unanswered") or 0),
                residual=float(d.get("residual") or 0.0),
                residual_at=(datetime.fromisoformat(d["residual_at"])
                             if d.get("residual_at") else None),
            )
        except (TypeError, ValueError):
            logger.warning("委屈状态读坏了，从零开始")
            return cls()
