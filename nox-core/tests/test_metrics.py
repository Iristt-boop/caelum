"""步数 / HRV 两个感知源（2026-08-19）。

在这之前 Attention 只有睡眠一个**感知型**源，World Model 也只有一个写入者。

钉住三件事：

1. **阈值相对她自己的基线** —— 她日均两千多步，拿「每天一万步」那套判，
   他会天天说她不动，那是最该避免的结果
2. **只在状态变化时产事件** —— 「连续几天不动」由强度不衰减表达，
   不靠重复发事件刷屏
3. **先记事实，再判断值不值得关心** —— 不产生事件的那些天，
   World Model 照样得留档，否则趋势永远建不起来
"""

from __future__ import annotations

from datetime import datetime, timezone

from attention.sources.metrics import (
    HRV,
    STEPS,
    DailyMetricSource,
    build_all,
    classify,
)

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self):
        self.state = {}

    def get_source_state(self, key):
        return self.state.get(key)

    def set_source_state(self, key, value):
        self.state[key] = value


class FakeProvider:
    def __init__(self, **fields):
        self.fields = {"has_data": True, "date": "2026-08-19", **fields}

    def get_state(self, turn=None, force_refresh=False):
        return self.fields


class FakeWorld:
    def __init__(self):
        self.written = []

    def observe(self, **kw):
        self.written.append(kw)


def _src(spec, world=None, **fields):
    return DailyMetricSource(spec, FakeProvider(**fields), FakeStore(), world)


# ---------------------------------------------------------------- 分级


def test_步数按她自己的基线分级():
    """基线 2300 —— 她最近 14 天量出来的，不是「每天一万步」。"""
    assert classify(2358, STEPS) == "normal"
    assert classify(1091, STEPS) == "low"        # 47%
    assert classify(391, STEPS) == "very_low"    # 17%


def test_步数走得多不算异常():
    """走得多是好事，不需要他操心。"""
    assert classify(9000, STEPS) == "normal"


def test_hrv按她自己的基线分级():
    assert classify(79, HRV) == "normal"
    assert classify(58, HRV) == "low"            # 74%
    assert classify(40, HRV) == "very_low"       # 51%


def test_拿不到数据是unknown不是normal():
    """「不知道」和「正常」混起来会让恢复检测出错。"""
    assert classify(None, STEPS) == "unknown"
    assert classify(0, STEPS) == "unknown"


# ---------------------------------------------------------------- 只在变化时产事件


def test_状态变化才产事件():
    src = _src(STEPS, steps=391)
    e = src.poll(NOW)
    assert e is not None
    assert e.type == "activity_changed"
    assert e.subtype == "very_low"


def test_同一天不重复产事件():
    """HealthProvider 的 ttl 是 6 小时，一天会 poll 好几次。"""
    src = _src(STEPS, steps=391)
    assert src.poll(NOW) is not None
    assert src.poll(NOW) is None


def test_状态没变不产事件():
    """「连续几天不动」由强度不衰减表达，不靠重复发事件。"""
    store = FakeStore()
    p1 = FakeProvider(steps=391, date="2026-08-18")
    assert DailyMetricSource(STEPS, p1, store).poll(NOW) is not None

    p2 = FakeProvider(steps=420, date="2026-08-19")   # 还是 very_low
    assert DailyMetricSource(STEPS, p2, store).poll(NOW) is None


def test_恢复了要产recovered():
    store = FakeStore()
    DailyMetricSource(STEPS, FakeProvider(steps=391, date="2026-08-18"), store).poll(NOW)
    e = DailyMetricSource(STEPS, FakeProvider(steps=2400, date="2026-08-19"), store).poll(NOW)
    assert e is not None
    assert e.subtype == "recovered"


def test_一直正常不产事件():
    store = FakeStore()
    DailyMetricSource(STEPS, FakeProvider(steps=2400, date="2026-08-18"), store).poll(NOW)
    assert DailyMetricSource(
        STEPS, FakeProvider(steps=2500, date="2026-08-19"), store).poll(NOW) is None


def test_没数据不产事件():
    assert _src(STEPS, steps=None).poll(NOW) is None
    assert _src(HRV, hrv_ms=None).poll(NOW) is None


# ---------------------------------------------------------------- World Model


def test_每天都记事实哪怕不产事件():
    """不产生事件的那些天照样得留档，否则趋势永远建不起来。"""
    store, world = FakeStore(), FakeWorld()
    DailyMetricSource(STEPS, FakeProvider(steps=2400, date="2026-08-18"), store, world).poll(NOW)
    DailyMetricSource(STEPS, FakeProvider(steps=2500, date="2026-08-19"), store, world).poll(NOW)
    assert len(world.written) == 2, "第二天没产事件，但事实必须记下来"
    assert world.written[0]["type"] == "daily_steps"


def test_同一天的事实幂等():
    world = FakeWorld()
    src = _src(STEPS, world=world, steps=2400)
    src.poll(NOW)
    src.poll(NOW)
    assert len({w["dedup_key"] for w in world.written}) == 1


def test_写world失败不影响判断():
    """记账系统出问题不该让他哑掉 —— 关心她是主线，留档是副产品。"""
    class Bad:
        def observe(self, **kw):
            raise RuntimeError("库炸了")

    assert _src(STEPS, world=Bad(), steps=391).poll(NOW) is not None


def test_hrv写的是自己的事实类型():
    world = FakeWorld()
    _src(HRV, world=world, hrv_ms=40).poll(NOW)
    assert world.written[0]["type"] == "hrv"


# ---------------------------------------------------------------- 装配


def test_build_all给出两个源():
    srcs = build_all(FakeProvider(), FakeStore(), None)
    assert [s.spec.name for s in srcs] == ["steps", "hrv"]
    # 两个源的 state key 不能撞，否则互相覆盖对方的「上次是什么等级」
    assert len({s.state_key for s in srcs}) == 2
