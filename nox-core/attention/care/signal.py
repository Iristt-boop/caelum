"""Care 层的数据模型 —— 念头（CareSignal）和关心链（CareThread）。

这一层在 `ExperienceEvent` 之上：

    ExperienceEvent  「发生了什么」   sources/ 产出，不带判断
          ↓ Evaluator / Registry「这值不值得关心」
    CareSignal       「该找她一次」   ← 这里
          ↓ CareOrchestrator「现在吗 / 说什么 / 什么时候」
    真的开口

## 为什么要有 CareThread（糖糖 2026-08-18 定的）

她的原话：

> 5 分钟、10 分钟、30 分钟连续发，这三个消息不是三次主动关心，
> 而是一次关心事件产生的连续追踪。

所以**配额按链记，不按消息记**。一条链内部想追几步追几步，
只有「开一条新链」才吃额度。这样能同时成立：

    日常   —— 一小时最多开一条新链
    特殊   —— 出门这种事，链内部允许 5 分钟、10 分钟、30 分钟连着追

这个概念其实早就存在了 —— 唤醒链的 `MAX_CHAIN = 5` 就是一条链的步数上限，
只是它当时藏在 `wakeup.py` 里，别的机制用不上。这次把它提出来当一等公民。

## 关闭条件跟着链的类型走，不是全局规则

**这是 2026-08-18 那个 bug 的正解。** 昨晚他留了张纸条「到点把主卧空调关上」，
59 秒后糖糖说了句话，纸条就被撤了 —— 因为当时只有一条全局规则
「她一开口，整条链立刻结束」。

那条规则对追问型是对的（「吃完饭了吗」→ 她回了就没什么可追的），
对任务型是错的：**空调该不该关，跟她回不回话没有半点关系。**
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: 链的三种类型。关闭条件、能不能动手、她回话算不算数，全看这个。
FOLLOWUP = "followup"   # 追问：「她去吃饭了，回来问一句」—— 她回话就关
TASK = "task"           # 任务：「到点把空调关掉」—— 她回话**不关**，事没办完就还在
COMPANY = "company"     # 陪伴：随机惦记、做梦、时间醒来 —— 说完就关

OPEN = "open"
CLOSED = "closed"

#: 一条链最多几步。沿用唤醒链原来的 MAX_CHAIN。
#: 这是**上限不是配额** —— 他随时可以提前收工。
DEFAULT_MAX_STEPS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class CareSignal:
    """一个念头：「我想起她了，也许该找她一次」。

    Source 只管产生念头，**不许自己开口** —— 那是 Orchestrator 的事。
    这条边界不定死的话，加第七个源的时候又会有人图省事直接接到 push 上去。
    """

    #: 谁想起来的："sleep" / "time" / "wake" / "random" / "dream" / "location"
    source: str
    #: 话题。冷却和去重按它算
    subject: str
    #: 想开哪种链
    thread_kind: str = COMPANY
    #: 有多急，0-1。同一轮里多个念头撞车时用它排序
    urgency: float = 0.5
    #: 续哪条已有的链。None = 想开一条新的（要吃额度）
    thread_id: str | None = None
    #: 时效：这个点之前别说。**做梦用的** ——
    #: 凌晨三点梦到什么不该当场把她叫醒，攒到她醒了再说
    not_before: datetime | None = None
    #: 这条链挂在哪个会话上（唤醒链要用）
    session_id: str = ""
    #: 给 deliver 的原料（Intent、事件对象、梦的正文……）
    payload: dict[str, Any] = field(default_factory=dict)

    def ready(self, now: datetime) -> bool:
        return self.not_before is None or now >= self.not_before


@dataclass
class CareThread:
    """一次关心事件的完整生命周期。"""

    kind: str
    subject: str
    id: str = field(default_factory=lambda: f"care-{uuid.uuid4().hex[:10]}")
    session_id: str = ""
    opened_at: datetime = field(default_factory=_now)
    last_step_at: datetime | None = None
    steps: int = 0
    max_steps: int = DEFAULT_MAX_STEPS
    status: str = OPEN
    closed_reason: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def alive(self) -> bool:
        return self.status == OPEN and self.steps < self.max_steps

    @property
    def closes_on_reply(self) -> bool:
        """她回话了，这条链还有没有意义？

        任务型说没关系 —— **事情该不该做，和她回不回话无关**。
        """
        return self.kind != TASK

    def note(self, action: str, **extra: Any) -> None:
        self.history.append({"at": _now().isoformat(), "action": action, **extra})

    def step(self, now: datetime | None = None) -> None:
        self.steps += 1
        self.last_step_at = now or _now()
        if self.steps >= self.max_steps:
            self.status = CLOSED
            self.closed_reason = f"到步数上限（{self.max_steps}）"

    def close(self, reason: str) -> None:
        self.status = CLOSED
        self.closed_reason = reason
        self.note("close", reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "subject": self.subject,
            "session_id": self.session_id,
            "opened_at": self.opened_at.isoformat(),
            "last_step_at": self.last_step_at.isoformat() if self.last_step_at else None,
            "steps": self.steps,
            "max_steps": self.max_steps,
            "status": self.status,
            "closed_reason": self.closed_reason,
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CareThread:
        return cls(
            id=d["id"],
            kind=d.get("kind", COMPANY),
            subject=d.get("subject", ""),
            session_id=d.get("session_id", ""),
            opened_at=datetime.fromisoformat(d["opened_at"]),
            last_step_at=(
                datetime.fromisoformat(d["last_step_at"]) if d.get("last_step_at") else None
            ),
            steps=int(d.get("steps", 0)),
            max_steps=int(d.get("max_steps", DEFAULT_MAX_STEPS)),
            status=d.get("status", OPEN),
            closed_reason=d.get("closed_reason", ""),
            history=d.get("history") or [],
        )


class ThreadBook:
    """管所有关心链。纯内存 + 纯同步，落盘由调用方负责（同 WakeBook / IntentEngine）。"""

    def __init__(self, items: list[CareThread] | None = None) -> None:
        self._items: dict[str, CareThread] = {t.id: t for t in (items or [])}

    # -------------------------------------------------------------- 写

    def open(self, kind: str, subject: str, *, session_id: str = "",
             max_steps: int = DEFAULT_MAX_STEPS, now: datetime | None = None) -> CareThread:
        t = CareThread(kind=kind, subject=subject, session_id=session_id,
                       max_steps=max_steps, opened_at=now or _now())
        self._items[t.id] = t
        return t

    def close_on_reply(self, session_id: str, reason: str = "她回话了") -> int:
        """她开口了。**只关该关的那些** —— 任务型留着。

        ⚠️ 这里就是 2026-08-18 那个 bug 的修复点。原来的
        `WakeBook.cancel_for()` 不分类型一律撤，把「到点关空调」也撤了。
        """
        n = 0
        for t in self._items.values():
            if t.session_id != session_id or t.status != OPEN:
                continue
            if not t.closes_on_reply:
                t.note("kept_on_reply", why="任务型：她回话不影响这件事该不该做")
                continue
            t.close(reason)
            n += 1
        return n

    # -------------------------------------------------------------- 读

    def get(self, thread_id: str) -> CareThread | None:
        return self._items.get(thread_id)

    def alive(self) -> list[CareThread]:
        return [t for t in self._items.values() if t.alive]

    def alive_for(self, subject: str) -> CareThread | None:
        for t in self._items.values():
            if t.alive and t.subject == subject:
                return t
        return None

    def opened_since(self, since: datetime) -> list[CareThread]:
        """配额用：这段时间里开了几条**新**链（链内部的追踪不算）。"""
        return [t for t in self._items.values() if t.opened_at >= since]

    def all(self) -> list[CareThread]:
        return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    # -------------------------------------------------------------- 序列化

    def to_list(self) -> list[dict[str, Any]]:
        # 只留还活着的和最近关掉的，别让历史无限长
        items = sorted(self._items.values(), key=lambda t: t.opened_at, reverse=True)
        keep = [t for t in items if t.alive] + [t for t in items if not t.alive][:50]
        return [t.to_dict() for t in keep]

    @classmethod
    def from_list(cls, rows: list[dict[str, Any]]) -> ThreadBook:
        out = []
        for r in rows or []:
            try:
                out.append(CareThread.from_dict(r))
            except Exception:  # noqa: BLE001
                # 坏一条不该让整本账加载失败
                continue
        return cls(out)
