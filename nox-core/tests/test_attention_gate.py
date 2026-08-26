"""统一开口闸（DailyGate）的单元测试。纯逻辑，不打网络。"""

from __future__ import annotations

import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.gate import DailyGate, GateDecision  # noqa: E402

CN = timezone(timedelta(hours=8), "CST")


def _cn(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=CN)


def _utc(y, mo, d, h, mi=0):
    """给 UTC 时刻，内部转 CST 判断。"""
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# ---------------------------------------------------------------- 安静时段


def test_quiet_hours_blocked():
    gate = DailyGate()
    # 凌晨 3 点（CST）—— 她睡觉，必须拦
    d = gate.can_speak(_cn(2026, 8, 8, 3, 0))
    assert d.can_speak is False
    assert "安静时段" in d.reason


def test_daytime_allowed():
    gate = DailyGate()
    d = gate.can_speak(_cn(2026, 8, 8, 14, 0))
    assert d.can_speak is True


def test_quiet_hours_boundary():
    gate = DailyGate()
    # 1:00 整算安静（>= QUIET_START）
    assert gate.can_speak(_cn(2026, 8, 8, 1, 0)).can_speak is False
    # 8:59 安静（< QUIET_END）
    assert gate.can_speak(_cn(2026, 8, 8, 8, 59)).can_speak is False
    # 9:00 放行
    assert gate.can_speak(_cn(2026, 8, 8, 9, 0)).can_speak is True


def test_quiet_hours_uses_cst_not_utc():
    """服务器在东京，判断必须按她时区（CST）。"""
    gate = DailyGate()
    # UTC 19:00 = CST 次日 03:00 —— 她睡觉，必须拦
    d = gate.can_speak(_utc(2026, 8, 7, 19, 0))
    assert d.can_speak is False


# ---------------------------------------------------------------- 每日额度


def test_quota_blocks_after_limit():
    gate = DailyGate(daily_quota=3)
    now = _cn(2026, 8, 8, 12, 0)
    for _ in range(3):
        assert gate.can_speak(now).can_speak is True
        gate.note_spoke(now)
    d = gate.can_speak(now)
    assert d.can_speak is False
    assert "额度" in d.reason
    assert d.spoken_today == 3


def test_quota_resets_next_day():
    gate = DailyGate(daily_quota=2)
    gate.note_spoke(_cn(2026, 8, 8, 12, 0))
    gate.note_spoke(_cn(2026, 8, 8, 13, 0))
    assert gate.can_speak(_cn(2026, 8, 8, 14, 0)).can_speak is False
    # 第二天重置
    assert gate.can_speak(_cn(2026, 8, 9, 12, 0)).can_speak is True


def test_quota_notes_are_daily_keyed():
    gate = DailyGate(daily_quota=1)
    gate.note_spoke(_cn(2026, 8, 8, 10, 0))
    # 同一天再记一次
    gate.note_spoke(_cn(2026, 8, 8, 11, 0))
    d = gate.can_speak(_cn(2026, 8, 8, 12, 0))
    assert d.spoken_today == 2


# ---------------------------------------------------------------- 状态持久化


def test_dump_load_roundtrip():
    gate = DailyGate(daily_quota=5)
    gate.note_spoke(_cn(2026, 8, 8, 12, 0))
    gate.note_spoke(_cn(2026, 8, 8, 13, 0))

    state = gate.dump_state()
    g2 = DailyGate()
    g2.load_state(state)
    assert g2.dump_state() == state
    assert g2.can_speak(_cn(2026, 8, 8, 14, 0)).spoken_today == 2


def test_load_empty_state():
    gate = DailyGate()
    gate.load_state(None)
    assert gate.can_speak(_cn(2026, 8, 8, 12, 0)).can_speak is True


def test_reset():
    gate = DailyGate()
    gate.note_spoke(_cn(2026, 8, 8, 12, 0))
    gate.reset()
    assert gate.can_speak(_cn(2026, 8, 8, 12, 0)).spoken_today == 0


# ---------------------------------------------------------------- 决策对象


def test_gate_decision_render():
    d = GateDecision(True, "今天还有开口额度", 1, 3)
    assert "可以" in d.render()
    d2 = GateDecision(False, "今日额度已用完", 3, 3)
    assert "不行" in d2.render()
