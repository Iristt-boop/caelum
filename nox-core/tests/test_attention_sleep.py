"""M2 测试：睡眠数据 → Concern。

守的是两件容易做砸的事：

1. **阈值比的是她自己的基线**，不是通用人群的
   （`context/providers/health.py:25` 那条规矩）
2. **同一件事不重复触发** —— 她深睡本来就偏少、也常连着几天睡不够，
   按绝对值判会天天报警，那正是 M3 要防的「烦人」
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.engine import AttentionEngine  # noqa: E402
from attention.evaluator import AttentionEvaluator  # noqa: E402
from attention.intent import GENERATE_THRESHOLD  # noqa: E402
from attention.events import ExperienceEvent  # noqa: E402
from attention.registry import AttentionRegistry  # noqa: E402
from attention.relationship import RelationshipState  # noqa: E402
from attention.sources.sleep import BASELINE_MIN, SleepSource, classify  # noqa: E402
from attention.store import AttentionStore  # noqa: E402

T0 = datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc)
SUBJECT = "糖糖的睡眠"


class FakeProvider:
    """假的 HealthProvider —— 只要 get_state 返回同样形状的 dict 就行。"""

    def __init__(self, **kw: Any) -> None:
        self.state: dict[str, Any] = {"has_data": True, **kw}

    def get_state(self, turn: Any = None, force_refresh: bool = False) -> dict[str, Any]:
        return self.state


@pytest.fixture
def store(tmp_path):
    s = AttentionStore(tmp_path / "attn.db")
    yield s
    s.close()


def _sleep(hours: float, date: str = "2026-08-08", **kw) -> FakeProvider:
    return FakeProvider(sleep_date=date, sleep_min=hours * 60, **kw)


# ---------------------------------------------------------------- 分级


def test_classify_against_her_own_baseline():
    """6.2 小时按她的基线（7.2h）算还行，按通用的 8 小时算就是不足。

    用错基线的后果不是「判得严一点」，是**天天触发**。
    """
    six_two = 6.2 * 60
    assert classify(six_two, baseline=BASELINE_MIN) == "normal"
    assert classify(six_two, baseline=480) == "short"      # 8 小时基线


def test_classify_levels():
    assert classify(7.5 * 60) == "normal"
    assert classify(5.8 * 60) == "short"        # 0.81
    assert classify(4.5 * 60) == "very_short"   # 0.63


def test_classify_unknown_is_not_normal():
    """「不知道」和「正常」混起来，恢复检测就会出错。"""
    assert classify(None) == "unknown"
    assert classify(0) == "unknown"


# ---------------------------------------------------------------- Temporal Filter


def test_first_short_night_fires(store):
    src = SleepSource(_sleep(5.5), store)
    ev = src.poll(now=T0)
    assert ev is not None
    assert ev.subtype == "short"
    assert ev.payload["baseline_min"] == BASELINE_MIN


def test_same_sleep_date_is_skipped(store):
    """HealthProvider 的 ttl 是 6 小时，一天会 poll 好几次，
    但那是同一觉，不该反复产生事件。"""
    src = SleepSource(_sleep(5.5, date="2026-08-08"), store)
    assert src.poll(now=T0) is not None
    assert src.poll(now=T0 + timedelta(hours=2)) is None


def test_repeated_short_does_not_refire(store):
    """连着两晚都睡不够，第二晚不是「新消息」。

    「连续三天睡不好」靠 Attention 强度不衰减来表达，不靠重复发事件。
    """
    provider = _sleep(5.5, date="2026-08-08")
    src = SleepSource(provider, store)
    assert src.poll(now=T0) is not None

    provider.state.update(sleep_date="2026-08-09", sleep_min=5.4 * 60)
    assert src.poll(now=T0 + timedelta(days=1)) is None


def test_worsening_fires_again(store):
    """从 short 恶化到 very_short 是新消息。"""
    provider = _sleep(5.5, date="2026-08-08")
    src = SleepSource(provider, store)
    src.poll(now=T0)

    provider.state.update(sleep_date="2026-08-09", sleep_min=4.2 * 60)
    ev = src.poll(now=T0 + timedelta(days=1))
    assert ev is not None and ev.subtype == "very_short"


def test_recovery_fires_recovered(store):
    provider = _sleep(5.0, date="2026-08-08")
    src = SleepSource(provider, store)
    src.poll(now=T0)

    provider.state.update(sleep_date="2026-08-09", sleep_min=7.5 * 60)
    ev = src.poll(now=T0 + timedelta(days=1))
    assert ev is not None and ev.subtype == "recovered"


def test_steady_normal_never_fires(store):
    """一直睡得好就该一直安静。"""
    provider = _sleep(7.5, date="2026-08-08")
    src = SleepSource(provider, store)
    assert src.poll(now=T0) is None

    provider.state.update(sleep_date="2026-08-09")
    assert src.poll(now=T0 + timedelta(days=1)) is None


def test_low_deep_sleep_alone_does_not_fire(store):
    """CLAUDE.md 写着她「深睡偏少」—— 那是常态。
    拿它当触发条件，她每天都会被关心一次。"""
    src = SleepSource(_sleep(7.5, deep_sleep_min=25), store)
    assert src.poll(now=T0) is None


def test_no_data_is_not_an_event(store):
    src = SleepSource(FakeProvider(has_data=False), store)
    assert src.poll(now=T0) is None


def test_provider_crash_does_not_propagate(store):
    """Provider 自己崩了不该让 Attention 跟着挂。"""

    class Boom:
        def get_state(self, turn=None, force_refresh=False):
            raise RuntimeError("health-mcp 挂了")

    assert SleepSource(Boom(), store).poll(now=T0) is None


def test_filter_state_survives_restart(tmp_path):
    """Temporal Filter 的记忆也要落盘 —— 否则重启后同一觉会重放一次。"""
    path = tmp_path / "attn.db"
    s1 = AttentionStore(path)
    SleepSource(_sleep(5.5), s1).poll(now=T0)
    s1.close()

    s2 = AttentionStore(path)
    assert SleepSource(_sleep(5.5), s2).poll(now=T0) is None
    s2.close()


# ---------------------------------------------------------------- Evaluator


def _evt(subtype: str = "short", hours: float = 5.5) -> ExperienceEvent:
    return ExperienceEvent(
        source="health", type="sleep_quality_changed", subtype=subtype,
        payload={"sleep_date": "2026-08-08", "sleep_min": hours * 60,
                 "baseline_min": BASELINE_MIN},
        timestamp=T0,
    )


def test_care_topic_boosts_strength():
    """同一件事，睡眠在 care_topics 里就该更上心。"""
    reg = AttentionRegistry()
    cared = AttentionEvaluator(RelationshipState()).evaluate(_evt(), reg, T0)

    neutral_rel = RelationshipState(care_topics={})
    neutral = AttentionEvaluator(neutral_rel).evaluate(_evt(), reg, T0)

    assert cared.strength > neutral.strength


def test_worse_severity_means_stronger_concern():
    reg = AttentionRegistry()
    ev = AttentionEvaluator(RelationshipState())
    assert ev.evaluate(_evt("very_short"), reg, T0).strength > \
           ev.evaluate(_evt("short"), reg, T0).strength


def test_avoid_topic_wins_over_everything():
    """她说过「别老问我睡觉的事」之后，多低的睡眠都不该再关心。
    这是 M5 最重要的一条路径，先把它守住。"""
    rel = RelationshipState(avoid_topics={"睡眠"})
    d = AttentionEvaluator(rel).evaluate(_evt("very_short", 3.0), AttentionRegistry(), T0)
    assert not d.should_apply
    assert "avoid" in d.reason


def test_recovered_weakens_the_concern():
    """睡好了一晚，关心要明显松一口气。

    这里原本是 ignore（想着让它按半衰期自己淡）。dry-run 模拟三天时
    发现那样不行：slow 半衰期 7 天，睡好一晚只掉 9%，强度还有 0.73，
    照样过 Intent 阈值 —— Nox 会在她睡好的那天继续念叨前天的事。
    """
    d = AttentionEvaluator(RelationshipState()).evaluate(
        _evt("recovered"), AttentionRegistry(), T0)
    assert d.should_apply
    assert d.action == "weaken"
    assert d.factor < 0.5


def test_recovery_drops_below_intent_threshold(store):
    """睡好之后，强度要掉到「不值得开口」以下。"""
    engine = AttentionEngine.bootstrap(store, RelationshipState())
    engine.handle(_evt("very_short"), now=T0)
    assert engine.registry.get(SUBJECT).current_strength(T0) > 0.9

    engine.handle(_evt("recovered"), now=T0 + timedelta(days=1))
    after = engine.registry.get(SUBJECT).current_strength(T0 + timedelta(days=1))
    assert after < GENERATE_THRESHOLD


def test_unknown_source_is_ignored():
    d = AttentionEvaluator(RelationshipState()).evaluate(
        ExperienceEvent(source="music", type="track_changed", timestamp=T0),
        AttentionRegistry(), T0)
    assert not d.should_apply


def test_decision_reason_is_human_readable():
    """将来要能回答「他凭什么关心这个」。"""
    d = AttentionEvaluator(RelationshipState()).evaluate(_evt(), AttentionRegistry(), T0)
    assert "5.5 小时" in d.reason and "7.2 小时" in d.reason


# ---------------------------------------------------------------- 端到端（M2 验收）


def test_end_to_end_sleep_becomes_concern(store):
    """M2 的验收标准：Registry 里真的长出 Concern("糖糖的睡眠")。"""
    engine = AttentionEngine.bootstrap(store, RelationshipState())
    ev = SleepSource(_sleep(5.2), store).poll(now=T0)
    assert ev is not None

    engine.handle(ev, now=T0)

    a = engine.registry.get(SUBJECT)
    assert a is not None
    assert a.kind == "concern"
    assert a.strength == pytest.approx(0.71, abs=0.02)   # 0.55 × (1 + 0.3×1.0)
    assert a.evidence[0].summary.startswith("2026-08-08")


def test_concern_survives_restart(tmp_path):
    """M2 的另一条验收：重启之后那份关心还在。"""
    path = tmp_path / "attn.db"

    s1 = AttentionStore(path)
    e1 = AttentionEngine.bootstrap(s1, RelationshipState())
    e1.handle(SleepSource(_sleep(4.5), s1).poll(now=T0), now=T0)
    before = e1.registry.get(SUBJECT).strength
    s1.close()

    s2 = AttentionStore(path)                    # 模拟 nox-core 重启
    e2 = AttentionEngine.bootstrap(s2, RelationshipState())
    a = e2.registry.get(SUBJECT)
    assert a is not None
    assert a.strength == pytest.approx(before)
    assert a.since == T0
    s2.close()


def test_consecutive_bad_nights_keep_concern_alive(store):
    """连着睡不好的那几天，关心不该淡掉。

    第二晚不产生事件（见 test_repeated_short_does_not_refire），
    但强度会按 slow 衰减 —— 一天只掉一点，仍然远高于阈值。
    """
    engine = AttentionEngine.bootstrap(store, RelationshipState())
    engine.handle(_evt("short"), now=T0)

    three_days_later = T0 + timedelta(days=3)
    assert engine.registry.get(SUBJECT).current_strength(three_days_later) > 0.5


def test_ignored_event_does_not_touch_registry(store):
    engine = AttentionEngine.bootstrap(store, RelationshipState())
    engine.handle(
        ExperienceEvent(source="music", type="track_changed", timestamp=T0), now=T0)
    assert len(engine.registry) == 0
