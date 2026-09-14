"""把「今天不去，明天再去」变成一个待办决策（审计 F8）。

## 它要拆开的那件事

糖糖 2026-09-14：

> 「没有完成」≠「继续现在这个提醒策略」

现在 `todo_due.py` 把这两件揉在一起了。它开头那句
「她说一句『在忙』并不代表运动做了」说的是**不能标完成** ——
它没说不能改下次介入时间。正确的形状是：

    Todo：          状态 = 未完成        ← 不动
    Intervention：  下一次介入 = 明天     ← 只改这个

## 这个模块是**纯函数**

输入 `(Resolution, 在追的待办, today)`，输出一个决策。
没有 IO、没有模型、不产生副作用 —— 所以能穷举着测。
真正去调 bridge 的是调用方，而且第一版**根本不调**（shadow）。

## 第一版会发现的事

现有 bridge 只有 `/api/todo/fired`（「今天追过了」，跨天自动失效）。
它刚好够表达「推到明天」，**再远的推迟没有地方放** ——
`deferred_until` 是 bridge 那边要加的字段。
所以决策里会区分这两种 mechanism，让 shadow 日志直接告诉我们
「真接上的话，有多少比例是现有机制够用的」。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from temporal.resolver import Resolution

#: 现有机制够用：`/api/todo/fired` 标一下今天追过了，跨天自动失效
MECH_FIRED = "mark_fired"
#: 需要 bridge 加 `deferred_until` 字段才做得到
MECH_NEEDS_FIELD = "needs_deferred_until"


@dataclass(frozen=True)
class DeferDecision:
    """要不要推迟、推到哪天、用什么机制。

    ⚠️ **`should_defer=False` 时 `why_not` 必填** —— 同 `TemporalResult`
    那条：说不清为什么没做的决策，在日志里等于没记。
    """

    should_defer: bool
    todo_id: str | None = None
    until: date | None = None
    mechanism: str | None = None
    why_not: str | None = None

    def __post_init__(self) -> None:
        if not self.should_defer and not self.why_not:
            raise ValueError("不推迟就要说明为什么")
        if self.should_defer and not (self.todo_id and self.until and self.mechanism):
            raise ValueError("要推迟就得说清楚：哪条、推到哪天、用什么机制")


def _no(reason: str) -> DeferDecision:
    return DeferDecision(should_defer=False, why_not=reason)


def decide(resolution: Resolution, *, todo_id: str | None, today: date) -> DeferDecision:
    """她这句话该不该改这条待办的下次介入时间。

    **不改状态。** 返回值里没有任何「标完成」的可能 —— 那是结构性的：
    这个模块产出的东西压根表达不了「完成」。
    """
    if not todo_id:
        #: 她说了个时间，但当时没有在追的待办 —— 常态，不是错。
        #: 记下来是为了知道「真接上的话，有多少次是能对上号的」
        return _no("当时没有在追的待办")

    #: 🔴 判据是 `ok`，不是 `unresolved_reason is None`（空集不是通过）
    if not resolution.ok:
        return _no(f"时间没解析出来：{resolution.unresolved_reason}")

    if resolution.date is None:
        #: `duration`（两小时后）和 `deadline`（周五之前）都落在这里。
        #: 待办是按**天**追的，给它一个时刻或一个上界都对不上 ——
        #: 宁可不动，也不要把「两小时后」硬取整成一天
        return _no(f"精度对不上（{resolution.precision}）—— 待办按天追，需要 date")

    until = resolution.date
    if until <= today:
        #: 「今天再说」「昨天就该做了」—— 那不是推迟
        return _no("没有往后推（还是今天或更早）")

    #: 推到明天：现有的 `/api/todo/fired` 就够 —— 它标「今天追过了」，
    #: 跨天自动失效，明天那条链会重开
    #: 再远的：`fired` 表达不了，需要 bridge 加 `deferred_until`
    mech = MECH_FIRED if until == today + timedelta(days=1) else MECH_NEEDS_FIELD

    return DeferDecision(should_defer=True, todo_id=todo_id,
                         until=until, mechanism=mech)
