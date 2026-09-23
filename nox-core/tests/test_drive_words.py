"""情绪词表只有一张，而且 Resonance 能产出的每种情绪都在里面（2026-09-23）。

糖糖：「以后我们说不定还会在 resonance 加情绪，总不能加一个就要这样维护一个，会乱的。」
加醋意/委屈那天发现后端抄着两份词表，Moments 那份没跟上 —— 写帖子的提示词
里会原样漏出 "jealousy"。所以收成 `attention.resonance.DRIVE_WORDS` 一张。

这条测试让「加情绪只改一个地方」这件事**有人盯着**：新 Drive 进了 snapshot
却没进词表，这里红。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from attention.dejection import DejectionState
from attention.jealousy import JealousyState
from attention.longing import LongingState
from attention.playfulness import PlayfulnessState
from attention.registry import AttentionRegistry
from tests.test_resonance_v3 import _ev
from attention.restlessness import RestlessnessState
from attention.resonance import DRIVE_WORDS, ResonanceState
from attention.sulk import SulkState

NOW = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)


def _everything_on() -> dict:
    """把每一种 Drive 都点亮，看 snapshot 吐出哪些名字。"""
    reg = AttentionRegistry()
    for kind in ("concern", "regret", "curiosity"):
        reg.upsert(f"{kind}-x", 0.6, kind=kind, decay="slow", event=_ev(kind),
                   summary="x", now=NOW)
    longing = LongingState(value=0.6)
    dej = DejectionState()
    dej.on_failed("改文件", NOW)
    dej.on_gave_up(NOW)
    play = PlayfulnessState()
    for _ in range(3):
        play.on_turn(NOW, valence="playful", cue="哈哈")
    jel = JealousyState()
    jel.on_message(NOW, "有个男生要我微信")
    sulk = SulkState(residual=0.5, residual_at=NOW, last_silent_h=3)
    rs = ResonanceState(reg, longing, dej, play, RestlessnessState(),
                        jealousy=jel, sulk=sulk)
    return rs.snapshot(NOW, want=1.0, busy_app="game", busy_seconds=3600)


def test_每种情绪都有名字():
    names = set(_everything_on())
    assert {"concern", "longing", "jealousy", "sulk", "playfulness"} <= names, (
        f"点亮测试本身没点亮（{names}）—— 下面那条会空过")
    missing = names - set(DRIVE_WORDS)
    assert not missing, f"这些情绪没有中文名，会原样漏进他的上下文和 Moments：{missing}"


def test_Moments和上下文用的是同一张():
    import moments
    from context.providers import resonance as provider

    assert provider._WORDS is DRIVE_WORDS
    for k, v in DRIVE_WORDS.items():
        assert moments.DRIVE_WORDS[k] == v
