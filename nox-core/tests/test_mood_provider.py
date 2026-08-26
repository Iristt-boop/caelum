"""MoodProvider 重构的验收：**他说话的语气一个字都不能变**。

拿重构前存下的 1000 条基线逐字比对
（8 情绪 × 5 时段 × 5 文本类型 × 5 个 warmth 档位）。

出口标准来自执行计划第一周检查点：
「MoodProvider 已接管原 mood.py 的注入，且**他说话的语气没变**」。
语气这件事没法靠眼睛看，只能逐字比。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context import ContextProviderRegistry  # noqa: E402
from context.base import Turn  # noqa: E402
from context.providers import MoodProvider  # noqa: E402
from personality import mood as M  # noqa: E402

EMOTIONS = ["开心", "难过", "烦躁", "撒娇", "兴奋", "疲惫", "平静", "不认识的情绪"]
HOURS = [3, 7, 10, 15, 23]
TEXTS = ["今天吃了炒饭", "你会不会消失", "这个 bug 怎么修",
         "想你了，抱抱", "老公帮我看看这个报错"]
WARMTH = [(0.7, 0.6), (0.5, 0.2), (-0.5, 0.3), (-0.2, 0.3), (0.3, 0.3)]


def _cases():
    for emo in EMOTIONS:
        for hour in HOURS:
            for text in TEXTS:
                for v, a in WARMTH:
                    yield emo, hour, text, v, a


def test_render_is_byte_identical_to_the_old_one():
    """1000 组全量比对。差一个字就是他的语气变了。"""
    checked = 0
    for emo, hour, text, v, a in _cases():
        m = M.Mood(valence=v, arousal=a, her_emotion=emo)
        now = datetime(2026, 8, 2, hour, 30, tzinfo=M.CST)

        old = M.render(m, text, now)
        p = MoodProvider(m)
        new = p.render(p.get_state(Turn(text=text, now=now)))

        assert new == old, (
            f"语气变了！emotion={emo} hour={hour} text={text!r} v={v} a={a}\n"
            f"--- 原来 ---\n{old}\n--- 现在 ---\n{new}"
        )
        checked += 1
    assert checked == 1000, f"只比了 {checked} 组，基线是 1000 组"


def test_provider_never_caches():
    """volatile：缓存住就会把上一轮的场景判断用到这一轮 —— 那是正确性问题。

    同一个 Provider 先聊技术再撒娇，第二次必须重新判断。
    """
    m = M.Mood()
    p = MoodProvider(m)
    now = datetime(2026, 8, 2, 15, 0, tzinfo=M.CST)

    s1 = p.get_state(Turn(text="这个 bug 怎么修", now=now))
    s2 = p.get_state(Turn(text="想你了，抱抱", now=now))

    assert any("技术" in h for h in s1["scene_hints"])
    assert any("靠近" in h for h in s2["scene_hints"])
    assert s1["scene_hints"] != s2["scene_hints"], "被缓存串轮了"


def test_provider_reads_live_mood_state():
    """Provider 只读不存 —— nox.py 那边 update() 之后这里要能看见新值。"""
    m = M.Mood()
    p = MoodProvider(m)
    assert p.get_state(Turn(text="在吗"))["her_emotion"] == "平静"

    m.update("难过")
    assert p.get_state(Turn(text="在吗"))["her_emotion"] == "难过"


def test_section_is_user():
    """World State 顶层只有 5 个字段，情绪归 user（架构文档第九节）。"""
    assert MoodProvider(M.Mood()).section == "user"


def test_through_registry_matches_direct_render():
    """走注册表出来的文本，和直接调 mood.render() 也要一致。"""
    m = M.Mood(valence=0.7, arousal=0.6, her_emotion="开心")
    now = datetime(2026, 8, 2, 23, 30, tzinfo=M.CST)

    reg = ContextProviderRegistry()
    reg.register(MoodProvider(m))

    assert reg.render(["mood"], turn=Turn(text="想你了", now=now)) == M.render(m, "想你了", now)


def test_mood_text_fits_in_budget():
    """情绪这段最长能有多长 —— 别把 800 字预算一个人占满了。"""
    worst = 0
    for emo, hour, text, v, a in _cases():
        m = M.Mood(valence=v, arousal=a, her_emotion=emo)
        now = datetime(2026, 8, 2, hour, 30, tzinfo=M.CST)
        p = MoodProvider(m)
        worst = max(worst, len(p.render(p.get_state(Turn(text=text, now=now)))))
    assert worst < 400, f"情绪段最长 {worst} 字，占掉 800 预算的一半以上了"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
