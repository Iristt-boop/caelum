"""给历史消息标上日期。

## 要解决的事（糖糖 2026-08-11 报的）

> 「nox 现在对时间没有概念。我前几天做的美甲他会一直以为是今天做的。」

根因不在他的推算能力，在于**那个数字根本没给他**。
`data/store.py` 的 `load()` 原来是：

    SELECT role, text FROM messages ...

`messages` 表**存了 `created_at`，读的时候扔了**。所以进他上下文的历史长这样：

    她：我今天去做美甲了
    他：好看吗
    她：（其实是四天后）……

他看到「我今天去做美甲了」，那个"今天"就是他能拿到的全部信息，
而他手上唯一的时间是 TimeProvider 给的【此刻】= 今天。
两条一拼，美甲就是今天做的。

## 做法：日期分隔线，不是每条盖章

    （这段对话开始于 8月7日 周四）
    我今天去做美甲了
    ---
    好看吗
    ---
    （8月11日 周一）
    晚上吃什么

每条都盖章要 40 条 × 8 token；只在**换天**时插一行，通常 3-5 行就够。

## ⚠️ 只用绝对日期，绝不用「4天前」

这是这个文件最重要的一条约束，有两个独立理由：

**1. 相对时间会腐烂。** 历史消息是冻住的 —— 今天写进去的「4天前」，
   后天再读还是「4天前」，而实际已经六天前了。
   把一个会过期的值写进一个不会更新的地方，比不写更糟。

**2. 相对时间砸缓存。** DeepSeek 的提示词缓存是自动前缀匹配
   （`context/providers/time.py:78` 那段注释）：历史里任何一个字变了，
   它后面的全部错位、一起不命中。绝对日期写死不变，每天重算的相对时间
   则会让整段历史每天失效一次。

相对时间只出现在**每次都重新生成**的地方 —— 工具结果（记忆检索）和
`dynamic_system`。那两个地方本来就不进缓存前缀。

他要算「几天前」，用这里的绝对日期减 TimeProvider 的【此刻】就行 ——
那是他擅长的，前提是两个数都在手上。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from agent.llm import Message
from personality.mood import CST, now_cst
from temporal import humanize, relative, slot_of  # noqa: F401  ← re-export，定义在 temporal.py

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

#: 时段划分和相对时间都从 `temporal` 取（2026-09-14，审计 F1/F6）。
#: 这里 re-export 只是为了不改动下游的 import —— **定义只有一份**。
#: 原来这份是 09-14 当天从 `providers/time.py` 搬过来的，
#: 搬完当天就发现该搬得更远：它不属于 Context 层，属于时间语义层。

#: 同一天里隔多久就重新报一次时间。
#:
#: 🔴 **为什么需要日内标记**（糖糖 2026-09-14 报的，这是第二次了）：
#: 2026-08-11 那次加日期分隔线，治的是「前几天做的美甲他以为是今天」。
#: 但它只在**换天**时插一行 —— 于是同一天内的消息之间一个时间都没有。
#: 她上午说的话，到了下午他会说成「昨天聊的」，因为
#: 「上午说的」和「昨天说的」在他眼里长得一模一样：都是一句没有时间的话。
#:
#: 实测（她 2026-09-14 的会话）：22 条消息全在同一天，跨度 14:01~15:47，
#: 中间有一个一小时的空档 —— 而他看到的只有最上面孤零零一行「（9月14日）」。
#:
#: 45 分钟这个数：一段对话的自然停顿通常几分钟到十几分钟，不该打断；
#: 而她去睡个午觉、出门一趟回来，基本都超过它。
GAP = timedelta(minutes=45)


def _cn(dt: datetime) -> datetime:
    """统一换算到中国时间再判断「哪一天」。

    库里存的是 UTC。不换算的话，她晚上 8 点之后说的话会被算成第二天 ——
    而那正是她最常聊天的时段。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CST)


def _label(dt: datetime) -> str:
    """⚠️ 第一条**不能**写成「这段对话开始于 X」。

    历史只取最近 40 条，第一条是**窗口**的起点，不是**会话**的起点。
    2026-08-11 部署时实测：一个 8月6日开始的会话，窗口第一条是 8月7日的，
    标成「这段对话开始于 8月7日」就是在骗他 —— 而且骗得理直气壮。

    会话真正开始于什么时候，由 `nox._session_span()` 那行讲，
    它读的是 `sessions.created_at`，不受窗口影响。**各说各的事实，不重叠。**

    ⚠️ **日内标记也带完整日期**（2026-09-14）。写成「（下午3点）」会省几个字，
    但那要求他自己往回找最近一行日期才知道是哪天 —— 而**他做不好的恰恰是这件事**，
    整个 bug 就是这么来的。每一行都自足，多几个 token 换掉一整类错误。
    """
    d = _cn(dt)
    _, cn = slot_of(d.hour)
    return f"（{d.month}月{d.day}日 {_WEEKDAYS[d.weekday()]} {cn}{d.hour}点）"


