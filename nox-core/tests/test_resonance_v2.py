"""Resonance V2：对话产生 Concern。

见 `CAELUM-RESONANCE-ARCHITECTURE.md` 第七节 V2。

## 这一层的风险不在"漏"，在"错"

漏掉一句话，她还会再说。**读错一句话**，他会念叨一件她根本没说过的事，
而她无从知道他为什么这么想。

所以这个文件里权重最大的是否定和优先级那几条 ——
关键词匹配翻车的地方 99% 在那儿（「我不难受」含「难受」）。
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.appraisal import SUBJECT, RuleAppraiser  # noqa: E402
from attention.evaluator import AttentionEvaluator  # noqa: E402
from attention.events import ExperienceEvent  # noqa: E402
from attention.intent import GENERATE_THRESHOLD  # noqa: E402
from attention.registry import AttentionRegistry  # noqa: E402
from attention.relationship import RelationshipState  # noqa: E402


def _say(text: str) -> ExperienceEvent:
    return ExperienceEvent(
        source="chat", type="message",
        payload={"text": text, "session_id": "s1"},
    )


def _decide(text: str, rel: RelationshipState | None = None):
    return AttentionEvaluator(rel or RelationshipState()).evaluate(
        _say(text), AttentionRegistry()
    )


# ---------------------------------------------------------------- Appraisal 本身


@pytest.mark.parametrize("text", [
    "我今天压力好大",
    "有点撑不住了",
    "好难受啊",
])
def test_distress_is_recognised(text):
    a = RuleAppraiser().appraise(text)
    assert a is not None and a.valence == "distress"


@pytest.mark.parametrize("text", [
    "今天天气不错",
    "晚饭吃的火锅",
    "那部电影挺好看的",
    "",
])
def test_ordinary_talk_produces_nothing(text):
    """🔴 **大多数话都该返回 None。** 这是常态，不是失败。"""
    assert RuleAppraiser().appraise(text) is None


@pytest.mark.parametrize("text", [
    "我不难受",
    "一点也没难受",
    "别累着自己",
])
def test_negation_is_not_distress(text):
    """🔴 关键词匹配最容易翻的车。

    「我不难受」含「难受」两个字。少了否定判断，
    **她说自己没事反而会被记成一条 concern**。
    """
    a = RuleAppraiser().appraise(text)
    assert a is None or a.valence != "distress", f"{text!r} 被读成了难受"


def test_relief_wins_over_distress():
    """「不难受了」既含 relief 词组也含「难受」。

    顺序反了就会把「她好了」读成「她不好」—— 正好读反。
    """
    a = RuleAppraiser().appraise("今天不难受了")
    assert a is not None and a.valence == "relief"


def test_strongest_cue_wins():
    """一句话里有轻有重时按重的算。"""
    a = RuleAppraiser().appraise("有点好累，而且压力好大")
    assert a is not None
    assert a.intensity == pytest.approx(0.62)


# ---------------------------------------------------------------- 接进 Evaluator


def test_distress_becomes_concern():
    d = _decide("我今天压力好大")
    assert d.action == "upsert"
    assert d.subject == SUBJECT
    assert d.kind == "concern"
    #: summary 用原话 —— Intent 的 reason 直接取最新 evidence，
    #: 他开口时说的就是基于这句
    assert "压力好大" in d.summary


def test_relief_weakens():
    """她说缓过来了 → weaken，不是等半衰期慢慢忘。

    对齐睡眠 recovered 那条的教训（`evaluator.py` 里记着 2026-08-08 实测）。
    """
    d = _decide("今天好多了")
    assert d.action == "weaken"
    assert d.factor < 1.0


def test_ordinary_talk_is_ignored():
    d = _decide("晚饭吃的火锅")
    assert d.action == "ignore"
    assert not d.should_apply


def test_avoided_topic_wins_over_everything():
    """她说过「别老问我心情」，那就一句都不记。"""
    rel = RelationshipState(avoid_topics=["心情"])
    d = _decide("我今天压力好大", rel)
    assert d.action == "ignore"
    assert "avoid_topics" in d.reason


# ---------------------------------------------------------------- 强度分档


def test_heavy_distress_crosses_speak_threshold():
    """明确而重的话要够得着开口阈值，否则这条链等于没接。"""
    d = _decide("有点撑不住了")
    assert d.strength >= GENERATE_THRESHOLD


def test_light_complaint_stays_below_threshold():
    """🔴 随口一句抱怨**不该**换来一次主动关心。

    「好累」压在 0.42，低于 0.55 —— 只进 Registry 攒着，不触发开口。
    """
    d = _decide("今天好累")
    assert d.action == "upsert"
    assert d.strength < GENERATE_THRESHOLD


def test_decay_is_normal_not_fast():
    """decay 档位直接决定这条链有没有用。

    fast(6h)：0.62 掉到阈值只要 1 小时 —— 她晚上说完就睡，
    第二天他已经忘了，**等于白记**。normal 给 8.3 小时。
    """
    d = _decide("有点撑不住了")
    assert d.decay == "normal"


def test_strength_survives_overnight():
    """晚上说的，第二天早上还够得着 —— 用真实衰减算，不是看常量。"""
    reg = AttentionRegistry()
    d = _decide("有点撑不住了")
    reg.upsert(d.subject, d.strength, kind=d.kind, decay=d.decay,
               event=_say("有点撑不住了"), summary=d.summary)

    a = reg.get(d.subject)
    later = a.last_updated + timedelta(hours=8)
    assert a.current_strength(later) >= GENERATE_THRESHOLD

    # 但不该惦记到后天
    assert a.current_strength(a.last_updated + timedelta(hours=48)) < GENERATE_THRESHOLD


# ---------------------------------------------------------------- 不碰别人的地盘


def test_subject_does_not_collide_with_health():
    """🔴 不能和健康那几条撞 subject。

    撞了的后果：两条来源写进同一条 Concern —— strength 互相覆盖
    （upsert 取较大值）、evidence 混成一串，
    事后分不清他到底在为哪件事担心。
    """
    assert SUBJECT not in {"糖糖的睡眠", "糖糖的活动量", "糖糖的状态"}


def test_health_events_still_work():
    """加了对话规则之后，健康那条路一点没变。"""
    ev = ExperienceEvent(
        source="health", type="sleep_quality_changed", subtype="very_short",
        payload={"sleep_min": 240, "baseline_min": 430},
    )
    d = AttentionEvaluator(RelationshipState()).evaluate(ev, AttentionRegistry())
    assert d.action == "upsert"
    assert d.subject == "糖糖的睡眠"


# ---------------------------------------------------------------- 单独的开关


def test_can_be_turned_off_alone(monkeypatch):
    """🔴 出了问题要能只关这一条，不牵连睡眠那几条。

    线上 `NOX_ATTENTION_LIVE=1`，他是真会开口的 ——
    万一太吵，把整个 `NOX_ATTENTION` 关掉的代价太大。
    """
    monkeypatch.setenv("NOX_CHAT_CONCERN", "0")
    d = _decide("有点撑不住了")
    assert d.action == "ignore"
    assert "NOX_CHAT_CONCERN" in d.reason

    # 健康那条一点不受影响
    ev = ExperienceEvent(
        source="health", type="sleep_quality_changed", subtype="very_short",
        payload={"sleep_min": 240, "baseline_min": 430},
    )
    assert AttentionEvaluator(RelationshipState()).evaluate(
        ev, AttentionRegistry()).action == "upsert"


def test_on_by_default(monkeypatch):
    monkeypatch.delenv("NOX_CHAT_CONCERN", raising=False)
    assert _decide("有点撑不住了").action == "upsert"
