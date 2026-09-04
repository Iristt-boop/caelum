"""Attention Registry —— Nox 现在关心着什么。

## 这一轮只做 concern

架构设计里 Attention 有四型（focus / concern / curiosity / goal），
这一轮**只实现 concern**。另外三种等第二个 Source 接进来再说
（v1.3 第 14.3 节：明确不做什么）。

类型字段现在就留着，是为了以后加不用改表；`upsert` 只接受 `KINDS` 里的，
免得有人顺手塞进来一个**没人处理的** focus。

2026-08-24 加了第二种：`regret`（他开口没被理，见 `regret.py`）。
放开的前提是它确实有人处理 —— Evaluator 产生它、Resonance 聚合它。

⚠️ **但它不该变成待办。** Intent 只认 `concern`（见 `intent.py` 的
`GENERATE_KINDS`）：后悔的表现是下次晚一点说，不是再说一次。

## subject 是主键

同一个 subject 只有一条 Attention。「昨晚睡 5.2 小时」和
「今晚又睡了 5 小时」不该变成两条 Concern("糖糖的睡眠") ——
那样 Scheduler 会以为有两件事要说，就会说两次。

第二次来的时候是 **strengthen**，不是 create。这条是 Attention Evaluator
统一分类的基础，也是架构文档第十五节列的风险之一
（「Source 自己决定 Attention 类型」）。

## 衰减是惰性算的，不靠定时任务

存 `strength` + `last_updated`，读的时候按经过的时间现算。

不用定时任务扫全表的理由很实在：**服务会重启，也会停**。
定时任务停了，衰减就停了 —— 一周之后回来，Nox 还在为上周的事情紧张。
惰性算的话，不管中间停多久，读出来的强度永远是对的。

半衰期按 `decay` 分档：关心一个人的睡眠该慢慢淡（slow），
「她刚才说想吃披萨」这种该很快就没（fast）。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from attention.events import ExperienceEvent

logger = logging.getLogger(__name__)

#: 半衰期：强度衰减到一半需要多久。
#: slow 用于长期关心（睡眠、健康），fast 用于一闪而过的念头。
_HALF_LIFE = {
    "slow": timedelta(days=7),
    "normal": timedelta(days=2),
    "fast": timedelta(hours=6),
}

#: `upsert` 认哪些 kind。**加新的之前先问：谁处理它？**
#: 没人处理的类型进了 Registry，就是一条永远不会被读的数据
#: 2026-09-04 加 curiosity：第一个**和她无关**的 kind。
#: 产生者 attention/sources/curiosity.py，规则 evaluator._evaluate_curiosity，
#: 读它的是 resonance.snapshot()（按 kind 分组，自动多一个 Drive）
KINDS = frozenset({"concern", "regret", "curiosity"})

#: 低于这个强度就当没有了。不清掉的话 Registry 会慢慢堆满
#: 一堆 0.001 的陈年 Concern，`list()` 每次都要跳过它们。
FLOOR = 0.05

#: 一条 Attention 最多留几条证据。不封顶的话，一个每天触发的 Source
#: 半年能堆两百条，而真正有用的只有最近几条。
MAX_EVIDENCE = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Evidence:
    """支撑这条 Attention 的证据。出处要留着 —— 不然没法回答
    「他凭什么觉得我睡眠有问题」。"""

    event_id: str
    summary: str
    at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "summary": self.summary, "at": self.at.isoformat()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Evidence:
        return cls(event_id=d["event_id"], summary=d["summary"], at=datetime.fromisoformat(d["at"]))


@dataclass
class Attention:
    """一件 Nox 关心着的事。

    ⚠️ `strength` 是**上次更新那一刻**的强度，不是现在的。
    要当前值一律用 `current_strength()` —— 直接读 `strength` 会拿到
    一个没算过衰减的旧数，这是这个模块最容易用错的地方。
    """

    subject: str
    kind: str = "concern"
    strength: float = 0.0
    since: datetime = field(default_factory=_now)
    last_updated: datetime = field(default_factory=_now)
    decay: str = "slow"
    evidence: list[Evidence] = field(default_factory=list)

    def current_strength(self, now: datetime | None = None) -> float:
        """按经过的时间算出此刻的强度。

        指数衰减：每过一个半衰期减半。
        """
        now = now or _now()
        elapsed = (now - self.last_updated).total_seconds()
        if elapsed <= 0:
            return self.strength
        half_life = _HALF_LIFE.get(self.decay, _HALF_LIFE["normal"]).total_seconds()
        return self.strength * math.pow(0.5, elapsed / half_life)

    def is_alive(self, now: datetime | None = None) -> bool:
        return self.current_strength(now) >= FLOOR

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "kind": self.kind,
            "strength": self.strength,
            "since": self.since.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "decay": self.decay,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Attention:
        return cls(
            subject=d["subject"],
            kind=d.get("kind", "concern"),
            strength=float(d["strength"]),
            since=datetime.fromisoformat(d["since"]),
            last_updated=datetime.fromisoformat(d["last_updated"]),
            decay=d.get("decay", "slow"),
            evidence=[Evidence.from_dict(e) for e in d.get("evidence", [])],
        )


class AttentionRegistry:
    """一组 Attention，按 subject 索引。

    纯内存 + 纯同步。落盘是 `attention.store.AttentionStore` 的事，
    这一层不碰 IO —— 分开之后这些规则逻辑全都能用普通单测覆盖，
    不需要临时数据库。
    """

    def __init__(self) -> None:
        self._items: dict[str, Attention] = {}

    # ------------------------------------------------------------ 写

    def upsert(
        self,
        subject: str,
        strength: float,
        *,
        kind: str = "concern",
        decay: str = "slow",
        event: ExperienceEvent | None = None,
        summary: str = "",
        now: datetime | None = None,
    ) -> Attention:
        """新建或加强一条 Attention，返回更新后的对象。

        已经存在时是**取较大值**，不是相加 —— 相加的话，一个每小时
        报一次的 Source 几天就能把强度顶到 1.0，而事情本身没有变严重。

        `since` 永远保留第一次的时间：「从 8 月 4 号就开始担心了」
        这件事本身有意义，Scheduler 会用它判断该不该升级。
        """
        if kind not in KINDS:
            raise ValueError(f"不认识的 kind {kind!r}，认识的是 {sorted(KINDS)}（见模块 docstring）")
        if not 0.0 <= strength <= 1.0:
            raise ValueError(f"strength 要在 [0, 1]，拿到 {strength}")
        now = now or _now()

        item = self._items.get(subject)
        if item is None:
            item = Attention(
                subject=subject, kind=kind, strength=strength,
                since=now, last_updated=now, decay=decay,
            )
            self._items[subject] = item
            logger.info("新的关心：%s（strength=%.2f）", subject, strength)
        else:
            # 拿衰减后的当前值比，不是上次存的原始值 —— 否则一条三天前
            # 强度 0.9 的旧 Concern 会把今天真实的 0.6 压住
            decayed = item.current_strength(now)
            item.strength = max(decayed, strength)
            item.last_updated = now
            item.decay = decay
            logger.info(
                "加强关心：%s（%.2f → %.2f）", subject, decayed, item.strength,
            )

        if event is not None:
            item.evidence.append(Evidence(
                event_id=event.id,
                summary=summary or str(event),
                at=event.timestamp,
            ))
            # 只留最近的，老的丢掉（理由见 MAX_EVIDENCE）
            if len(item.evidence) > MAX_EVIDENCE:
                del item.evidence[:-MAX_EVIDENCE]
        return item

    def weaken(self, subject: str, factor: float, now: datetime | None = None) -> Attention | None:
        """按比例削弱。M5 的 Feedback 用这个 —— 她忽略了就该淡下去。"""
        item = self._items.get(subject)
        if item is None:
            return None
        now = now or _now()
        item.strength = item.current_strength(now) * factor
        item.last_updated = now
        logger.info("削弱关心：%s → %.2f", subject, item.strength)
        return item

    def drop(self, subject: str) -> bool:
        return self._items.pop(subject, None) is not None

    def prune(self, now: datetime | None = None) -> list[str]:
        """清掉已经衰减到地板以下的。返回被清掉的 subject。"""
        now = now or _now()
        dead = [s for s, a in self._items.items() if not a.is_alive(now)]
        for s in dead:
            del self._items[s]
        if dead:
            logger.info("清掉已经淡掉的关心：%s", "、".join(dead))
        return dead

    # ------------------------------------------------------------ 读

    def get(self, subject: str) -> Attention | None:
        return self._items.get(subject)

    def list(
        self, min_strength: float = 0.0, now: datetime | None = None
    ) -> list[Attention]:
        """按当前强度从高到低列出。

        `min_strength` 比的是**衰减之后**的值。
        """
        now = now or _now()
        out = [a for a in self._items.values() if a.current_strength(now) >= min_strength]
        return sorted(out, key=lambda a: a.current_strength(now), reverse=True)

    def top(self, now: datetime | None = None) -> Attention | None:
        items = self.list(now=now)
        return items[0] if items else None

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, subject: object) -> bool:
        return subject in self._items

    # ------------------------------------------------------------ 序列化

    def to_list(self) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self._items.values()]

    @classmethod
    def from_list(cls, rows: list[dict[str, Any]]) -> AttentionRegistry:
        reg = cls()
        for row in rows:
            a = Attention.from_dict(row)
            reg._items[a.subject] = a
        return reg
