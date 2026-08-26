"""TimeProvider 测试。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.base import Turn  # noqa: E402
from context.providers.time import TimeProvider  # noqa: E402
from personality.mood import CST  # noqa: E402


def _at(month: int, day: int, hour: int, minute: int = 30) -> Turn:
    return Turn(now=datetime(2026, month, day, hour, minute, tzinfo=CST))


@pytest.mark.parametrize("hour,slot", [
    (0, "late_night"), (3, "late_night"), (4, "late_night"),
    (5, "morning"), (9, "morning"), (11, "morning"),
    (12, "afternoon"), (15, "afternoon"), (17, "afternoon"),
    (18, "evening"), (22, "evening"),
    (23, "late_night"),
])
def test_time_of_day_boundaries(hour, slot):
    """边界值逐个钉住 —— 时段算错不会报错，只会让他在不对的时候说不对的话。"""
    s = TimeProvider().get_state(_at(8, 2, hour))
    assert s["time_of_day"] == slot, f"{hour} 点应该是 {slot}"


def test_weekday_and_weekend():
    # 2026-08-02 是周日，08-03 是周一
    sun = TimeProvider().get_state(_at(8, 2, 15))
    assert sun["weekday"] == "周日" and sun["is_weekend"] is True

    mon = TimeProvider().get_state(_at(8, 3, 15))
    assert mon["weekday"] == "周一" and mon["is_weekend"] is False


def test_no_fake_holiday():
    """没有节假日数据源就不许报节假日 —— 宁可不提供，也不编一个。"""
    assert "is_holiday" not in TimeProvider().get_state(_at(10, 1, 12))


def test_render_is_short():
    """这段每轮都发且在缓存断点之后，每个字都按未命中价付费。"""
    p = TimeProvider()
    text = p.render(p.get_state(_at(8, 2, 16)))
    assert text == "【此刻】2026-08-02 周日 16 点（下午，周末）"
    assert len(text) < 60


def test_render_has_no_minutes():
    """给模型看的那句不带分钟 —— 决定的是缓存的**有效窗口**。

    DeepSeek 的提示词缓存是自动前缀匹配：dynamic_system 变一个字，
    后面的 history 和当轮消息全部错位、一起不命中。
    写成 16:30 的话缓存只能活一分钟，隔一分钟说话就是一次冷启动
    （冷启动 62.5%，命中时 98.9%，2026-08-02 实测）。

    精确时间留在 state 里给程序用；她要问几点几分，他有 get_current_time 工具。
    """
    p = TimeProvider()
    a = p.render(p.get_state(_at(8, 2, 16, 30)))
    p.invalidate()
    b = p.render(p.get_state(_at(8, 2, 16, 59)))
    assert a == b, "同一小时内渲染结果必须一模一样，否则每轮都在砸缓存"

    p.invalidate()
    c = p.render(p.get_state(_at(8, 2, 17, 0)))
    assert c != a, "跨小时应该变"

    # 精确值仍然可用，只是不进给模型看的那句
    p.invalidate()          # 上一行刚把 17:00 存进缓存，TTL 还没过
    assert p.get_state(_at(8, 2, 16, 30))["clock"] == "16:30"


def test_caches_within_a_minute():
    """同一轮里多次读到的应该是同一个时刻，不能一个看到 16:59 另一个 17:00。"""
    p = TimeProvider()
    p.get_state()
    first = p.get_state()["clock"]
    for _ in range(5):
        assert p.get_state()["clock"] == first


def test_not_volatile():
    """时间不依赖当轮说了什么，可以缓存（跟 mood 相反）。"""
    assert TimeProvider().volatile is False
    assert TimeProvider().ttl == timedelta(minutes=1)


def test_section_is_time():
    assert TimeProvider().section == "time"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