def with_dates(rows: Iterable[tuple[str, str | None, str | None]]) -> list[Message]:
    """把 (role, text, created_at) 拼成带日期分隔线的历史。

    分隔线**贴在那天第一条消息的正文前面**，不单独发一条 system 消息 ——
    历史中间插 system 各家 adapter 支持度不一，贴正文前是最稳的做法，
    而且它跟着那条消息一起冻住，缓存友好。

    `created_at` 解析不出来就跳过标注，不抛 —— 少一个日期总比整段历史读不出来好。

    ## 什么时候插一行（2026-09-14 起三个条件，命中任一）

    1. **换天** —— 原来只有这一条
    2. **跨时段**（上午→下午→晚上→深夜）—— 接住「聊了一整天没断」的情况：
       没有大空档，但 12 点一过就该让他知道现在是下午了
    3. **隔了 `GAP`（45 分钟）** —— 接住「她去睡了个午觉」这种

    2 和 3 是互补的，少哪个都有漏网：只看空档，连续聊到天黑他还以为是上午；
    只看时段，14:00 睡到 16:00 中间没跨时段就不标。
    """
    out: list[Message] = []
    last_day = None
    last_slot = None
    last_at: datetime | None = None

    for role, text, created_at in rows:
        stamp = ""
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at)
            except ValueError:
                dt = None
            if dt is not None:
                local = _cn(dt)
                day = local.date()
                slot, _ = slot_of(local.hour)
                # 顺序无关：三个条件任一命中就报一次时间
                if (day != last_day
                        or slot != last_slot
                        or (last_at is not None and local - last_at >= GAP)):
                    stamp = _label(dt) + "\n"
                last_day, last_slot, last_at = day, slot, local

        out.append(Message(role=role, text=(stamp + (text or "")) if stamp else text))

    return out


# ------------------------------------------------------------------ 相对时间
#
# 只给**每次都重新生成**的东西用：工具结果（记忆检索）和 dynamic_system。
# 那两个不进缓存前缀，也不会冻在历史里，所以相对时间在这里是安全的。
# 绝不要拿它去改历史消息 —— 理由见本文件开头那两条。


#: 记忆正文里出现过的所有日期写法（实际抓线上数据看出来的，不是猜的）：
#:   2026-08-08 / 2026.07.09-10 / 2026年6月17日 / 2026-07-10晚 / 8月7日
#: ⚠️ 年份可选 —— 缺年份时按当年算，这是记日记的人默认的意思
_DATE = re.compile(
    r"(?<![\d-])"                      # 左边不能贴着数字（别切进 bucket_id）
    r"(?:(20\d{2})[-./年])?"           # 年，可选
    r"(\d{1,2})[-./月]"                # 月
    r"(\d{1,2})"                       # 日
    r"日?"
    r"(?![\d]*[-./]\d)"                # 右边不能再接日期分量
)


def relativize(text: str, today: date | None = None) -> str:
    """给正文里的日期就地补上「（3天前）」。

    **补，不是替换。** 绝对日期必须留着：
      - 相对时间会腐烂，绝对的不会
      - 她问「具体哪天」时他还答得出来

    没有日期的记忆就**不管** —— 不编。线上很多桶压根没写日期
    （「糖糖睡前撒娇要拥抱」），给它硬安一个只会让他说错。
    """
    today = today or now_cst().date()

    def repl(m: re.Match) -> str:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return m.group(0)
        try:
            when = date(int(y) if y else today.year, mo, d)
        except ValueError:
            return m.group(0)          # 2月30日这种，原样放过
        gap = (today - when).days
        # 没写年份又算出来是未来 → 多半是去年的事（12 月的记忆在 1 月读）
        if not y and gap < 0:
            try:
                when = when.replace(year=when.year - 1)
                gap = (today - when).days
            except ValueError:
                return m.group(0)
        if gap < 0 or gap > 3650:
            return m.group(0)          # 明显不是在说日期，别乱标
        return f"{m.group(0)}（{humanize(gap)}）"

    return _DATE.sub(repl, text)
