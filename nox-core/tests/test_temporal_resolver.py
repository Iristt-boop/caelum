"""Temporal Resolver（第二层）。契约见 `CAELUM-Temporal-Intent-Contract.md`。

## 为什么先写这一半

Resolver **不含模型**：输入 `(Intent, reference_time)`，输出 `Resolution`。
所以边界能穷举、不花钱、结果可复现。糖糖 2026-09-14 点名要盯的几个，
全在下面，而且都是确定性断言。

## 这些测试能挡什么

- `weekday_next` 被写成「往后找最近的那天」（**必须用周一当 reference 才分叉**）
- `reference_time` 被换成 `now()`（23:58 → 00:02 那条）
- `deadline` 被降格成普通事件日期
- slot 被收成一个时刻
- `precision: none` 被下游当成功
- 封闭集合被悄悄扩一个 kind

## 挡不住什么

- LLM 有没有把「明天」认成 `day_offset(+1)` —— 那是另一半（还没写）
- 语法够不够表达真实语言 —— 那要跑几天真实数据（shadow）
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from temporal import CST  # noqa: E402
from temporal.intent import KINDS, Intent  # noqa: E402
from temporal.resolver import Resolution, resolve  # noqa: E402


def ref(y=2026, m=9, d=14, hh=12, mm=0) -> datetime:
    """她那边的一个时刻。默认 2026-09-14 **周一** 12:00。"""
    return datetime(y, m, d, hh, mm, tzinfo=CST)


# ---------------------------------------------------------------- day_offset

@pytest.mark.parametrize("n, expect", [
    (0, date(2026, 9, 14)), (1, date(2026, 9, 15)),
    (-1, date(2026, 9, 13)), (2, date(2026, 9, 16)), (-2, date(2026, 9, 12)),
])
def test_day_offset(n, expect):
    r = resolve(Intent(kind="day_offset", n=n), ref())
    assert r.precision == "date" and r.date == expect


def test_day_offset_跨午夜前两分钟():
    """🔴 糖糖点名：2026-09-14 23:58 +1 day → 09-15。

    锚点是 `reference_time`（= message_time），不是「现在」。
    这条正是为了杀掉「把 reference 换成 now()」那个变异 ——
    她 23:58 说的「明天」，两分钟后解析仍然必须是 09-15。
    """
    r = resolve(Intent(kind="day_offset", n=1), ref(hh=23, mm=58))
    assert r.date == date(2026, 9, 15)


def test_day_offset_跨月跨年():
    assert resolve(Intent(kind="day_offset", n=1), ref(m=9, d=30)).date == date(2026, 10, 1)
    assert resolve(Intent(kind="day_offset", n=1), ref(y=2026, m=12, d=31)).date == date(2027, 1, 1)
    assert resolve(Intent(kind="day_offset", n=-1), ref(y=2026, m=1, d=1)).date == date(2025, 12, 31)


# ---------------------------------------------------------------- weekday_next

def test_weekday_next_周一reference是分叉点():
    """🔴 糖糖点名，而且这是唯一能抓住错误算法的 reference。

    `weekday_next` = **下一日历周**的那天，不是「往后找最近的那天」。

        ref = 2026-09-14 周一，求「下周三」
          日历周算法 ✅ → 09-23（下一周的周三）
          while 算法  ❌ → 09-16（**本**周三）

    两种算法在别的日子里结果相同，所以**不用周一当 reference，这条永远绿**。
    """
    r = resolve(Intent(kind="weekday_next", weekday=3), ref(d=14))   # 周一
    assert r.date == date(2026, 9, 23), "写成了「往后找最近的周三」"


def test_weekday_next_周日reference():
    """周日说「下周三」= 3 天后。两种算法在这里结果相同 ——
    所以它**证明不了**算法对，只能证明没写反。"""
    r = resolve(Intent(kind="weekday_next", weekday=3), ref(d=20))   # 周日
    assert r.date == date(2026, 9, 23)


@pytest.mark.parametrize("weekday, expect", [
    (1, date(2026, 9, 21)), (3, date(2026, 9, 23)),
    (5, date(2026, 9, 25)), (7, date(2026, 9, 27)),
])
def test_weekday_next_一整周(weekday, expect):
    """ref 固定在周一，下一日历周是 09-21(一) ~ 09-27(日)。"""
    assert resolve(Intent(kind="weekday_next", weekday=weekday), ref(d=14)).date == expect


# ---------------------------------------------------------------- 歧义

def test_光秃秃的周几是歧义():
    """🔴 「周五去」= 这周五还是下周五？系统无从知道 —— **不猜**。"""
    r = resolve(Intent(kind="weekday_bare", weekday=5), ref())
    assert not r.ok
    assert r.precision == "none"
    assert r.unresolved_reason == "weekday_ambiguous"
    assert r.date is None and r.at is None


def test_none_必须带原因():
    """「没有时间表达」和「有时间表达但解析不了」长得一样，必须分开。"""
    with pytest.raises(ValueError, match="必须说明为什么"):
        Resolution(precision="none")


def test_ok_是唯一判据():
    """🔴 不许拿 `unresolved_reason is None` 当判据（空集不是通过）。"""
    bad = resolve(Intent(kind="weekday_bare", weekday=5), ref())
    good = resolve(Intent(kind="day_offset", n=1), ref())
    assert bad.ok is False and good.ok is True


# ---------------------------------------------------------------- month_end

@pytest.mark.parametrize("y, m, expect_day", [
    (2026, 2, 28), (2024, 2, 29),          # 🔴 闰年
    (2026, 4, 30), (2026, 12, 31), (2026, 1, 31),
])
def test_month_end(y, m, expect_day):
    """糖糖点名：2月 / 4月 / 12月。闰年那条单独一行。"""
    r = resolve(Intent(kind="month_end"), ref(y=y, m=m, d=5))
    assert r.precision == "date" and r.date == date(y, m, expect_day)


# ---------------------------------------------------------------- duration

def test_duration_跨午夜():
    """🔴 糖糖点名：23:58 + 2h → 01:58，**日期要跟着跨过去**。"""
    r = resolve(Intent(kind="duration", hours=2), ref(d=14, hh=23, mm=58))
    assert r.precision == "datetime"
    assert r.at == datetime(2026, 9, 15, 1, 58, tzinfo=CST)


def test_duration_各单位():
    assert resolve(Intent(kind="duration", minutes=10), ref(hh=12)).at.minute == 10
    assert resolve(Intent(kind="duration", days=3), ref()).at.date() == date(2026, 9, 17)


def test_duration_不给date():
    """它落在一个**时刻**上，不是一天。下游要 date 的自己去截。"""
    assert resolve(Intent(kind="duration", hours=2), ref()).date is None


# ---------------------------------------------------------------- slot

def test_slot_给区间不给点():
    """🔴 糖糖点名：evening → [18:00, 23:00)，**没有 at**。

    时段边界在 `temporal.SLOTS`（第一层）按她的作息定死了。
    把区间收成一个点是纯粹的编造。
    """
    r = resolve(Intent(kind="day_offset", n=-1, slot="evening"), ref())
    assert r.precision == "slot"
    assert r.at is None, "slot 被收成了一个时刻"
    assert r.range == (datetime(2026, 9, 13, 18, tzinfo=CST),
                       datetime(2026, 9, 13, 23, tzinfo=CST))


def test_slot_深夜跨午夜():
    """23:00 → 次日 05:00。写成同日区间的话它会是个空区间。"""
    r = resolve(Intent(kind="day_offset", n=0, slot="late_night"), ref())
    lo, hi = r.range
    assert lo == datetime(2026, 9, 14, 23, tzinfo=CST)
    assert hi == datetime(2026, 9, 15, 5, tzinfo=CST)
    assert hi > lo, "深夜区间是空的 —— 多半写成了同一天"


def test_slot_仍然带date():
    """区间是那天的，date 也该留着给只要日期的下游。"""
    assert resolve(Intent(kind="day_offset", n=1, slot="morning"), ref()).date == date(2026, 9, 15)


# ---------------------------------------------------------------- deadline

def test_deadline_是上界不是事件时间():
    """🔴 糖糖点名：「下周五之前」**不是**「事情发生在下周五」。

    所以 `date` 和 `at` 都是 None —— 这是**结构性**保证：
    下游哪怕想把它当普通事件时间用，也没得读。
    """
    r = resolve(Intent(kind="deadline",
                       before=Intent(kind="weekday_next", weekday=5)), ref(d=14))
    assert r.precision == "upper_bound"
    assert r.date is None and r.at is None, "deadline 被降格成了普通事件时间"
    # 「下周五之前」= 不晚于 09-25 结束
    assert r.upper_bound == datetime(2026, 9, 26, 0, 0, tzinfo=CST)


def test_deadline_里的歧义会穿透():
    """「周五之前」里的「周五」是歧义 → 整个 deadline 是歧义。
    不许在包装器里偷偷给个默认。"""
    r = resolve(Intent(kind="deadline",
                       before=Intent(kind="weekday_bare", weekday=5)), ref())
    assert not r.ok
    assert r.unresolved_reason == "weekday_ambiguous"


def test_deadline_套时刻类():
    """「两小时之内」→ 上界就是那个时刻本身。"""
    r = resolve(Intent(kind="deadline",
                       before=Intent(kind="duration", hours=2)), ref(hh=12))
    assert r.upper_bound == datetime(2026, 9, 14, 14, tzinfo=CST)


# ---------------------------------------------------------------- 封闭集合

def test_kind_是封闭集合():
    """🔴 契约第八节点名的假绿之一：加一个表外分支**不会让任何测试红**。

    所以反过来断言 —— 把允许的集合写死，多一个就红。
    """
    assert KINDS == {"day_offset", "weekday_next", "weekday_bare",
                     "month_end", "duration", "deadline"}


def test_表外的kind直接抛():
    with pytest.raises(ValueError, match="表外的 kind"):
        Intent(kind="next_month")


@pytest.mark.parametrize("bad", [
    dict(kind="day_offset", weekday=3),          # 带了不属于它的字段
    dict(kind="month_end", n=1),
    dict(kind="day_offset"),                     # 缺必填
    dict(kind="weekday_next", weekday=8),        # 越界
    dict(kind="duration", hours=2, slot="morning"),   # 时刻类不能带 slot
    dict(kind="day_offset", n=1, slot="午夜"),   # 不认识的时段
])
def test_字段组合非法就抛(bad):
    with pytest.raises(ValueError):
        Intent(**bad)


def test_from_dict_拒绝不认识的字段():
    """这是封闭集合的入口 —— 在这里放行的东西下游全会当真。"""
    with pytest.raises(ValueError, match="不认识的字段"):
        Intent.from_dict({"kind": "day_offset", "n": 1, "resolved_date": "2026-09-15"})


def test_intent_里没有任何_resolved_字段():
    """🔴 LLM 就算想顺手算个日期，也没地方放 —— 这比提示词里写「别算」硬。"""
    fields = set(Intent(kind="day_offset", n=1).to_dict())
    assert not any(f.startswith("resolved") for f in fields)
    assert fields == {"kind", "n"}


# ---------------------------------------------------------------- 锚点

def test_reference_必须带时区():
    with pytest.raises(ValueError, match="必须带时区"):
        resolve(Intent(kind="day_offset", n=1), datetime(2026, 9, 14, 12))


def test_reference_换算到她那边():
    """库里存 UTC。UTC 09-14 16:30 = CST 09-15 00:30，「明天」该是 09-16。"""
    utc = datetime(2026, 9, 14, 16, 30, tzinfo=timezone.utc)
    assert resolve(Intent(kind="day_offset", n=1), utc).date == date(2026, 9, 16)


def test_resolve_不碰现在():
    """🔴 Resolver 里不许调 `temporal.now()`。

    理解层是异步的，拿"现在"当锚，她 23:58 说的「明天」会在 00:02
    变成后天。断言方式：同一个 intent + 同一个 reference，
    **在任何时候调用都给同一个答案**。
    """
    i = Intent(kind="day_offset", n=1)
    r1 = resolve(i, ref(hh=23, mm=58))
    r2 = resolve(i, ref(hh=23, mm=58))
    assert r1.date == r2.date == date(2026, 9, 15)

    import inspect
    import temporal.resolver as mod
    src = inspect.getsource(mod.resolve)
    assert "now()" not in src, "Resolver 里出现了 now() —— 锚点必须由调用方注入"


# ---------------------------------------------------------------- 往返

@pytest.mark.parametrize("intent", [
    Intent(kind="day_offset", n=1),
    Intent(kind="day_offset", n=-1, slot="evening"),
    Intent(kind="weekday_next", weekday=3),
    Intent(kind="month_end"),
    Intent(kind="duration", hours=2, minutes=30),
    Intent(kind="deadline", before=Intent(kind="weekday_next", weekday=5)),
])
def test_intent_序列化往返(intent):
    """shadow 日志要把原始 intent 记下来，读得回来才有意义。"""
    assert Intent.from_dict(intent.to_dict()) == intent


# ---------------------------------------------------------------- 上界的比较语义
#
# 糖糖 2026-09-14 钉的：
#   「周五之前」→ upper_bound = 周五次日 00:00
#                → 语义：**严格早于**这个时间点，但包含整个周五
# 差一个等号就把次日 00:00 那一瞬间也算进来了，所以比较收在
# `Resolution.within()` 一处，下游不许自己写。


def _friday_deadline():
    """「下周五之前」，ref = 2026-09-14 周一 → 下周五是 09-25。"""
    return resolve(Intent(kind="deadline",
                          before=Intent(kind="weekday_next", weekday=5)), ref(d=14))


@pytest.mark.parametrize("when, inside, why", [
    (datetime(2026, 9, 25, 0, 0, tzinfo=CST),  True,  "周五一开始就在里面"),
    (datetime(2026, 9, 25, 23, 59, tzinfo=CST), True,  "整个周五都算"),
    (datetime(2026, 9, 26, 0, 0, tzinfo=CST),  False, "次日 00:00 是右开的那一端"),
    (datetime(2026, 9, 26, 0, 1, tzinfo=CST),  False, "过了就是过了"),
    (datetime(2026, 9, 14, 12, 0, tzinfo=CST), True,  "之前的时刻当然也满足"),
])
def test_上界是右开的(when, inside, why):
    assert _friday_deadline().within(when) is inside, why


def test_上界差一个等号就错():
    """🔴 这条专门盯 `<` 被写成 `<=`。

    次日 00:00 那一瞬间：`<` 排除，`<=` 包含。
    只测周五中间的时刻是抓不到的 —— 那种断言两种写法都过。
    """
    r = _friday_deadline()
    boundary = r.upper_bound
    assert r.within(boundary) is False, "上界用了 <=，把边界那一瞬间也算进来了"
    assert r.within(boundary - timedelta(microseconds=1)) is True


def test_slot_区间左闭右开():
    r = resolve(Intent(kind="day_offset", n=0, slot="evening"), ref())
    lo, hi = r.range
    assert r.within(lo) is True
    assert r.within(hi) is False, "区间右端应该是开的"


def test_date_精度问的是同一天():
    r = resolve(Intent(kind="day_offset", n=1), ref())
    assert r.within(datetime(2026, 9, 15, 3, tzinfo=CST)) is True
    assert r.within(datetime(2026, 9, 16, 3, tzinfo=CST)) is False


def test_没解析出来的结果不许被消费():
    """🔴 消费一个 precision=none 正是要抓的 bug —— 抛，不静悄悄返回 False。

    返回 False 的话它长得和「解析出来了但不在范围里」一模一样。
    """
    bad = resolve(Intent(kind="weekday_bare", weekday=5), ref())
    with pytest.raises(ValueError, match="不该被消费"):
        bad.within(ref())


def test_时刻类问不了落不落在里面():
    """`duration` 是一个点，不是范围。"""
    r = resolve(Intent(kind="duration", hours=2), ref())
    with pytest.raises(ValueError, match="是一个时刻"):
        r.within(ref())
