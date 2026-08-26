"""Resonance V3：Drive 聚合。

见 `CAELUM-RESONANCE-ARCHITECTURE.md` 第六节、原则 1、第十节。

## 这里守的是三条边界

  1. **只读** —— 绝不写 Registry
  2. **不自己开口** —— V3 不参与任何决策
  3. **能回答为什么** —— 每个 Drive 带 because / evidence

第 1 条最要紧。一旦允许回写，Drive 和 Concern 会互相喂对方，
强度从哪来的就永远说不清了。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.events import ExperienceEvent  # noqa: E402
from attention.registry import AttentionRegistry  # noqa: E402
from attention.resonance import Drive, ResonanceState, _combine  # noqa: E402

T0 = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


def _ev(subject: str) -> ExperienceEvent:
    return ExperienceEvent(source="test", type="t", payload={"s": subject})


def _reg(*rows) -> AttentionRegistry:
    """rows: (subject, strength, summary)"""
    reg = AttentionRegistry()
    for subject, strength, summary in rows:
        reg.upsert(subject, strength, kind="concern", decay="slow",
                   event=_ev(subject), summary=summary, now=T0)
    return reg


# ---------------------------------------------------------------- 叠加语义


def test_several_worries_weigh_more_than_the_biggest():
    """🔴 这是这一层存在的全部理由。

    Registry 的 `upsert` 取较大值 —— 三件事压着和一件事压着，
    在它眼里一样重。Drive 要能表达这个差别。
    """
    one = ResonanceState(_reg(("睡眠", 0.62, "a"))).get("concern", T0)
    three = ResonanceState(_reg(
        ("睡眠", 0.45, "a"), ("心情", 0.62, "b"), ("活动量", 0.40, "c"),
    )).get("concern", T0)

    assert three.intensity > one.intensity
    assert three.intensity == pytest.approx(0.875, abs=0.005)


def test_never_reaches_one():
    """叠加**不能溢出**。求和的话三件小事就破 1 了。"""
    assert _combine([0.9, 0.9, 0.9, 0.9]) < 1.0
    assert _combine([0.45, 0.62, 0.40]) < 1.0


def test_one_heavy_worry_is_not_diluted():
    """一件很重的事不该被一堆小事稀释 —— 那是求平均会犯的错。"""
    heavy_alone = _combine([0.9])
    heavy_with_small = _combine([0.9, 0.1, 0.1])
    assert heavy_with_small >= heavy_alone


def test_empty_registry_has_no_drive():
    assert ResonanceState(AttentionRegistry()).snapshot(T0) == {}


# ---------------------------------------------------------------- 能回答为什么


def test_drive_knows_why_it_exists():
    """原则 1：光有一个数字没有意义。"""
    d = ResonanceState(_reg(
        ("糖糖的睡眠", 0.62, "昨晚只睡了 4 小时"),
        ("糖糖说的心情", 0.45, "她说：压力好大"),
    )).get("concern", T0)

    assert d.because[0] == "糖糖的睡眠"       # 强的排前面
    assert "糖糖说的心情" in d.because
    assert any("4 小时" in e for e in d.evidence)
    assert d.source_count == 2


def test_describe_is_human_readable():
    d = ResonanceState(_reg(("糖糖的睡眠", 0.62, "昨晚只睡了 4 小时"))).get("concern", T0)
    assert "糖糖的睡眠" in d.describe()


def test_describe_handles_empty():
    """没有惦记的事时也要说得出话，不能崩。"""
    empty = Drive(name="concern", intensity=0.0, load=0.0, because=[], evidence=[],
                  source_count=0, computed_at=T0)
    assert empty.describe()


# ---------------------------------------------------------------- 🔴 只读


def test_snapshot_does_not_touch_registry():
    """绝不写 Registry。一旦回写，强度从哪来的就说不清了。"""
    reg = _reg(("糖糖的睡眠", 0.62, "a"), ("糖糖说的心情", 0.45, "b"))
    before = {a.subject: (a.strength, a.last_updated, len(a.evidence))
              for a in reg.list(now=T0)}

    ResonanceState(reg).snapshot(T0)
    ResonanceState(reg).snapshot(T0 + timedelta(hours=3))

    after = {a.subject: (a.strength, a.last_updated, len(a.evidence))
             for a in reg.list(now=T0)}
    assert after == before


def test_resonance_has_no_write_methods():
    """结构性保证：这个类**不该**长出写方法来。

    比起「这次没写」，更该守的是「以后也不许写」。
    """
    public = {n for n in dir(ResonanceState) if not n.startswith("_")}
    assert public == {"snapshot", "get"}, f"多出来的方法：{public}"


# ---------------------------------------------------------------- 跟着衰减走


def test_drive_follows_decay_without_storing_anything():
    """Drive 没有自己的记忆 —— Registry 淡了它就跟着淡。

    存下来的话会出现「他为一件已经淡掉的事继续难受」。
    """
    reg = _reg(("糖糖的睡眠", 0.62, "a"))
    state = ResonanceState(reg)

    now_ = state.get("concern", T0).intensity
    later = state.get("concern", T0 + timedelta(days=7)).intensity  # slow 半衰期
    assert later == pytest.approx(now_ / 2, abs=0.01)


def test_dying_concerns_do_not_prop_up_the_drive():
    """低于地板值的陈年关心不参与叠加。

    不排除的话，一堆将死的 0.05 会把 Drive 慢慢垫高，
    表现成「他总是隐隐担心着，但说不出在担心什么」。
    """
    reg = _reg(*[(f"陈年{i}", 0.06, "x") for i in range(20)])
    # 放很久，全部掉到地板以下
    d = ResonanceState(reg).snapshot(T0 + timedelta(days=60))
    assert d == {}


def test_load_keeps_resolution_where_intensity_saturates():
    """🔴 真实数据里 `intensity` 会饱和，`load` 不会。

    2026-08-24 串真实链路时发现的：睡眠 very_short 单条就是
    0.75 × 关系加成 1.3 = 0.975，任何单调聚合都必然 ≥0.975 ——
    一件事和三件事的 `intensity` 分别是 0.98 和 0.99，看不出差别。

    我的单元测试当时用 0.45/0.62/0.40，**恰好避开了这个区间**。
    """
    one = ResonanceState(_reg(("睡眠", 0.97, "a"))).get("concern", T0)
    three = ResonanceState(_reg(
        ("睡眠", 0.97, "a"), ("心情", 0.71, "b"), ("活动量", 0.40, "c"),
    )).get("concern", T0)

    # intensity 几乎分不出来
    assert abs(three.intensity - one.intensity) < 0.03
    # load 差得很清楚
    assert three.load > one.load * 2
