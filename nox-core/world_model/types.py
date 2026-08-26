"""World Model 的三个数据契约。

⚠️ **`Observation` 和 `ExperienceEvent`（`attention/events.py`）是两回事，
   别合并。** 它们长得像，但生命周期完全不同：

    ExperienceEvent   一次性的，Attention 消费完就没了
    Observation       永久保存的事实，可以被反复查询、可以支撑将来的重算

同一件事（她昨晚睡了 5 小时）会同时产生两者 —— 这是**故意的**，
不是重复。一个负责「值不值得关心」，一个负责「发生过什么」。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Observation:
    """Provider 观察到的一条事实。**冻结的，永不改写。**

    `observed` 里放的是**原始值**（`{"value": 5.33, "unit": "hour"}`），
    不是结论。「睡得差」是派生判断，属于 State，不属于这里。
    """

    source: str                      # health / home / location / ...
    type: str                        # sleep_duration / weight / ...
    observed: dict[str, Any]         # 原始值，不是结论
    observed_at: datetime            # 事情**发生**的时间
    id: str = field(default_factory=lambda: f"obs-{uuid.uuid4().hex[:12]}")
    ingested_at: datetime = field(default_factory=_now)   # 我们**收到**的时间
    confidence: float = 1.0
    #: 同一件事的幂等键（例如某一天的睡眠）。有它才能防重复写入
    dedup_key: str | None = None

    def __post_init__(self) -> None:
        # 时区是必须的。naive datetime 混进来，跨天判断和 TTL 全会错
        # （`attention/events.py` 立过同一条规矩）
        if self.observed_at.tzinfo is None:
            raise ValueError(f"observed_at 必须带时区: {self.observed_at!r}")


#: State 的三个生命周期。⚠️ **stale 永远保留，但不许伪装成 current**。
#: 「我最后记录是 8 月 1 日 68kg，最近没有新数据」和「你现在 68kg」
#: 是两句不同的话，说错了就是在骗她。
FRESH = "fresh"
STALE = "stale"
UNKNOWN = "unknown"


@dataclass
class State:
    """某个领域的当前状态。**派生的，可以随时从 Observation 重算。**"""

    type: str
    value: dict[str, Any]
    observed_at: datetime
    status: str = FRESH
    source: str = ""
    #: 这个结论是从哪几条 Observation 来的 —— 他答「为什么」时要用
    based_on: list[str] = field(default_factory=list)
    confidence: float = 1.0

    @classmethod
    def from_observation(cls, obs: Observation, ttl: timedelta,
                         now: datetime | None = None) -> State:
        now = now or _now()
        age = now - obs.observed_at
        return cls(
            type=obs.type,
            value=dict(obs.observed),
            observed_at=obs.observed_at,
            status=FRESH if age <= ttl else STALE,
            source=obs.source,
            based_on=[obs.id],
            confidence=obs.confidence,
        )

    def describe(self) -> str:
        """给模型看的一句话。**stale 必须说出来**，不能装作是当前值。"""
        if self.status == UNKNOWN:
            return f"{self.type}：不知道"
        if self.status == STALE:
            # ⚠️ 不用 `%-m/%-d`，那是 glibc 扩展，Windows 上跑测试会炸。
            # `tools/todo.py` 早就栽过一次，我又踩了同一个坑。
            d = self.observed_at.astimezone()
            return (f"{self.type}：最后一次记录是 {d.month}月{d.day}日，"
                    f"之后没有新数据")
        return f"{self.type}：{self.value}"


@dataclass(frozen=True)
class Evidence:
    """`query()` 的返回单元 —— **带来源的事实**，不是拼好的一段文本。

    为什么不直接返回字符串：他要能回答「你怎么知道的」。
    一段拼接文本回答不了，一条带 `source` + `observed_at` 的 Evidence 可以。
    """

    content: str
    kind: str                        # observed / derived
    source: str
    observed_at: datetime
    confidence: float = 1.0
    reference: str = ""              # health://sleep/2026-08-08
    raw: dict[str, Any] = field(default_factory=dict)
