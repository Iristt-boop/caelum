"""Care Orchestrator —— 主动关心的唯一出口。

糖糖 2026-08-18 定的架构。她的原话：

> 惦记引擎不要成为「第五条主动消息渠道」。
> 把现在的四套机制保留，但全部变成 Trigger Source，
> 最后统一进入一个 Care Orchestrator。

```
Sleep ─┐
Time ──┤
Wake ──┤   六个源只负责「产生一个念头」
Random ┤   ⚠️ 任何一个都不许自己开口
Dream ─┤
Loc ───┘
        ↓
  ┌──────────────┐
  │ Orchestrator │
  ├──────────────┤
  │ 值不值得现在找│ ← 时效 + 额度（按链算）
  │ 找什么        │ ← 线头，抓不到就不说
  │ 什么时候找    │ ← 现在 / 押后 / 丢掉
  └──────────────┘
        ↓
       Nox
```

**这么做的收益**：他多久想起她一次，和他多久出现一次，被拆成了两件事。
他可以每 20 分钟想起她，但一天只冒头几次 —— 想念的频率和打扰的频率解耦。
原来四条渠道各自直通 `push`，做不到这个。

## 这一步（第一步）只搬家，不改行为

现有三条线降级成 Source 接进来，**每条走的闸和以前完全一样**：

| 源 | 过 Scheduler | 过额度 | 备注 |
|---|---|---|---|
| sleep | 是（service 里已算） | 是 | 原样 |
| time | 否（时间点就是为它定的） | 是 | 原样 |
| wake | 否 | **否** | 对话延续，2026-08-14 定的 |

新的 Random / Dream / Location 等第二步再接。先跑一天确认没退化 ——
这套东西的 bug 有个共同特点：**是静默的**，不报错，只是他不再出现了。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from attention.care.ledger import BLOCK, FAILED, SKIP, SPEAK, CareLedger
from attention.care.signal import (
    COMPANY,
    CareSignal,
    CareThread,
    ThreadBook,
)

logger = logging.getLogger(__name__)

#: 日常节奏：一小时最多开**一条新链**。链内部的连续追踪不受这条管
#: （出门那种 5/10/30 分钟连问算一条链，见 signal.py 开头）。
DEFAULT_NEW_THREAD_COOLDOWN_MIN = 60

#: 押后队列的上限。做梦那条线一晚上最多几个念头，这个数远够用；
#: 设上限只是为了「就算哪里写错了，也不会无声地把内存吃光」
MAX_HELD = 50


class CareSource(Protocol):
    """念头的来源。只回答「我想起什么了」，不回答「该不该说」。"""

    name: str

    def poll(self, now: datetime) -> list[CareSignal]:
        ...


@dataclass
class SourcePolicy:
    """每个源走哪些闸。**把原来散在 tick() 里的规则摆到台面上。**"""

    #: 吃不吃「一小时一条新链」的额度
    takes_quota: bool = True
    #: 吃不吃统一开口闸（DailyGate：每日额度 + 安静时段）。
    #: 唤醒链**不吃**（2026-08-14 定的：它是对话延续，不是「她沉默时的主动开口」）
    takes_gate: bool = True
    #: 开新链时这条链最多几步
    max_steps: int = 5
    #: **同一条链两步之间至少隔多久**。0 = 不限。
    #:
    #: 到点追待办是 1 小时一次（`Todo-Daily-Planner-设计.md` 第二节），
    #: 但 Attention 心跳是 15 分钟一次 —— 不设这个，一件没做的事
    #: 会被每 15 分钟追一遍，四倍于她定的节奏。
    min_step_gap_min: int = 0
    #: 默认开哪种链
    thread_kind: str = COMPANY


@dataclass
class CareOutcome:
    """一个念头的下场。**每个念头都要有下场** —— 静默丢弃是这套东西最难查的病。"""

    signal: CareSignal
    action: str          # "spoke" / "held" / "dropped" / "failed"
    reason: str = ""
    thread: CareThread | None = None
    #: 真发出去那条消息在 conversations 里的 id。**账本只存 id，不存原文**
    #: —— 原文属于 conversations，想看拿 id 去那边找（糖糖 2026-08-18）
    message_id: str | None = None

    def render(self) -> str:
        who = f"{self.signal.source}/{self.signal.subject}"
        return f"{who} → {self.action}（{self.reason}）"


class CareOrchestrator:
    """收念头、做决定、交给 deliver 去说。

    `deliver(signal, thread, now) -> bool` 由调用方给：真正开口那一下长什么样
    （推锁屏 / 落库 / dry-run 打日志）不归这一层管。
    返回 False = 没说出去，链不计步。

    ⚠️ **`now` 必须一路传到 deliver**，不许在下游自己 `datetime.now()`。
    2026-08-18 我就是在这儿栽的：deliver 里重新读了钟，于是额度记在
    「真实的今天」，而调用方查的是传进来的那个时刻 —— 表现成
    「消息发出去了，但额度没记上」。测试抓住了，线上不会。
    """

    def __init__(
        self,
        threads: ThreadBook,
        deliver: Callable[[CareSignal, CareThread, datetime], bool],
        *,
        policies: dict[str, SourcePolicy] | None = None,
        quota_cooldown_min: int = DEFAULT_NEW_THREAD_COOLDOWN_MIN,
        gate_check: Callable[[datetime], str] | None = None,
        asleep: Callable[[datetime], bool] | None = None,
        watching: Callable[[datetime], str] | None = None,
        ledger: CareLedger | None = None,
    ) -> None:
        self.threads = threads
        self.deliver = deliver
        #: 决策账本。**记的是决策不是开口** —— 说了 / 想了想没说 / 被拦下，
        #: 三样都记，这样「他今天惦记过你几次」才有分母（见 ledger.py）
        self.ledger = ledger or CareLedger()
        self.policies = policies or {}
        self.quota_cooldown_min = quota_cooldown_min
        #: 统一开口闸。返回拦截理由，空字符串 = 放行。
        #: 它属于「值不值得现在找」这一层，所以住在这儿，
        #: 不该散在各条线自己的代码里 —— 散着的时候没人说得清哪条线吃哪个闸。
        self.gate_check = gate_check
        #: 她睡没睡。**这是唯一保留的状态判断**（糖糖 2026-08-18）——
        #: 「忙不忙」整条去掉了（"有消息就主动发，我忙了我不回就好"），
        #: 只剩这一个，而且它不是用来拦他的，是**梦和惦记的分岔口**：
        #: 她醒着 → 该说就说；她睡了 → 他去做梦。
        #: 给 None 就是「不知道」，按她的口径走：**宁可说**。
        self.asleep = asleep
        #: 她在不在看片。返回拦截理由，空字符串 = 放行（同 gate_check 的形状）。
        #: **和闸不是一回事**：闸只管新链，这个连续链的追问一起拦 ——
        #: 看片时被追问「运动做了没」和被随便惦记一句，一样烦。
        #: 见 `attention/care/watching.py`
        self.watching = watching
        self._inbox: list[CareSignal] = []
        self._held: list[CareSignal] = []

    # ------------------------------------------------------------ 收

    def submit(self, signal: CareSignal | None) -> None:
        if signal is not None:
            self._inbox.append(signal)

    def submit_all(self, signals: list[CareSignal] | None) -> None:
        for s in signals or []:
            self.submit(s)

    def collect(self, sources: list[CareSource], now: datetime) -> None:
        """轮询所有源。**一个源炸了不许带塌别人** —— 这是六条线共用的通道。"""
        for src in sources:
            try:
                self.submit_all(src.poll(now))
            except Exception:  # noqa: BLE001
                logger.exception("Care 源 %s 出错，这轮跳过", getattr(src, "name", src))

    # ------------------------------------------------------------ 决

    def _policy(self, signal: CareSignal) -> SourcePolicy:
        return self.policies.get(signal.source, SourcePolicy())

    def _quota_blocked(self, now: datetime) -> str:
        """一小时内已经开过新链了吗？返回拦截理由，空字符串 = 放行。"""
        since = now - timedelta(minutes=self.quota_cooldown_min)
        recent = self.threads.opened_since(since)
        if not recent:
            return ""
        last = max(t.opened_at for t in recent)
        mins = int((now - last).total_seconds() / 60)
        return f"{self.quota_cooldown_min} 分钟内已经开过一条链了（{mins} 分钟前）"

    def run(self, now: datetime) -> list[CareOutcome]:
        """把这一轮攒下的念头走完。返回每一个的下场。"""
        # 押后的先回来排队，然后按急迫度排序
        batch = self._held + self._inbox
        self._inbox, self._held = [], []
        batch.sort(key=lambda s: s.urgency, reverse=True)

        out: list[CareOutcome] = []
        for signal in batch:
            outcome = self._decide(signal, now)
            self._note(outcome, now)
            out.append(outcome)
        return out

    #: `CareOutcome.action` → 账本里的决策。
    #: 「押后」（held）不记 —— 它还没有下场，等真轮到它再记，
    #: 否则做梦那条会在一晚上刷出几十笔「等着呢」
    _DECISION = {"spoke": SPEAK, "failed": FAILED}

    def _note(self, o: CareOutcome, now: datetime) -> None:
        if o.action == "held":
            return
        if o.action == "dropped":
            # **区分「他自己不说」和「规则不让说」** —— 这是整个账本的重点。
            # 前者是他的判断（没什么具体的可说），后者是栏杆太紧，
            # 混成一个数就分不清「他不粘人」和「我们拦太狠」
            decision = SKIP if o.reason == "没什么具体的可说" else BLOCK
        else:
            decision = self._DECISION.get(o.action, BLOCK)
        self.ledger.record(
            source=o.signal.source,
            decision=decision,
            thread_id=o.thread.id if o.thread else None,
            message_id=o.message_id,
            reason="" if decision == SPEAK else o.reason,
            now=now,
        )

    def _decide(self, signal: CareSignal, now: datetime) -> CareOutcome:
        policy = self._policy(signal)

        # ---- 时效：梦是凌晨做的，但要等她醒了再说
        if not signal.ready(now):
            # 押后队列要封顶。押后的念头只有「等一会儿再说」这一种来源，
            # 正常不会堆 —— 但真堆起来的时候，宁可丢最早的也不能无限长
            if len(self._held) < MAX_HELD:
                self._held.append(signal)
            else:
                logger.warning("押后队列满了（%d），丢掉最早的一个", MAX_HELD)
                self._held = self._held[1:] + [signal]
            return CareOutcome(signal, "held", "还没到该说的时候")

        # ---- 她在看片：所有线一起闭嘴（2026-08-22）
        #
        # 放在这么靠前是有意的：**续链的追问也要拦**。放到下面开新链那一段的话，
        # 「到点追待办」会照常在她看到高潮时冒出来 —— 那正是最烦的一种。
        #
        # 丢掉而不是押后：这类念头下一轮源会重新产出来（同链内节奏那段的理由）。
        # 而且记成 BLOCK 有用 —— 她能在账本里看见「3 次因为你在看电影没说」，
        # 押后的话这几次在账本上是不存在的。
        if self.watching is not None:
            blocked = self.watching(now)
            if blocked:
                return CareOutcome(signal, "dropped", blocked, thread=None)

        # ---- 续已有的链：不吃额度，链内部想追几步追几步
        thread = self.threads.get(signal.thread_id) if signal.thread_id else None
        if thread is not None and not thread.alive:
            return CareOutcome(signal, "dropped", f"链已经关了（{thread.closed_reason}）", thread)

        # ---- 链内节奏：追待办是 1 小时一次，不是每次心跳都追
        if thread is not None and policy.min_step_gap_min and thread.last_step_at:
            waited = (now - thread.last_step_at).total_seconds() / 60
            if waited < policy.min_step_gap_min:
                # **丢掉而不是押后** —— 这类念头每次心跳都会被源重新产出来，
                # 押后会让 _held 越堆越长，同一件事攒出几十份
                return CareOutcome(
                    signal, "dropped",
                    f"这条链 {int(waited)} 分钟前刚追过（要隔 {policy.min_step_gap_min} 分钟）",
                    thread,
                )

        if thread is None:
            # ---- 开新链：这一步才吃闸和额度。
            #      续已有的链不走这里 —— 连续追踪不算「又一次主动关心」
            if policy.takes_gate and self.gate_check is not None:
                blocked = self.gate_check(now)
                if blocked:
                    return CareOutcome(signal, "dropped", blocked)
            if policy.takes_quota:
                blocked = self._quota_blocked(now)
                if blocked:
                    return CareOutcome(signal, "dropped", blocked)
            thread = self.threads.open(
                signal.thread_kind or policy.thread_kind,
                signal.subject,
                session_id=signal.session_id,
                max_steps=policy.max_steps,
                now=now,
            )

        # ---- 真的说
        try:
            # deliver 可以回 bool，也可以回消息 id（str）——
            # 回 id 的话账本就能把这次决策和 conversations 里那条对上
            spoke = self.deliver(signal, thread, now)
        except Exception:  # noqa: BLE001
            # 发送失败不该让整轮挂掉，但**必须留痕** ——
            # 不然会变成「以为说了其实没说」
            logger.exception("Care 交付失败：%s", signal.subject)
            thread.note("deliver_failed", subject=signal.subject)
            return CareOutcome(signal, "failed", "发送失败", thread)

        if not spoke:
            thread.note("nothing_to_say", subject=signal.subject)
            return CareOutcome(signal, "dropped", "没什么具体的可说", thread)

        thread.step(now)
        thread.note("spoke", source=signal.source, subject=signal.subject)
        return CareOutcome(signal, "spoke", "说了", thread,
                           message_id=spoke if isinstance(spoke, str) else None)

    # ------------------------------------------------------------ 观察

    def snapshot(self) -> dict[str, Any]:
        return {
            "ledger": self.ledger.summary(),
            "threads_alive": [t.to_dict() for t in self.threads.alive()],
            "held": [{"source": s.source, "subject": s.subject,
                      "not_before": s.not_before.isoformat() if s.not_before else None}
                     for s in self._held],
            "quota_cooldown_min": self.quota_cooldown_min,
        }
