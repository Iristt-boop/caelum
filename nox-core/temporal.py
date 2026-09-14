"""Temporal Semantics —— 全系统共享的时间语义地基。

## 为什么有这个模块（糖糖 2026-09-14 定的方向）

审计（`CAELUM-时间模型审计-2026-09-14.md`）的结论不是「哪里写错了」，是：

> CAELUM 已经有时间语义模型，但这个模型没有成为全系统共享的基础设施。

具体形状：`timezone(timedelta(hours=8))` 在仓库里被**独立定义了 8 次**，
三个名字（`CST` / `LOCAL_TZ` / `_CST`）指同一个东西；「今天是哪天」有四种算法；
「几天前」有两套互不知道的实现，其中一套还是按小时差算的。

她的原话：

> 以后 Todo 不需要自己理解「明天」，Memory 不需要自己理解「昨天」，
> Speaker 也不应该自己理解「几个小时以前」。它们只消费已经解析好的时间语义。

**这一版只做第一层：时区 / 日历天 / 相对时间。**
第二层（`event_time` 进理解层）、第三层（Todo 的 deferred）、
第四层（Recurrence / Deadline）都还没做，形状见审计文档第六节。

## 三种时间不是一回事（这是整件事的核心）

```
message_time   这句话是什么时候说的        data/store.py:55 created_at
observed_at    这个事实是什么时候被观察到   world_model/types.py:36 ← 已经做对了
event_time     这句话指向的事情什么时候发生  ❌ 还不存在
```

    9月14日 14:00，她说「我昨天去健身了」
        message_time = 09-14 14:00
        event_time   = 09-13          ← 现在没人算，模型只能临场猜

本模块**不负责**解析 `event_time`（那是第二层，要走理解层），
只负责把「算时间」这件事的地基铺平，让第二层有地方落。

## 边界：只有这里能造 UTC+8

`scripts/check-boundaries.sh` 的 R9 盯着这条。别在别处再写
`timezone(timedelta(hours=8))` —— 那是 F1 长回来的方式。

要支持她出国、或者把「她的一天」从 00:00 挪到 04:00（她凌晨才睡），
改这里一处就够。散在八处的话，改十个地方漏一个不报错。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# ---------------------------------------------------------------- 时区

#: 🔴 **全仓唯一的 UTC+8 定义。** 别处不许再造（check-boundaries R9）。
#:
#: 用固定偏移而不是 `ZoneInfo("Asia/Shanghai")`：Windows 上 zoneinfo 要额外装
#: tzdata，少一个依赖少一处会炸的地方。中国不用夏令时，+8 永远成立。
#:
#: 为什么不用机器本地时间：本地开发在 Windows（台北）、线上在阿里云新加坡
#: （Asia/Shanghai），眼下两边都是 +8 所以看不出问题。但哪天服务器重装成 UTC，
#: 跨天判断就整体偏 8 小时 —— **而且不会报错**，只会让他在她上午聊天时说「该睡了」。
CST = timezone(timedelta(hours=8), "CST")

#: 历史别名。`attention/` 那几个文件里叫 LOCAL_TZ，保留名字省掉几十处改动 ——
#: 要治的是「定义有八份」，不是「名字有两个」。
LOCAL_TZ = CST


def now() -> datetime:
    """当下，带时区。**所有「现在」都该从这里来。**"""
    return datetime.now(CST)


def to_local(dt: datetime) -> datetime:
    """换算到她那边的时间。

    🔴 **naive 直接抛，不猜。** `datetime.astimezone()` 对 naive 值会
    **按机器本地时区**解释 —— 线上机器恰好是 +8，所以猜对了，
    但那是巧合不是设计（审计 F3）。同一条规矩 `world_model/types.py:46`
    和 `attention/events.py:103` 早就立过，这里是第三处，也是唯一共享的一处。
    """
    if dt.tzinfo is None:
        raise ValueError(f"时间必须带时区，不许猜：{dt!r}")
    return dt.astimezone(CST)


# ---------------------------------------------------------------- 日历天
#
# 🔴 「今天/昨天」是**日历概念**，不是「过去了多少小时」。
# 这不是边界 bug，是错的时间模型（糖糖 2026-09-14 判的）：
#
#     23:59 → 00:01     过了 2 分钟，已经是「昨天」
#     02:00 → 23:00     过了 21 小时，仍然是「今天」
#
# `attention/speaker.py` 原来就是按小时差算的（`hours < 20 → 今天`），
# 两头都会错。下面这几个函数是唯一正确的口径。


def today(at: datetime | None = None) -> date:
    """她那边的今天。"""
    return to_local(at).date() if at else now().date()


def same_day(a: datetime, b: datetime) -> bool:
    """两个时刻是不是她那边的同一天。"""
    return to_local(a).date() == to_local(b).date()


def days_between(when: datetime, ref: datetime | None = None) -> int:
    """相差几个**日历天**（正数 = when 在过去）。

    ⚠️ 不是 `(ref - when).days` —— 那个算的是 24 小时的整数倍。
    """
    return (to_local(ref or now()).date() - to_local(when).date()).days


# ---------------------------------------------------------------- 时段

#: 时段边界按糖糖的真实作息划，不是常识里的早中晚：
#: 她凌晨 1-2 点睡、早上 9-11 点起，所以 23 点对她还是「晚上」不是「深夜」。
SLOTS = [
    (5, 12, "morning", "上午"),
    (12, 18, "afternoon", "下午"),
    (18, 23, "evening", "晚上"),
]


def slot_of(hour: int) -> tuple[str, str]:
    """小时 → (英文槽位, 中文槽位)。落在表外的都是深夜。"""
    for lo, hi, en, cn in SLOTS:
        if lo <= hour < hi:
            return en, cn
    return "late_night", "深夜"


# ---------------------------------------------------------------- 相对时间
#
# ⚠️ 这几个只给**每次都重新生成**的地方用：工具结果、dynamic_system、prompt。
# **绝不要拿它去改历史消息** —— 历史是冻住的，今天写进去的「4天前」后天再读
# 还是「4天前」；而且相对时间每天变一次，会让整段历史的缓存每天失效一次
# （DeepSeek 是自动前缀匹配）。详见 `context/timeline.py` 开头那两条。
#
# 糖糖 2026-09-14 把这条提炼成了一句原则：
#   **存储层应该尽可能稳定、绝对；展示层才负责相对化。**


def humanize(days: int) -> str:
    """把「几天前」说成人话。

    粒度是**故意粗的**：她问「美甲是什么时候做的」，
    「上周」比「7 天前」更像人说的话。真要精确，绝对日期就在旁边。
    """
    if days < 0:
        return "以后"
    if days == 0:
        return "今天"
    if days == 1:
        return "昨天"
    if days == 2:
        return "前天"
    if days < 7:
        return f"{days}天前"
    if days < 14:
        return "上周"
    if days < 30:
        return f"{days // 7}周前"
    if days < 60:
        return "上个月"
    if days < 365:
        return f"{days // 30}个月前"
    return f"{days // 365}年多前"


def relative(when: datetime, ref: datetime | None = None) -> str:
    """两个时刻之间的人话说法。`humanize` 的时刻版。

    合并了原来的两套实现（审计 F6）：
      · `context/timeline.humanize(days)` —— 一直是对的，按天数入参
      · `attention/speaker._humanize(when, now)` —— 按小时差，**错的模型**

    ⚠️ 合并后 speaker 那条路的措辞会有两处小变化（都在它的 7 天窗口内）：
        「2 天前」→「前天」   「3 天前」→「3天前」（少一个空格）
    两边现在共用一张词表，不会再各说各的。
    """
    return humanize(days_between(when, ref))
