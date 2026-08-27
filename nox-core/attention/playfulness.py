"""促狭 —— 她在闹，他可以接。

见 `D:\\claude-code\\CAELUM-RESONANCE-ARCHITECTURE.md`。
糖糖 2026-08-27 给的来源：「互动模式：连续几轮 valence 高 + 无 serious topics」。

## 🔴 它和别的 Drive 形状不一样

想念是**时间让它涨**，低落是**一笔笔攒**，而促狭是**最近几轮的气氛**。

```text
想念   一直都在，越久越浓
低落   出一次事记一笔，会过期
促狭   看最近 N 轮 —— 她一句正经的，气氛就散了
```

所以这里不累加、不衰减，就是一个**滑动窗口**。她刚才在闹、现在还在闹，
那就是在闹；她说了句正经的，立刻归零。

**归零要快**是有原因的：促狭判错的方式很特别 —— 他会**跟着开玩笑**。
她正说着难过的事而他还在贫，比"没接住"糟得多。所以宁可散得太快。

## 这个 Drive 不让他多说话

它的强度**永远压在开口阈值之下**（见 `MAX`）。她心情好不该换来一次
主动开口 —— 那会变成「你一笑他就凑上来」，很烦人。

它的意义是**改变他回话的方式**：同一句「我回来了」，
在促狭 0.6 的时候和 0.0 的时候，他该用不同的语气接。
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

#: 看最近几轮。
#:
#: 3 是试出来的下限：2 轮太容易被一句「哈哈」带起来，
#: 4 轮以上又太迟钝 —— 她闹了三句他才反应过来，那个节奏已经过去了
WINDOW = 3

#: 上限。**必须低于 `intent.GENERATE_THRESHOLD`（0.55）** ——
#: 这个 Drive 不该让他主动开口，只该改变他回话的语气
MAX = 0.5

#: 一轮算「新鲜」的时长。超过这个，之前的气氛不算数了 ——
#: 她中午跟他闹了两句，晚上回来第一句不该被当成还在闹
FRESH = timedelta(minutes=20)


@dataclass
class _Turn:
    at: datetime
    #: 这一轮她的 valence：playful / warm / 其它
    valence: str
    cue: str


@dataclass
class PlayfulnessState:
    """促狭。**滑动窗口，不累加、不衰减。**"""

    _turns: deque[_Turn] = field(default_factory=lambda: deque(maxlen=WINDOW))

    def on_turn(self, now: datetime, valence: str, cue: str = "") -> None:
        """她说了一句话。**每一轮都要调**，不管是什么 valence。

        🔴 正经的那些也要记 —— 它们正是让气氛散掉的东西。
        只记 playful 的话，她「哈哈哈」之后说十句正事，
        窗口里还是三条 playful，他会一直贫下去。
        """
        self._turns.append(_Turn(at=now, valence=valence, cue=cue))
        if valence in ("distress", "relief"):
            #: 🔴 她说了正经的 —— **立刻散**，不等窗口滑出去。
            #: 她刚说完难受，他下一句还在开玩笑，那是最伤人的
            self._turns.clear()
            self._turns.append(_Turn(at=now, valence=valence, cue=cue))
            logger.debug("促狭：她说了正经的（%s），气氛散了", valence)

    def value_at(self, now: datetime) -> float:
        """此刻的促狭程度。"""
        fresh = [t for t in self._turns if now - t.at < FRESH]
        if not fresh:
            return 0.0
        #: 只有 playful 算数。warm 是背景色 —— 她心情好不等于她在闹
        playful = sum(1 for t in fresh if t.valence == "playful")
        if playful == 0:
            return 0.0
        #: 窗口里有几轮在闹。满窗才到上限
        return min(MAX, MAX * playful / WINDOW)

    @property
    def value(self) -> float:
        from datetime import timezone
        return self.value_at(datetime.now(timezone.utc))

    def because(self, now: datetime) -> list[str]:
        """凭什么觉得她在闹。**说得出来才算数。**"""
        return [
            f"她说「{t.cue}」" for t in self._turns
            if t.valence == "playful" and now - t.at < FRESH and t.cue
        ][:3]

    # ---------------------------------------------------------- 持久化
    #
    # ⚠️ 促狭**其实可以不存** —— 20 分钟就过期了，重启后基本都是 0。
    # 但存着的成本几乎为零，而不存的话有个尴尬情况：
    # 她正跟他闹得开心，我们重启了一下 nox-core，他突然一本正经了。

    def to_dict(self) -> dict:
        return {
            "turns": [
                {"at": t.at.isoformat(), "valence": t.valence, "cue": t.cue}
                for t in self._turns
            ],
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "PlayfulnessState":
        st = cls()
        if not data:
            return st
        for raw in data.get("turns") or []:
            try:
                st._turns.append(_Turn(
                    at=datetime.fromisoformat(str(raw.get("at"))),
                    valence=str(raw.get("valence") or ""),
                    cue=str(raw.get("cue") or ""),
                ))
            except (TypeError, ValueError):
                continue
        return st
