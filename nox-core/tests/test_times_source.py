"""固定时间醒来 Source（TimeWakeSource）的单元测试。纯逻辑，不打网络。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.sources.times import TimeWake, TimeWakeSource  # noqa: E402

CN = timezone(timedelta(hours=8), "CST")


def _cn(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=CN)


class FakeStore:
    """内存版 source_state，模拟 AttentionStore 的接口。"""

    def __init__(self):
        self.state: dict[str, dict] = {}

    def get_source_state(self, key):
        return self.state.get(key)

    def set_source_state(self, key, value):
        self.state[key] = value


# ---------------------------------------------------------------- 配置解析


def test_parse_default():
    wakes = TimeWake.parse("12:00:午饭,18:30:晚饭,22:30:睡前")
    assert len(wakes) == 3
    assert wakes[0].subject == "午饭"
    assert wakes[0].hour == 12
    assert wakes[1].subject == "晚饭"
    assert wakes[1].minute == 30


def test_parse_skips_bad():
    wakes = TimeWake.parse("12:00:午饭,25:00:坏时间,abcd,18:30:晚饭")
    assert [w.subject for w in wakes] == ["午饭", "晚饭"]


def test_parse_empty():
    assert TimeWake.parse("") == []
    assert TimeWake.parse(",,,") == []


# ---------------------------------------------------------------- 时间窗口触发


def test_hit_exact_time():
    store = FakeStore()
    src = TimeWakeSource("12:00:午饭", store)
    ev = src.poll(_cn(2026, 8, 8, 12, 0))
    assert ev is not None
    assert ev.source == "times"
    assert ev.type == "time_wake"
    assert ev.subtype == "午饭"


def test_hit_within_window_after():
    """poll 迟到 8 分钟（在 +10 窗口内）也要命中。"""
    store = FakeStore()
    src = TimeWakeSource("12:00:午饭", store)
    ev = src.poll(_cn(2026, 8, 8, 12, 8))
    assert ev is not None


def test_miss_outside_window():
    store = FakeStore()
    src = TimeWakeSource("12:00:午饭", store)
    # 12:20 已经超出 +10 窗口
    assert src.poll(_cn(2026, 8, 8, 12, 20)) is None


def test_same_day_only_once():
    """同一天同一个时间点只触发一次。"""
    store = FakeStore()
    src = TimeWakeSource("12:00:午饭", store)
    assert src.poll(_cn(2026, 8, 8, 12, 0)) is not None
    # 同一天再来（比如 poll 又跑了一轮）—— 不重复
    assert src.poll(_cn(2026, 8, 8, 12, 5)) is None


def test_next_day_fires_again():
    store = FakeStore()
    src = TimeWakeSource("12:00:午饭", store)
    assert src.poll(_cn(2026, 8, 8, 12, 0)) is not None
    assert src.poll(_cn(2026, 8, 8, 12, 5)) is None
    # 第二天
    assert src.poll(_cn(2026, 8, 9, 12, 0)) is not None


def test_multiple_wakes_independent():
    """午饭触发不影响晚饭（subject 是话题冷却的 key）。"""
    store = FakeStore()
    src = TimeWakeSource("12:00:午饭,18:30:晚饭", store)
    assert src.poll(_cn(2026, 8, 8, 12, 0)) is not None
    assert src.poll(_cn(2026, 8, 8, 18, 30)) is not None


def test_disabled_when_empty_config():
    store = FakeStore()
    src = TimeWakeSource("", store)
    assert src.enabled is False
    assert src.poll(_cn(2026, 8, 8, 12, 0)) is None
