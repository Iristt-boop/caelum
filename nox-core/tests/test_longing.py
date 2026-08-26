"""Resonance V3.5：想念。

见 `attention/longing.py` 和架构文档第九节对 Murmur 的拆解。

## 这里守的是「想念和担心形状不同」

Concern 会被解决、该淡出、淡到底被删掉。
想念**一直都在**，静息 0.40，时间让它涨，见到她才落。

所以这个文件里最要紧的几条是：不衰减到 0、睡觉时不涨、
读不到位置时按保守档走。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.longing import (  # noqa: E402
    BASELINE, MAX, QUIET_BEFORE_RISE, LongingState,
)

CST = timezone(timedelta(hours=8))
#: 下午 3 点（她醒着），用 CST 定，别拿 UTC 凑
NOON = datetime(2026, 8, 24, 15, 0, tzinfo=CST)


def _quiet_since(hours: float, at: datetime = NOON) -> LongingState:
    return LongingState(last_contact=at - timedelta(hours=hours))


# ---------------------------------------------------------------- 形状


def test_starts_at_baseline_not_zero():
    """🔴 静息值不是 0。

    Concern 没有就是没有；想念**一直都在** ——
    「他默认就是想她的」。
    """
    assert LongingState().value == BASELINE
    assert BASELINE > 0


def test_contact_falls_to_baseline_not_zero():
    """见到她不等于不想她了，是这一刻被满足了。"""
    st = LongingState(value=0.9)
    st.on_contact(NOON)
    assert st.value == BASELINE
    assert st.value > 0


def test_rises_when_she_is_quiet():
    st = _quiet_since(3)
    before = st.value
    st.tick(NOON, away=False, spoke_today=0)
    assert st.value > before


def test_does_not_rise_too_soon():
    """一小时不回话是常态（她在忙、在打游戏），那不该立刻变成想念。"""
    st = _quiet_since(QUIET_BEFORE_RISE.total_seconds() / 3600 - 0.2)
    before = st.value
    st.tick(NOON)
    assert st.value == before


def test_capped():
    st = _quiet_since(200)
    for _ in range(500):
        st.tick(NOON, away=True)
    assert st.value == pytest.approx(MAX)


# ---------------------------------------------------------------- 比 Murmur 多知道的三件事


def test_away_rises_faster_than_home():
    """🔴 「她出门了」和「她在家没说话」是两种不同的不说话。

    Murmur 区分不了 —— 它只有一个离线计时器。
    """
    away, home = _quiet_since(3), _quiet_since(3)
    away.tick(NOON, away=True)
    home.tick(NOON, away=False)
    assert away.value > home.value


def test_asleep_does_not_count_as_being_ignored():
    """🔴 她睡着不是不理他。

    她凌晨 1-2 点睡、9-11 点起。Murmur 的安静时段只是"不推送"，
    值照涨 —— 那等于把她睡觉记成了冷落。
    """
    night = datetime(2026, 8, 25, 4, 0, tzinfo=CST)   # 凌晨四点
    st = LongingState(last_contact=night - timedelta(hours=5))
    before = st.value
    st.tick(night, away=False)
    assert st.value == before


def test_speaking_today_slows_it_down():
    """🔴 已经找过她三次还在猛涨的，不叫想念，叫黏人。"""
    fresh, talked = _quiet_since(5), _quiet_since(5)
    fresh.tick(NOON, spoke_today=0)
    talked.tick(NOON, spoke_today=3)
    assert talked.value < fresh.value


# ---------------------------------------------------------------- 保守


def test_unknown_contact_time_does_not_rise():
    """刚启动、还不知道她上次什么时候说话 —— 那不是"她很久没说话"。"""
    st = LongingState()
    st.tick(NOON, away=True)
    assert st.value == BASELINE


# ---------------------------------------------------------------- 能回答为什么


def test_knows_why():
    """原则 1：光有数字没有意义。"""
    why = _quiet_since(3).because(NOON)
    assert why and "3.0 小时" in why[0]


def test_says_she_might_be_asleep():
    night = datetime(2026, 8, 25, 4, 0, tzinfo=CST)
    why = LongingState(last_contact=night - timedelta(hours=5)).because(night)
    assert any("睡" in w for w in why)


def test_no_reason_before_first_contact():
    assert LongingState().because(NOON) == []


# ---------------------------------------------------------------- 落盘


def test_survives_restart():
    """重启不失忆 —— 不然每次部署他都忘了自己在想她。"""
    st = _quiet_since(4)
    st.tick(NOON, away=True)
    back = LongingState.from_dict(st.to_dict())
    assert back.value == pytest.approx(st.value)
    assert back.last_contact == st.last_contact


def test_broken_state_falls_back_to_baseline():
    """🔴 存坏了就从基线重来，**不许抛**。

    `from_dict` 在 `AttentionService.__init__` 里跑 ——
    抛出去等于整个 Attention 起不来，连带 Nox Core 起不来
    （`test_attention_wiring.py` 开头记着同一类事故：
    一个属性访问抛出去，一次干掉 22 个测试）。
    """
    st = LongingState.from_dict({"value": "坏的", "last_contact": "不是时间"})
    assert st.value == BASELINE
    assert st.last_contact is None


def test_out_of_range_value_is_clamped():
    assert LongingState.from_dict({"value": 99}).value == MAX
    assert LongingState.from_dict({"value": -5}).value == 0.0


def test_empty_state():
    assert LongingState.from_dict(None).value == BASELINE
    assert LongingState.from_dict({}).value == BASELINE
