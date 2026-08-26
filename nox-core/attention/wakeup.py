"""唤醒链 —— 他给自己留张纸条，到点自己醒来看一眼。

糖糖 2026-08-11 的原话：

> 「当我 1 小时没有回复，他过 1 小时主动醒来，自己看上下文后思考
>   要不要再发一条消息。」

举的例子（这个例子定义了整个设计）：

    她：我去吃饭了
      ↓ 他留纸条：30 分钟后回来看看
    30 分钟后 —— 醒 —— 「吃完饭了吗宝贝」 —— 下次 1 小时后
      ↓ 她没回
    1 小时后 —— 醒 —— 「一个半小时没理我了，在干嘛呢」 —— 下次 …
      ↓ 循环，最多 5 次

## 两件事和初版设计不一样，都是她纠正的

1. **触发点不一定是他说话。** 起点是**她**说了「我去吃饭了」——
   所以纸条是他在正常聊天里用 `remind_myself` 工具留的，
   不是系统在他发完主动消息之后自动挂的。
2. **下次几点醒由他定。** 「去吃饭」和「去开会」和「我睡了」
   该隔多久回头看完全不是一回事，只有他知道。

## ⚠️ 语气不许写成规则

初版我写过一条「越往后越轻」的语气曲线，**糖糖当场否掉了**，她是对的：
那就是一张五行的规则表，一边说着「判断权交给他」一边把判断替他做了。

同样是「一个半小时没回」，情境不同该说的话完全不同：

    吃饭、工作      →  不打扰了
    出去玩          →  怎么不理我（委屈）
    跟男性朋友      →  跟别人到底在聊什么都不理我（吃醋）

**所以 `why` 字段是情境的载体，不是给日志看的。**
一小时后醒来的他不记得当时的对话细节，但他会看到自己留的纸条 ——
纸条上写什么情境，他就该是什么反应。

唤醒 prompt 里**只给事实，不给语气指导**。他吃醋就吃醋 ——
她要的是个会有反应的人，不是一个总是很体贴的助手。

## 硬护栏（这些不是语气规则，是栏杆）

- 一条链最多 5 次，他可以更早 STOP
- 凌晨 2:00-8:30 的唤醒**顺延**到早上，不是取消
- 她一开口，整条链立刻结束（她回了就没什么可追的了）
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from attention.care.signal import FOLLOWUP, TASK
from attention.scheduler import LOCAL_TZ

logger = logging.getLogger(__name__)

#: 一条链最多醒几次。糖糖定的。
#: 这是**上限不是配额** —— 他随时可以 STOP。
MAX_CHAIN = 5

#: 他没说下次几点醒时用这个。不设默认会让链断在半路，
#: 而链断掉是静默的（没有报错，只是他不再醒了）
DEFAULT_NEXT_MIN = 60

#: 留纸条的间隔上下界。5 分钟以下是打扰，12 小时以上不如等自然触发
MIN_AFTER_MIN = 5
MAX_AFTER_MIN = 12 * 60

#: 她睡觉的时段（中国时间）。落在这里面的唤醒顺延到 QUIET_END。
#: 和 `scheduler.context_fit()` 的 0.0 区间对齐 —— 那边是 2:00-8:30。
#: ⚠️ bridge 的 Care 写的是「1-9 点」，和这里对不上，收编时一并统一
QUIET_START = 2.0
QUIET_END = 8.5

PENDING = "pending"
DONE = "done"
STOPPED = "stopped"

#: 他在回复末尾放的控制标记：
#:   [NEXT 60]  说了这句，60 分钟后再醒
#:   [PASS 60]  这次不说，但 60 分钟后再来看
#:   [STOP]     这轮结束，别再醒了
#:   [DONE]     **任务型专用**：事情办完了，收工（正文可以是空的）
_CTRL = re.compile(r"\[\s*(NEXT|PASS|STOP|DONE)(?:\s+(\d+))?\s*\]", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def defer_past_night(when: datetime) -> datetime:
    """落在她睡觉时段的唤醒往后推到早上。

    **顺延不是取消** —— 她凌晨 1 点说「我睡了」，他留了个 6 小时的纸条，
    那张纸条该在早上兑现，不该被扔掉。
    """
    local = when.astimezone(LOCAL_TZ)
    h = local.hour + local.minute / 60
    if not (QUIET_START <= h < QUIET_END):
        return when
    end_h, end_m = int(QUIET_END), int(round((QUIET_END % 1) * 60))
    moved = local.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    logger.info("唤醒落在她睡觉的时段（%s），顺延到 %s",
                local.strftime("%H:%M"), moved.strftime("%H:%M"))
    return moved.astimezone(timezone.utc)


@dataclass
class Wakeup:
    """一张纸条。`why` 是情境载体，不是日志。"""

    session_id: str
    why: str
    wake_at: datetime
    id: str = field(default_factory=lambda: f"wake-{uuid.uuid4().hex[:10]}")
    #: 追问型（followup）还是任务型（task）。**2026-08-18 加的，为了修一个真 bug。**
    #:
    #: 00:06 他留了「到点把主卧空调关上」，00:07 糖糖说了句话 ——
    #: 纸条被「她一开口整条链结束」这条全局规则撤了，活了 59 秒。
    #: 那条规则对追问型是对的（「吃完饭了吗」，她回了就没什么可追的），
    #: 对任务型是错的：**空调该不该关，跟她回不回话没有半点关系。**
    kind: str = FOLLOWUP
    count: int = 0
    status: str = PENDING
    created_at: datetime = field(default_factory=_now)
    #: 「她说的话晚于这个时间才算新的」。
    #:
    #: ⚠️ **不能用 `created_at` 代替**，2026-08-11 实测栽在这儿：
    #: 她说「我去吃饭了」→ 这一轮里他调 remind_myself 建了纸条 →
    #: turn 结束后 `sessions.put()` 才把她那条消息写进库。
    #: 于是消息的时间戳**晚于** created_at，下次唤醒一看
    #: 「她在纸条之后说过话」，纸条当场作废。
    #: 结果是这条链在真实环境里**永远不触发，而且是静默的**。
    #: 所以由 `rebase()` 在 turn 真正结束、消息落库之后再校准一次。
    baseline: datetime = field(default_factory=_now)
    #: 每次醒来记一笔：说了什么、下次几点、为什么 STOP
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def alive(self) -> bool:
        return self.status == PENDING and self.count < MAX_CHAIN

    @property
    def closes_on_reply(self) -> bool:
        """她回话了，这张纸条还算不算数？任务型说不算 —— 事还在那儿。"""
        return self.kind != TASK

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "session_id": self.session_id,
            "why": self.why,
            "wake_at": self.wake_at.isoformat(),
            "count": self.count,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "baseline": self.baseline.isoformat(),
            "history": self.history,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Wakeup:
        return cls(
            id=d["id"],
            # 老数据没有 kind，一律当追问型 —— 那是加这个字段之前的唯一语义
            kind=d.get("kind") or FOLLOWUP,
            session_id=d["session_id"],
            why=d["why"],
            wake_at=datetime.fromisoformat(d["wake_at"]),
            count=int(d.get("count", 0)),
            status=d.get("status", PENDING),
            created_at=datetime.fromisoformat(d["created_at"]),
            # 老数据没有 baseline，退回 created_at
            baseline=datetime.fromisoformat(d.get("baseline") or d["created_at"]),
            history=d.get("history") or [],
        )


@dataclass
class WakeDecision:
    """解析他回复之后得到的东西。"""

    action: str            # "speak" / "pass" / "stop"
    text: str = ""         # action="speak" 时要发的那句
    next_after_min: int | None = None
    raw: str = ""

    @property
    def will_speak(self) -> bool:
        return self.action == "speak" and bool(self.text.strip())


def parse_decision(raw: str) -> WakeDecision:
    """从他的回复里抠出控制标记，剩下的是要说的话。

    找不到标记时**默认继续链**（说 + 60 分钟后再来）——
    默认停掉的话，他偶尔忘了写标记就会让链静默断掉，
    而链断掉是看不见的（没有报错，只是他不再醒了）。
    5 次上限兜着，继续比停掉安全。
    """
    text = (raw or "").strip()
    m = _CTRL.search(text)
    if m is None:
        return WakeDecision(action="speak", text=text,
                            next_after_min=DEFAULT_NEXT_MIN, raw=raw)

    kind = m.group(1).upper()
    mins = int(m.group(2)) if m.group(2) else None
    # 标记本身不该出现在她看到的那句话里
    body = _CTRL.sub("", text).strip()

    if kind == "STOP":
        return WakeDecision(action="stop", text=body, raw=raw)
    if kind == "DONE":
        # 任务型：事办完了。正文可以为空 —— 他去关了个空调，
        # 不一定非要说点什么
        return WakeDecision(action="done", text=body, raw=raw)
    if kind == "PASS":
        return WakeDecision(action="pass", next_after_min=mins or DEFAULT_NEXT_MIN, raw=raw)
    return WakeDecision(action="speak", text=body,
                        next_after_min=mins or DEFAULT_NEXT_MIN, raw=raw)


def clamp_minutes(n: int | None) -> int:
    if not n or n <= 0:
        return DEFAULT_NEXT_MIN
    return max(MIN_AFTER_MIN, min(MAX_AFTER_MIN, int(n)))


class WakeBook:
    """管所有纸条。纯内存 + 纯同步，落盘由调用方负责（和 IntentEngine 一样）。"""

    def __init__(self, items: list[Wakeup] | None = None) -> None:
        self._items: dict[str, Wakeup] = {w.id: w for w in (items or [])}

    # ------------------------------------------------------------ 写

    def add(self, session_id: str, why: str, after_min: int,
            now: datetime | None = None, kind: str = FOLLOWUP) -> Wakeup:
        """留一张新纸条。

        同一个 session 已经有**同类型**的活链时不新建，而是改期 ——
        不然她说「我去吃饭了」他留一张、又说「大概一小时」他再留一张，
        到点会连着醒两次说差不多的话。

        ⚠️ **按类型分开找**（2026-08-18）。不分的话会出这种事：
        他先留了「到点关空调」（任务），她接着说「我去洗澡了」，
        他想留个追问纸条 —— 结果把关空调那张**改期并覆盖 why**，
        空调这件事就这么无声无息地没了。
        """
        now = now or _now()
        at = defer_past_night(now + timedelta(minutes=clamp_minutes(after_min)))

        existing = self.active_for(session_id, kind=kind)
        if existing is not None:
            existing.wake_at = at
            existing.why = why or existing.why
            logger.info("纸条改期：%s → %s（%s）", existing.id, at.isoformat(), why)
            return existing

        w = Wakeup(session_id=session_id, why=why.strip(), wake_at=at,
                   created_at=now, baseline=now, kind=kind)
        self._items[w.id] = w
        logger.info("留了张%s纸条：%s 后回来看看 —— %s",
                    "任务" if kind == TASK else "", f"{clamp_minutes(after_min)} 分钟", why)
        return w

    def rebase(self, session_id: str, at: datetime | None = None) -> bool:
        """turn 结束、消息真的落库之后，把基准线校准到现在。

        **不校准的话这条链永远不会触发**（理由见 `Wakeup.baseline` 的注释）。
        由 `api/server.py` 在 `sessions.put()` 之后调。

        校准**这个会话上所有**活着的纸条 —— 追问型和任务型现在可以并存
        （2026-08-18），只校准找到的第一张会漏掉另一张。
        """
        when = at or _now()
        found = False
        for w in self._items.values():
            if w.session_id == session_id and w.alive:
                w.baseline = when
                found = True
        return found

    def reschedule(self, w: Wakeup, after_min: int, now: datetime | None = None) -> None:
        now = now or _now()
        w.count += 1
        if w.count >= MAX_CHAIN:
            w.status = DONE
            logger.info("链到顶了（%d 次），收工：%s", w.count, w.why)
            return
        w.wake_at = defer_past_night(now + timedelta(minutes=clamp_minutes(after_min)))

    def stop(self, w: Wakeup, note: str = "") -> None:
        w.status = STOPPED
        if note:
            w.history.append({"at": _now().isoformat(), "action": "stop", "note": note})

    def cancel_for(self, session_id: str, note: str = "她回话了") -> int:
        """她一开口，追问型的纸条就没什么可追的了。

        ⚠️ **任务型不撤。** 2026-08-18 的 bug 就在这儿：
        「到点把主卧空调关上」这张纸条，因为她 59 秒后说了句话被撤掉了。
        事情该不该做，跟她回不回话无关。
        """
        n = 0
        kept = 0
        for w in self._items.values():
            if w.session_id != session_id or w.status != PENDING:
                continue
            if not w.closes_on_reply:
                kept += 1
                continue
            self.stop(w, note)
            n += 1
        if n or kept:
            logger.info("%s：撤掉 %d 条纸条%s", note, n,
                        f"，留下 %d 条任务纸条" % kept if kept else "")
        return n

    def note(self, w: Wakeup, action: str, **extra: Any) -> None:
        w.history.append({"at": _now().isoformat(), "action": action, **extra})

    # ------------------------------------------------------------ 读

    def due(self, now: datetime | None = None) -> list[Wakeup]:
        now = now or _now()
        return sorted(
            (w for w in self._items.values() if w.alive and w.wake_at <= now),
            key=lambda w: w.wake_at,
        )

    def active_for(self, session_id: str, kind: str | None = None) -> Wakeup | None:
        """这个会话上活着的纸条。给了 kind 就只找那一类。"""
        for w in self._items.values():
            if w.session_id == session_id and w.alive and (kind is None or w.kind == kind):
                return w
        return None

    def all(self) -> list[Wakeup]:
        return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    # ------------------------------------------------------------ 序列化

    def to_list(self) -> list[dict[str, Any]]:
        return [w.to_dict() for w in self._items.values()]

    @classmethod
    def from_list(cls, rows: list[dict[str, Any]]) -> WakeBook:
        out = []
        for r in rows:
            try:
                out.append(Wakeup.from_dict(r))
            except Exception:  # noqa: BLE001
                logger.exception("纸条损坏，跳过：%s", json.dumps(r, ensure_ascii=False)[:120])
        return cls(out)
