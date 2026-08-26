"""睡眠链路接上 World Model 之后的两件事。

🔴 **这个文件存在的理由，是第一个测试。**

原来 `SleepSource.poll()` 把「值得关心」和「值得记录」混成了一个判断：
状态没变就 `return None`，整条数据一起扔了。于是「她这周每天都睡 6 小时」
这个事实**一条都没留下** —— 因为它「没有变化」。

平淡本身就是趋势的一部分。分岔必须在那些提前 return **之前**。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.evaluator import AttentionEvaluator  # noqa: E402
from attention.events import ExperienceEvent  # noqa: E402
from attention.registry import AttentionRegistry  # noqa: E402
from attention.relationship import RelationshipState  # noqa: E402
from attention.sources.sleep import BASELINE_MIN, SleepSource  # noqa: E402
from attention.store import AttentionStore  # noqa: E402
from world_model import WorldModel  # noqa: E402

CST = timezone(timedelta(hours=8))


class FakeProvider:
    def __init__(self, hours: float, date: str) -> None:
        self.hours, self.date = hours, date

    def get_state(self, turn=None, force_refresh=False):
        return {"available": True, "has_data": True,
                "sleep_min": self.hours * 60, "sleep_date": self.date,
                "deep_sleep_min": 30}


@pytest.fixture()
def rig(tmp_path):
    store = AttentionStore(tmp_path / "attn.db")
    world = WorldModel(tmp_path / "world.db")
    yield store, world
    store.close()
    world.store.close()


def test_boring_days_are_still_recorded(rig):
    """🔴 状态没变的日子 → **不产生事件，但必须留档**。

    这是整个第一阶段的核心。漏了它，趋势永远建不起来。
    """
    store, world = rig
    # ⚠️ 三天都要落在**同一个等级**里，否则会因为跨过 very_short 阈值
    # 而产生事件，测不到「状态没变」那条路径。
    # 基线 432 分钟，short 是 0.70~0.85 → 5.1~6.1 小时。取 5.5/5.6/5.7。
    days = [("2026-08-12", 5.5), ("2026-08-13", 5.6), ("2026-08-14", 5.7)]
    events = []
    for d, h in days:
        src = SleepSource(FakeProvider(h, d), store, world=world)
        events.append(src.poll(now=datetime.fromisoformat(f"{d}T09:00:00+08:00")))

    # 第一天状态从 None → short，产生事件；后面两天状态没变，不产生
    assert events[0] is not None
    assert events[1] is None and events[2] is None, "状态没变不该刷屏"

    # 但三天的事实**一条都不能少**
    rows = world.query("sleep_duration", limit=10)
    assert len(rows) == 3, "没有变化的日子也是事实"
    assert [r.raw["value"] for r in rows] == [5.7, 5.6, 5.5]


def test_world_model_failure_does_not_break_attention(rig):
    """记账系统挂了不该让他哑掉 —— 关心她是主线，留档是副产品。"""
    store, _ = rig

    class Boom:
        def observe(self, **kw):
            raise RuntimeError("world.db 锁住了")

    src = SleepSource(FakeProvider(4.5, "2026-08-16"), store, world=Boom())
    ev = src.poll(now=datetime(2026, 8, 16, 9, 0, tzinfo=CST))
    assert ev is not None, "World Model 出错，Attention 照常工作"


def _evt(hours: float, date: str) -> ExperienceEvent:
    return ExperienceEvent(
        source="health", type="sleep_quality_changed", subtype="short",
        payload={"sleep_min": hours * 60, "baseline_min": BASELINE_MIN,
                 "sleep_date": date},
        timestamp=datetime.fromisoformat(f"{date}T09:00:00+08:00"),
    )


def test_streak_makes_it_stronger_and_says_so(rig):
    """连着几天没睡够 → 强度更高，而且**说得出来**。

    这是糖糖能感知到的那个变化：
    「昨晚睡得少」→「已经连着 4 天没睡够了」
    """
    _, world = rig
    for d, h in [(12, 5.0), (13, 5.1), (14, 5.2), (15, 4.9)]:
        world.observe(source="health", type="sleep_duration",
                      observed={"value": h, "unit": "hour"},
                      observed_at=datetime(2026, 8, d, 9, 0, tzinfo=CST),
                      dedup_key=f"sleep/2026-08-{d}")

    rel = RelationshipState()
    reg = AttentionRegistry()
    now = datetime(2026, 8, 15, 10, 0, tzinfo=CST)

    lone = AttentionEvaluator(rel).evaluate(_evt(4.9, "2026-08-15"), reg, now)
    trend = AttentionEvaluator(rel, world=world).evaluate(
        _evt(4.9, "2026-08-15"), reg, now)

    assert trend.strength > lone.strength, "连着几天该更担心"
    assert "连着 4 天" in trend.summary
    assert "World Model" in trend.reason, "要答得出「我凭什么这么judge」"


def test_one_good_night_breaks_the_streak(rig):
    """中间睡好一天，连续就断了 —— 「上周有三天没睡好」和
    「连着三天没睡好」是两件事。"""
    _, world = rig
    for d, h in [(12, 5.0), (13, 5.1), (14, 8.0), (15, 4.9)]:
        world.observe(source="health", type="sleep_duration",
                      observed={"value": h, "unit": "hour"},
                      observed_at=datetime(2026, 8, d, 9, 0, tzinfo=CST),
                      dedup_key=f"sleep/2026-08-{d}")

    d = AttentionEvaluator(RelationshipState(), world=world).evaluate(
        _evt(4.9, "2026-08-15"), AttentionRegistry(),
        datetime(2026, 8, 15, 10, 0, tzinfo=CST))
    assert "连着" not in d.summary, "只数连续的，一天补回来就断"


def test_no_world_model_falls_back_to_single_night(rig):
    """没接 World Model 时，行为和接入之前完全一样。"""
    d = AttentionEvaluator(RelationshipState(), world=None).evaluate(
        _evt(4.9, "2026-08-15"), AttentionRegistry(),
        datetime(2026, 8, 15, 10, 0, tzinfo=CST))
    assert d.action == "upsert"
    assert "连着" not in d.summary
