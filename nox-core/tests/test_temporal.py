"""时间语义地基（`temporal.py`，审计第一层：F1 + F2 + F6）。

## 这些测试能挡什么

- 「今天/昨天」退回按小时差算（**错的时间模型**，不是边界 bug）
- 相对时间又分叉成两套（timeline 一套、speaker 一套）
- `to_local` 对 naive 值开始猜（那会把 F3 变成常态）
- 时段边界被改动

## 挡不住什么

- UTC+8 被别处重新定义 —— 那条靠 `scripts/check-boundaries.sh` 的 R9
  （测试查不了"全仓有没有第二份定义"，哨兵能）
- `event_time` 还不存在（第二层，还没做）
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import temporal  # noqa: E402
from temporal import (  # noqa: E402
    CST, LOCAL_TZ, days_between, humanize, now, relative, same_day, slot_of,
    to_local, today,
)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """造一个**她那边**的时刻。判据永远是她那边是哪天，不是 UTC 哪天。"""
    return datetime(2026, 9, day, hour, minute, tzinfo=CST)


# ---------------------------------------------------------------- 日历天

def test_今天昨天是日历概念不是小时差():
    """🔴 这是整个第一层的核心。

    `attention/speaker.py` 原来按 `hours < 20 → 今天` 算，两头都错：
      · 02:00 → 23:00   过了 21 小时，**仍然是今天**
      · 23:59 → 00:01   过了 2 分钟，**已经是昨天**
    """
    assert relative(at(14, 2), at(14, 23)) == "今天", "同一天被说成昨天"
    assert relative(at(13, 23, 59), at(14, 0, 1)) == "昨天", "过了 2 分钟却还说今天"


def test_days_between_数的是日历天不是24小时整数倍():
    assert days_between(at(14, 2), at(14, 23)) == 0      # 21 小时，同一天
    assert days_between(at(13, 23), at(14, 1)) == 1      # 2 小时，跨了一天
    assert days_between(at(11, 12), at(14, 12)) == 3


def test_same_day_和_today_口径一致():
    assert same_day(at(14, 0, 1), at(14, 23, 59))
    assert not same_day(at(13, 23, 59), at(14, 0, 1))
    assert today(at(14, 3)) == at(14, 3).date()


def test_跨时区的时刻按她那边算():
    """库里存 UTC。不换算的话她晚上 8 点之后说的话会被算成第二天 ——
    而那正是她最常聊天的时段。"""
    utc_late = datetime(2026, 9, 14, 16, 30, tzinfo=timezone.utc)   # CST 09-15 00:30
    utc_early = datetime(2026, 9, 14, 15, 30, tzinfo=timezone.utc)  # CST 09-14 23:30
    assert not same_day(utc_early, utc_late), "跨了中国时间的午夜却算成同一天"
    assert to_local(utc_late).day == 15


# ---------------------------------------------------------------- naive

@pytest.mark.parametrize("fn, args", [
    (to_local, (datetime(2026, 9, 14, 10),)),
    (same_day, (datetime(2026, 9, 14, 10), datetime(2026, 9, 14, 11))),
    (days_between, (datetime(2026, 9, 14, 10),)),
])
def test_naive_直接抛不许猜(fn, args):
    """🔴 `datetime.astimezone()` 对 naive 值会**按机器本地时区**解释。

    线上机器恰好 +8 所以猜对了 —— 那是巧合不是设计（审计 F3）。
    同一条规矩 `world_model/types.py:46` 和 `attention/events.py:103`
    早就立过，这里是唯一共享的那一处。
    """
    with pytest.raises(ValueError, match="必须带时区"):
        fn(*args)


# ---------------------------------------------------------------- 相对时间

@pytest.mark.parametrize("days, word", [
    (0, "今天"), (1, "昨天"), (2, "前天"), (3, "3天前"), (6, "6天前"),
    (7, "上周"), (13, "上周"), (14, "2周前"), (30, "上个月"), (400, "1年多前"),
    (-1, "以后"),
])
def test_词表(days, word):
    assert humanize(days) == word


def test_relative_和_humanize_是同一张词表():
    """审计 F6：合并之前 timeline 和 speaker 各有一套，措辞不同
    （「3 天前」带空格 vs「3天前」不带）。分叉过一次就会再分叉，所以焊死。
    """
    for d in range(0, 40):
        assert relative(at(14, 12) - timedelta(days=d), at(14, 12)) == humanize(d)


def test_speaker_和_timeline_现在用的是同一个函数():
    """不是"输出碰巧一样"，是**同一个对象** —— 那才叫并成一套。"""
    from attention.speaker import _humanize
    from context.timeline import humanize as timeline_humanize

    assert timeline_humanize is temporal.humanize
    assert _humanize(at(12, 12), at(14, 12)) == temporal.humanize(2) == "前天"


# ---------------------------------------------------------------- 时段 / 时区

@pytest.mark.parametrize("hour, cn", [
    (5, "上午"), (11, "上午"), (12, "下午"), (17, "下午"),
    (18, "晚上"), (22, "晚上"), (23, "深夜"), (1, "深夜"), (4, "深夜"),
])
def test_时段边界按她的作息(hour, cn):
    """她凌晨 1-2 点睡、9-11 点起 —— 所以 23 点对她还是「晚上」的尾巴、
    刚进「深夜」，不是常识里的那套早中晚。"""
    assert slot_of(hour)[1] == cn


def test_LOCAL_TZ_和_CST_是同一个对象():
    """两个名字一个东西。别哪天有人把其中一个改成别的偏移。"""
    assert LOCAL_TZ is CST
    assert CST.utcoffset(None) == timedelta(hours=8)


def test_now_带时区():
    assert now().tzinfo is not None
    assert now().utcoffset() == timedelta(hours=8)
