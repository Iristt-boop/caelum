"""统一开口闸 —— 所有主动开口共用的「今天还能不能说话」。

## 为什么要有它（M5′ a，糖糖 2026-08-14 定的）

现在有三条主动开口的线，**互相不知道对方**：

    Care（bridge）      沉默 >4h 就发，时间窗 1-9 点
    Attention（Core）   concern → intent → Scheduler，睡眠窗 2-8.5 点
    唤醒链（Core）      她说完话他留纸条，到点自己醒来判断

症状（糖糖亲历）：她睡 4 小时，Nox 连着发三条差不多的「怎么没睡好」——
Care 一条、晨报一条、Attention 一条。不是他话多，是三套系统各说各的，
没有一个人知道别人今天已经说过话了。

## 这一层管什么、不管什么

**只管「今天还能不能开口」这个总量问题**：

    1. 每日额度    今天已经主动说过几次了？超过额度就闭嘴
    2. 安静时段    1-9 点（她睡觉）任何主动开口都静默

**不管**「这个话题现在合不合适」——那是 Scheduler 的 `context_fit`。
两层是正交的：Gate 说「今天还有额度」，Scheduler 说「这个话题现在说
正合适」，两个都过才开口。三条线（Care / Attention / 唤醒链）都过
**同一个** Gate，额度是共享的。

## 状态落盘

「今天说过几次」重启后必须还记得 —— 否则每次部署完 Nox 都觉得
自己今天没说过话，可以马上开口。状态存 attention.db 的 source_state
表（key="gate"），和 Scheduler 的状态放一起。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: 糖糖在中国，服务器在东京。判断「现在是不是她睡觉时间」必须按她的时区。
#: 固定 offset（中国 1991 年后无夏令时，UTC+8 恒定）—— 理由见 scheduler.py。
from temporal import LOCAL_TZ  # noqa: E402  ← 唯一定义在 temporal.py（审计 F1）

#: 每天最多主动开口几次（Care + Attention + 唤醒链 合计）
DAILY_QUOTA = 3

#: 安静时段：这个窗口内任何主动开口都静默。
#: 她凌晨 1-2 点睡、9-11 点起（CLAUDE.md）。1 点前她可能还醒着，
#: 9 点后开始陆续醒 —— 所以安静窗取 1:00-9:00，保守一点。
QUIET_START = time(hour=1)
QUIET_END = time(hour=9)

#: source_state 表里的 key
STATE_KEY = "gate"


@dataclass
class GateDecision:
    """一次「能不能开口」的完整回答。"""

    can_speak: bool
    reason: str = ""
    #: 今天已经说了几次 / 上限
    spoken_today: int = 0
    quota: int = DAILY_QUOTA

    def render(self) -> str:
        return f"[{'可以' if self.can_speak else '不行'}] {self.reason}（今天已说 {self.spoken_today}/{self.quota}）"


class DailyGate:
    """统一开口闸：每日额度 + 安静时段。线程安全（写操作带锁）。"""

    def __init__(
        self,
        daily_quota: int = DAILY_QUOTA,
        quiet_start: time = QUIET_START,
        quiet_end: time = QUIET_END,
    ) -> None:
        self.daily_quota = daily_quota
        self.quiet_start = quiet_start
        self.quiet_end = quiet_end
        #: 今天（本地日期）已经主动开口的次数。跨天清零。
        self._spoken: dict[str, int] = {}  # date.isoformat() -> count

    # ------------------------------------------------------------ 主流程

    def can_speak(self, now: datetime | None = None) -> GateDecision:
        """现在能不能主动开口。**只回答总量问题，不看话题。**"""
        now = now or datetime.now(timezone.utc)
        local = now.astimezone(LOCAL_TZ)
        today = local.date().isoformat()
        spoken = self._spoken.get(today, 0)

        # 安静时段：先查这个 —— 即使还有额度，她睡着的时候也不许打扰
        if self._in_quiet_hours(local):
            return GateDecision(
                False,
                f"安静时段（{self.quiet_start.strftime('%H:%M')}-"
                f"{self.quiet_end.strftime('%H:%M')}），不打扰",
                spoken, self.daily_quota,
            )

        if spoken >= self.daily_quota:
            return GateDecision(
                False, f"今日额度已用完（{spoken}/{self.daily_quota}）",
                spoken, self.daily_quota,
            )

        return GateDecision(True, "今天还有开口额度", spoken, self.daily_quota)

    def note_spoke(self, now: datetime | None = None) -> None:
        """真的开口之后调一次，今天额度 -1。"""
        now = now or datetime.now(timezone.utc)
        today = now.astimezone(LOCAL_TZ).date().isoformat()
        self._spoken[today] = self._spoken.get(today, 0) + 1
        # 顺手清掉昨天的记录（只留今天和昨天，防字典无限长）
        for d in [k for k in self._spoken if k != today]:
            del self._spoken[d]

    # ------------------------------------------------------------ 内部

    def _in_quiet_hours(self, local: datetime) -> bool:
        """跨午夜判断：QUIET_START <= t < QUIET_END，其中 1:00 前的凌晨也算。"""
        t = local.time().replace(tzinfo=None)
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= t < self.quiet_end
        # 跨午夜（start > end）：22:00-06:00 这种
        return t >= self.quiet_start or t < self.quiet_end

    # ------------------------------------------------------------ 状态

    def dump_state(self) -> dict[str, Any]:
        # 安静时段也带上 —— Settings → Notifications 页要把它摆到台面上，
        # 别让前端抄一份常量（抄了早晚会和这里漂移）
        return {
            "spoken": dict(self._spoken),
            "quota": self.daily_quota,
            "quiet_start": self.quiet_start.isoformat(),
            "quiet_end": self.quiet_end.isoformat(),
        }

    def load_state(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        self._spoken = {k: int(v) for k, v in (state.get("spoken") or {}).items()}
        # 配置变了就以配置为准（比如调了额度）
        self.daily_quota = int(state.get("quota", self.daily_quota))

    def reset(self) -> None:
        """清空今天的记录（测试/手工用）。"""
        self._spoken.clear()
