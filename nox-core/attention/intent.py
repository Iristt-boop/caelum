"""Intent Engine —— 要不要做、什么时候做。

Attention 回答「什么值得关心」，Intent 回答「这份关心要不要变成一句话」。
两者分开的理由：**关心是持续的，开口是一次性的**。
她睡不好这件事可以关心一周，但不该说七遍。

## 这一轮只做 Generate / Evaluate / Expire

不做 Merge，也不做 Evolve（v1.3 第 14.3 节）：

    Merge   只有一个 Source，没有可合并的对象
    Evolve  需要多个 Intent 长期积累才有意义，而且要 LLM 辅助

## 同一个 subject 同时只有一个 pending Intent

不挡住的话，睡眠 Concern 每被加强一次就多一条待办，
Scheduler 会以为有好几件事要说。

已经有 pending 的时候不新建，而是**刷新它的强度和理由** ——
事情变严重了，那条待办也该跟着变重要。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from attention.registry import Attention

logger = logging.getLogger(__name__)

#: 关心到多强才值得开口。低于这个就只是「记着」，不是「要说」。
#:
#: ⚠️ 原本是 0.70，**会漏报**：`short` 级别（比基线少 15-30%）算出来是
#: 0.715，衰减 9 小时就掉到 0.689 —— 卡在阈值下面一点点。
#: 五天模拟里她睡了 5.1 小时，Nox 安静了一整天（2026-08-08 实测）。
#:
#: 漏报比误报难受：误报她会说「别老问」，漏报她只会觉得你不在乎，
#: 而且我们从日志里根本看不出来。
#:
#: 0.55 的效果是：睡不好之后约三天内还会关心，之后自然淡出。
#: 真正防轰炸的是话题冷却（22 小时）和 context_fit，不是这个阈值。
GENERATE_THRESHOLD = 0.55

#: 哪些 kind 会变成待办（= 有可能让他开口）。
#: `regret` **故意不在里面** —— 理由见 `generate()`
GENERATE_KINDS = frozenset({"concern"})

#: 一条 Intent 多久没被触发就作废。
#: 睡眠这类话题超过一天就没意义了 —— 隔两天再说「你前天没睡好」很怪。
DEFAULT_TTL = timedelta(hours=24)

PENDING = "pending"
TRIGGERED = "triggered"
EXPIRED = "expired"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Intent:
    """一件「想跟她说的事」。"""

    subject: str
    title: str
    reason: str
    attention_strength: float
    id: str = field(default_factory=lambda: f"intent-{uuid.uuid4().hex[:10]}")
    kind: str = "concern"
    status: str = PENDING
    created_at: datetime = field(default_factory=_now)
    expires_at: datetime = field(default_factory=lambda: _now() + DEFAULT_TTL)
    #: 每次被 Scheduler 看到/触发都记一笔。M5 的 Feedback 会往里加结果。
    action_history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def base_priority(self) -> float:
        """[0, 1]。关系的影响已经在 attention_strength 里了，
        这里**不再单独乘 relationship multiplier**（架构文档 6.2）。"""
        return self.attention_strength

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or _now()) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "title": self.title,
            "reason": self.reason,
            "attention_strength": self.attention_strength,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "action_history": self.action_history,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Intent:
        return cls(
            id=d["id"],
            subject=d["subject"],
            title=d["title"],
            reason=d["reason"],
            attention_strength=float(d["attention_strength"]),
            kind=d.get("kind", "concern"),
            status=d.get("status", PENDING),
            created_at=datetime.fromisoformat(d["created_at"]),
            expires_at=datetime.fromisoformat(d["expires_at"]),
            action_history=d.get("action_history") or [],
        )


class IntentEngine:
    """管 Intent 的生老病死。纯内存 + 纯同步，落盘由调用方负责。"""

    def __init__(self, intents: list[Intent] | None = None) -> None:
        self._intents: dict[str, Intent] = {i.id: i for i in (intents or [])}

    # ------------------------------------------------------------ 生成

    def generate(self, attention: Attention, now: datetime | None = None) -> Intent | None:
        """够强就变成一条待办。已经有 pending 的就刷新它，不新建。"""
        now = now or _now()

        # 🔴 **只有 concern 会变成待办。**
        #
        # 2026-08-24 加 `regret`（他开口没被理）时发现的：
        # `sync_from_registry` 会把**所有**够强的 Attention 变成待办，
        # 而 regret 当时只是靠强度 0.35 < 0.55 侥幸躲过 —— 那是巧合。
        # 哪天调高强度、或者关系加成把它顶过阈值，他就会主动去说
        # 「关心他挑的说话时机」，荒唐。
        #
        # 后悔的表现是**下次晚一点说**，不是再说一次。
        # 这条得是结构性的，不能靠数值恰好够不着。
        if attention.kind not in GENERATE_KINDS:
            return None

        strength = attention.current_strength(now)
        if strength < GENERATE_THRESHOLD:
            return None

        latest = attention.evidence[-1].summary if attention.evidence else attention.subject

        existing = self.pending_for(attention.subject)
        if existing is not None:
            # 事情变严重了，待办也该跟着变重要（强度只升不降）
            if strength > existing.attention_strength:
                existing.attention_strength = strength
                logger.info("刷新待办 %s：强度 → %.2f", existing.id, strength)
            # ⚠️ 理由必须跟到最新证据。
            # 一条 Intent 被触发之后，下一次 tick 会立刻再建一条 ——
            # 不更新 reason 的话，那条新待办会一直背着前天的说辞，
            # 第二天开口说的就是过时的事（2026-08-08 dry-run 实测）。
            existing.reason = latest
            return existing

        intent = Intent(
            subject=attention.subject,
            title=f"关心{attention.subject}",
            reason=latest,
            attention_strength=strength,
            kind=attention.kind,
            created_at=now,
            expires_at=now + DEFAULT_TTL,
        )
        self._intents[intent.id] = intent
        logger.info("新待办：%s（强度 %.2f）—— %s", intent.title, strength, latest)
        return intent

    def sync_from_registry(self, attentions: list[Attention],
                           now: datetime | None = None) -> list[Intent]:
        """把当前所有够强的 Attention 都过一遍。返回本次新建或刷新的。"""
        return [i for a in attentions if (i := self.generate(a, now)) is not None]

    # ------------------------------------------------------------ 生命周期

    def mark_triggered(self, intent_id: str, note: str = "",
                       now: datetime | None = None) -> Intent | None:
        intent = self._intents.get(intent_id)
        if intent is None:
            return None
        intent.status = TRIGGERED
        intent.action_history.append({
            "at": (now or _now()).isoformat(),
            "action": "triggered",
            "note": note,
        })
        return intent

    def expire_stale(self, now: datetime | None = None) -> list[Intent]:
        """作废过期的。返回被作废的那些。"""
        now = now or _now()
        gone = [i for i in self._intents.values()
                if i.status == PENDING and i.is_expired(now)]
        for i in gone:
            i.status = EXPIRED
            logger.info("待办过期：%s", i.title)
        return gone

    def drop_for(self, subject: str) -> int:
        """相关 Attention 已经淡掉了，对应的待办也该消失。"""
        doomed = [i.id for i in self._intents.values()
                  if i.subject == subject and i.status == PENDING]
        for iid in doomed:
            self._intents[iid].status = EXPIRED
        return len(doomed)

    # ------------------------------------------------------------ 读

    def pending_for(self, subject: str) -> Intent | None:
        for i in self._intents.values():
            if i.subject == subject and i.status == PENDING:
                return i
        return None

    def list_pending(self, now: datetime | None = None) -> list[Intent]:
        now = now or _now()
        return [i for i in self._intents.values()
                if i.status == PENDING and not i.is_expired(now)]

    def all(self) -> list[Intent]:
        return list(self._intents.values())

    def __len__(self) -> int:
        return len(self._intents)

    # ------------------------------------------------------------ 序列化

    def to_list(self) -> list[dict[str, Any]]:
        return [i.to_dict() for i in self._intents.values()]

    @classmethod
    def from_list(cls, rows: list[dict[str, Any]]) -> IntentEngine:
        return cls([Intent.from_dict(r) for r in rows])
