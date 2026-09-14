"""Temporal Resolver —— 「这个关系落在时间轴的哪里」。

契约见 `CAELUM-Temporal-Intent-Contract.md`。

## 它是确定性的，这一点很值钱

没有模型参与：输入 `(Intent, reference_time)`，输出 `Resolution`。
所以边界可以**穷举**着测，不花钱、不用等模型、结果可复现。
契约第八节那条 `23:58 → 00:02` 的变异就是靠这个杀掉的。

## 两条铁律

1. **`reference_time` 由调用方注入，永远是 `message_time`。**
   这里**不许**调 `temporal.now()` —— 理解层是异步的，
   拿"现在"当锚，她 23:58 说的「明天」会在 00:02 变成后天。
   和 `speaker._humanize` 原来按小时差算是同一个形状的错。
2. **算不出来就 `none`，不猜。** 猜错了没有痕迹。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from temporal import SLOTS, to_local
from temporal.intent import Intent

#: `precision` 的封闭集合。
#:
#: `upper_bound` 是 deadline 专用的一档 —— 见 `Resolution` 的注释，
#: 它**不是**一种"粗一点的 date"。
PRECISIONS = frozenset({"date", "datetime", "slot", "upper_bound", "none"})


@dataclass(frozen=True)
class Resolution:
    """解析的落点。**和 `Intent` 是两个对象，不许平铺合并**（契约第三节）。

    ## 🔴 deadline 为什么不给 `date`

    糖糖 2026-09-14 的提醒：

    > `deadline` 的 Resolution 最好明确成「上界」，
    > 不要为了方便把它当成普通事件时间。

    「下周五之前」**不是**「事情发生在下周五」。所以 deadline 解析出来
    `precision="upper_bound"`，`date` 和 `at` **都是 None**，
    只有 `upper_bound` 有值。

    这是**结构性**的保证，不是靠注释提醒：下游哪怕想把它当普通事件时间用，
    `.date` 读出来也是 None —— 想降格都没得读。
    """

    precision: str
    #: precision=date 时有
    date: date | None = None
    #: precision=datetime 时有
    at: datetime | None = None
    #: precision=slot 时有，左闭右开
    range: tuple[datetime, datetime] | None = None
    #: precision=upper_bound 时有。**只有 deadline 会填**
    upper_bound: datetime | None = None
    #: precision=none 时**必填**
    unresolved_reason: str | None = None

    def __post_init__(self) -> None:
        if self.precision not in PRECISIONS:
            raise ValueError(f"表外的 precision：{self.precision!r}")
        if self.precision == "none" and not self.unresolved_reason:
            raise ValueError("precision=none 必须说明为什么 —— 不然它和「没有时间表达」长得一样")
        if self.precision != "none" and self.unresolved_reason:
            raise ValueError("解析成功了就不该有 unresolved_reason")

    @property
    def ok(self) -> bool:
        """🔴 下游判「解析成功了没有」**只能**看这个。

        不许拿 `unresolved_reason is None` 当判据 —— 那是契约第五节说的
        「空集不是通过」的时间版本。
        """
        return self.precision != "none"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"precision": self.precision}
        if self.date is not None:
            d["date"] = self.date.isoformat()
        if self.at is not None:
            d["at"] = self.at.isoformat()
        if self.range is not None:
            d["range"] = [self.range[0].isoformat(), self.range[1].isoformat()]
        if self.upper_bound is not None:
            d["upper_bound"] = self.upper_bound.isoformat()
        if self.unresolved_reason:
            d["unresolved_reason"] = self.unresolved_reason
        return d


def _unresolved(reason: str) -> Resolution:
    return Resolution(precision="none", unresolved_reason=reason)


def _slot_range(day: date, slot: str, tz) -> tuple[datetime, datetime]:
    """那天的那个时段，左闭右开。

    深夜跨午夜（23:00 → 次日 05:00），所以不能写成简单的同日区间。
    """
    table = {en: (lo, hi) for lo, hi, en, _ in SLOTS}
    if slot in table:
        lo, hi = table[slot]
        return (datetime.combine(day, time(lo), tz), datetime.combine(day, time(hi), tz))
    # late_night：23:00 到次日 05:00
    return (datetime.combine(day, time(23), tz),
            datetime.combine(day + timedelta(days=1), time(5), tz))


def resolve(intent: Intent, reference_time: datetime) -> Resolution:
    """把关系落到时间轴上。

    `reference_time` **必须**是那句话的 `message_time`，带时区。
    naive 的直接抛（`to_local` 那条规矩，第一层立的）。
    """
    ref = to_local(reference_time)
    tz = ref.tzinfo
    kind = intent.kind

    # ---------------------------------------------------------- 日期类
    day: date | None = None

    if kind == "day_offset":
        day = ref.date() + timedelta(days=intent.n or 0)

    elif kind == "weekday_next":
        # 🔴 **下一日历周**的那天，不是"往后找最近的那天"（糖糖 2026-09-14 拍）。
        #
        # 不许写成：
        #     while candidate <= ref: candidate += timedelta(days=7)
        # 那个算的是「下一个未来星期 X」—— 两种算法**大部分日子结果相同**，
        # 只有 ref 早于本周那个星期几时才分叉，所以走歪了也不容易发现：
        #     ref=周一 求「下周三」→ 日历周给下周三，while 给**本**周三
        week_start = ref.date() - timedelta(days=ref.weekday())   # 本周一
        day = week_start + timedelta(days=7 + (intent.weekday or 1) - 1)

    elif kind == "weekday_bare":
        # 光秃秃的「周三」：今天周二时指明天，今天周四时多半指下周 ——
        # 系统无从知道她心里是哪个。**不猜。**
        return _unresolved("weekday_ambiguous")

    elif kind == "month_end":
        last = calendar.monthrange(ref.year, ref.month)[1]
        day = date(ref.year, ref.month, last)

    # ---------------------------------------------------------- 时刻类
    elif kind == "duration":
        delta = timedelta(days=intent.days or 0, hours=intent.hours or 0,
                          minutes=intent.minutes or 0)
        return Resolution(precision="datetime", at=ref + delta)

    # ---------------------------------------------------------- 上界
    elif kind == "deadline":
        if intent.before is None:
            return _unresolved("deadline_without_target")
        inner = resolve(intent.before, reference_time)
        if not inner.ok:
            # 歧义会**穿透包装器**：「周五之前」里的「周五」是歧义，
            # 整个 deadline 就是歧义。不许在这里偷偷给个默认
            return _unresolved(inner.unresolved_reason or "inner_unresolved")
        if inner.at is not None:
            bound = inner.at
        elif inner.date is not None:
            # 「X 之前」= 不晚于 X 那天结束。
            #
            # 🔶 这是一个**决定**，不是推导出来的：中文里「周五之前」
            # 基本都指「周五结束前」（含周五），而不是「周五开始前」。
            # 写在这里是为了它能被看见、被改 —— 不同意的话改这一行。
            bound = datetime.combine(inner.date + timedelta(days=1), time(0), tz)
        else:
            # 内层只有 slot 区间：取区间右端当上界
            bound = inner.range[1] if inner.range else None
            if bound is None:
                return _unresolved("deadline_target_has_no_bound")
        # ⚠️ date / at 都留 None —— 见 Resolution 的注释
        return Resolution(precision="upper_bound", upper_bound=bound)

    else:  # pragma: no cover —— Intent 的白名单挡在前面了
        return _unresolved(f"unknown_kind:{kind}")

    if day is None:  # pragma: no cover
        return _unresolved("no_date")

    # ---------------------------------------------------------- slot 修饰
    if intent.slot:
        # 「明天上午」= 那天的那一段，**不收成一个点**。
        # 把区间收成点是纯粹的编造（契约第五节判例③）
        return Resolution(precision="slot", date=day,
                          range=_slot_range(day, intent.slot, tz))

    return Resolution(precision="date", date=day)
