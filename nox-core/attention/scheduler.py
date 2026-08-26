"""Scheduler —— 现在是不是开口的时机。

它不决定说什么，只决定**什么时候**把哪条 Intent 交出去。

## 三道闸，缺一不可

    1. context_fit    这个点儿聊这件事合不合适
    2. 全局冷却        刚说过话就别接着说
    3. 每个话题的冷却   同一件事一天最多提一次

只有第 1 道是「聪明」的，2 和 3 是保底的。
**宁可漏说，不可打扰** —— 漏说她不知道，打扰她会记得。

## effective_score 不是只看 priority

    effective_score = base_priority × context_fit

架构文档 7.3.2 那个例子说明了为什么：晚上 10:30，deadline 的 priority
再高也不该压过睡眠，因为那个点儿聊 deadline 的 context_fit 很低。

## 时间按 Asia/Shanghai 算，不是服务器时间

VPS 在东京。用服务器本地时间判断「现在是不是晚上」会差一小时，
足够让「睡前关心」跑到她已经睡着之后。

## 状态要落盘

「上次什么时候开口的」重启后必须还记得 —— 否则每次部署完
Nox 都会觉得自己很久没说话了，可以马上开口。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from attention.intent import Intent

logger = logging.getLogger(__name__)

#: 糖糖在中国，服务器在东京 —— 判断「现在是不是晚上」必须按她的时区。
#:
#: ⚠️ 用固定 offset，**不要**改成 `ZoneInfo("Asia/Shanghai")`：
#: zoneinfo 在 Windows 上要额外装 `tzdata` 包，否则直接抛
#: ZoneInfoNotFoundError —— Linux 有系统时区库所以线上不会暴露，
#: 但开发机上连测试都跑不起来（2026-08-08 踩过）。
#: 中国 1991 年之后不实行夏令时，UTC+8 是恒定的，固定 offset 永远正确。
LOCAL_TZ = timezone(timedelta(hours=8), "CST")

#: 两次主动开口之间至少隔多久
BASE_INTERVAL = timedelta(hours=3)

#: 同一件事隔多久才能再提。比全局冷却长得多 ——
#: 「你昨晚没睡好」一天说一次已经够了，说两次就是唠叨。
#:
#: ⚠️ 22 小时不是随手写的，20 小时**会漏**：模拟一整天时发现
#: 00:00 提过之后，20:00 冷却解除，21:00 的睡前窗口又开一次口 ——
#: 一天说了两次。要盖过「同一个好窗口每天重现」的周期，
#: 这个值必须比 24 小时略小但足够接近（2026-08-08 dry-run 实测）。
SUBJECT_INTERVAL = timedelta(hours=22)

STATE_KEY = "scheduler"


@dataclass
class SchedulerDecision:
    """一次 tick 的完整结果。

    **dry-run 阶段全靠它** —— 没有它就只能看到「发了/没发」，
    看不到「为什么没发」，而后者才是这一步要观察的东西。
    """

    intent: Intent | None = None
    reason: str = ""
    effective_score: float = 0.0
    context_fit: float = 0.0
    considered: list[tuple[str, float]] = field(default_factory=list)

    @property
    def will_speak(self) -> bool:
        return self.intent is not None

    def render(self) -> str:
        """给日志看的一行。"""
        if self.intent is None:
            extra = ""
            if self.considered:
                extra = "；看过：" + "、".join(f"{s}({v:.2f})" for s, v in self.considered)
            return f"[不开口] {self.reason}{extra}"
        return (
            f"[想开口] {self.intent.title}"
            f"（score={self.effective_score:.2f} = "
            f"priority {self.intent.base_priority:.2f} × fit {self.context_fit:.2f}）"
            f" —— {self.intent.reason}"
        )


def context_fit(subject: str, now: datetime) -> tuple[float, str]:
    """这个点儿聊这件事合不合适，[0, 1] + 一句人话理由。

    现在只有睡眠一个话题，所以窗口是按**她的作息**定的
    （CLAUDE.md：凌晨 1-2 点睡，9-11 点起）：

        21:00-24:00  睡前，最合适                1.0
        00:00-02:00  过了零点，说昨晚已经晚了     0.2
        02:00-08:30  睡着了，绝对不要打扰         0.0
        08:30-12:00  刚起，问昨晚睡得怎么样自然   0.7
        12:00-21:00  白天聊睡眠不合时宜           0.3

    ⚠️ 00:00-02:00 原本给的是 0.5，dry-run 模拟一整天时发现它会
    在午夜真的开口（0.98 × 0.5 = 0.49，过线）。那个点说
    「你昨晚只睡了 4.7 小时」很怪 —— 她马上要睡了，说的却是前一晚，
    而且白白占掉当天的话题冷却，把 21:00 那个真正合适的窗口挤掉。
    降到 0.2 之后它基本不会单独触发（2026-08-08 实测）。

    第二个话题接进来时，这里要按 subject 分表 —— 现在不抽象，
    因为只有一条规则的规则表是纯粹的间接层。
    """
    local = now.astimezone(LOCAL_TZ)
    h = local.hour + local.minute / 60

    if 21 <= h < 24:
        return 1.0, "睡前，聊睡眠正合适"
    if 0 <= h < 2:
        return 0.2, "过了零点，这时候说昨晚已经晚了"
    if 2 <= h < 8.5:
        return 0.0, "她在睡觉"
    if 8.5 <= h < 12:
        return 0.7, "她刚起来，问昨晚睡得怎么样很自然"
    return 0.3, "白天聊睡眠不合时宜"


class Scheduler:
    """挑一条最该说的，或者决定什么都不说。"""

    def __init__(
        self,
        base_interval: timedelta = BASE_INTERVAL,
        subject_interval: timedelta = SUBJECT_INTERVAL,
    ) -> None:
        self.base_interval = base_interval
        self.subject_interval = subject_interval
        self._last_spoke_at: datetime | None = None
        self._last_by_subject: dict[str, datetime] = {}

    # ------------------------------------------------------------ 主流程

    def tick(self, intents: list[Intent], now: datetime | None = None) -> SchedulerDecision:
        """看一眼待办，决定要不要开口。**一次最多选一条。**"""
        now = now or datetime.now(timezone.utc)

        if not intents:
            return SchedulerDecision(reason="没有待办")

        # 全局冷却：刚说过话就别接着说
        if self._last_spoke_at is not None:
            quiet_for = now - self._last_spoke_at
            if quiet_for < self.base_interval:
                left = self.base_interval - quiet_for
                return SchedulerDecision(
                    reason=f"全局冷却中，还差 {int(left.total_seconds() / 60)} 分钟"
                )

        scored: list[tuple[Intent, float, float, str]] = []
        for intent in intents:
            # 同一件事的冷却
            last = self._last_by_subject.get(intent.subject)
            if last is not None and now - last < self.subject_interval:
                continue
            fit, why = context_fit(intent.subject, now)
            scored.append((intent, intent.base_priority * fit, fit, why))

        if not scored:
            return SchedulerDecision(reason="所有待办都在各自的话题冷却里")

        scored.sort(key=lambda t: t[1], reverse=True)
        considered = [(i.subject, s) for i, s, _, _ in scored]
        intent, score, fit, why = scored[0]

        # 分太低就是「现在不是时候」。没被选中不等于丢弃，
        # 它还在 pending 里，下次 tick 会重新算
        if score < 0.35:
            return SchedulerDecision(
                reason=f"最高分才 {score:.2f}（{why}），再等等",
                considered=considered,
                effective_score=score,
                context_fit=fit,
            )

        return SchedulerDecision(
            intent=intent, reason=why, effective_score=score,
            context_fit=fit, considered=considered,
        )

    def note_spoke(self, intent: Intent, now: datetime | None = None) -> None:
        """真的开口之后调一次，冷却从这一刻开始算。

        dry-run 阶段**也要调** —— 否则观察到的开口频率会比真实情况高得多，
        那就白观察了。
        """
        now = now or datetime.now(timezone.utc)
        self._last_spoke_at = now
        self._last_by_subject[intent.subject] = now

    # ------------------------------------------------------------ 状态

    def dump_state(self) -> dict[str, Any]:
        return {
            "last_spoke_at": self._last_spoke_at.isoformat() if self._last_spoke_at else None,
            "last_by_subject": {k: v.isoformat() for k, v in self._last_by_subject.items()},
        }

    def load_state(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        raw = state.get("last_spoke_at")
        self._last_spoke_at = datetime.fromisoformat(raw) if raw else None
        self._last_by_subject = {
            k: datetime.fromisoformat(v)
            for k, v in (state.get("last_by_subject") or {}).items()
        }
