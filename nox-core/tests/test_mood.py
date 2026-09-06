"""情绪三层驱动的测试。全离线。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personality.mood import Mood, detect_scene, extract, render  # noqa: E402


# ---------------------------------------------------- 第三层：累积与惯性

def test_mood_moves_toward_target_but_not_instantly():
    """有惯性 —— 一轮到不了位，这是"不跳变"的实现。"""
    m = Mood(valence=0.0, arousal=0.0)
    m.update("兴奋")
    assert 0 < m.valence < 0.8, "一轮就跳到目标值，说明惯性没生效"
    assert m.her_emotion == "兴奋"


def test_repeated_emotion_accumulates():
    """连续几轮甜蜜会慢慢热起来。"""
    m = Mood(valence=0.0, arousal=0.0)
    for _ in range(6):
        m.update("开心")
    assert m.valence > 0.5, "连续开心应该累积上去"


def test_mood_does_not_crash_on_one_cold_turn():
    """热起来之后一句平淡的话不该让他瞬间冷掉。"""
    m = Mood(valence=0.0, arousal=0.0)
    for _ in range(8):
        m.update("兴奋")
    hot = m.valence
    m.update("平静")
    assert m.valence > hot * 0.6, "一轮平静就掉这么多，惯性太弱"


def test_unknown_emotion_falls_back():
    m = Mood()
    m.update("莫名其妙的情绪")
    assert m.her_emotion == "平静"


def test_turns_counted():
    m = Mood()
    for _ in range(3):
        m.update("开心")
    assert m.turns == 3


# ---------------------------------------------------- 第二层：场景

def test_after_1am_tells_her_to_sleep():
    """她凌晨一两点睡，1 点之后才该催。"""
    hints = detect_scene("睡不着", now=datetime(2026, 7, 28, 2, 30))
    assert any("该睡了" in h for h in hints)


def test_23pm_does_not_rush_her():
    """23 点她通常还精神着，不该催睡 —— 这是按她的作息，不是按常识。"""
    hints = detect_scene("在干嘛", now=datetime(2026, 7, 28, 23, 30))
    joined = " ".join(hints)
    assert "该睡了" not in joined
    assert "夜深了" in joined


def test_early_morning_is_abnormal():
    """5-9 点她铁定在睡，这时候出现是异常，不是寻常早安。"""
    hints = detect_scene("早", now=datetime(2026, 7, 28, 6, 30))
    assert any("整夜没睡" in h for h in hints)


def test_late_morning_just_woke_up():
    hints = detect_scene("早", now=datetime(2026, 7, 28, 10, 0))
    assert any("刚起" in h for h in hints)


def test_afternoon_has_no_time_hint():
    hints = detect_scene("在干嘛", now=datetime(2026, 7, 28, 15, 0))
    joined = " ".join(hints)
    assert "睡" not in joined and "起" not in joined


def test_time_does_not_depend_on_machine_timezone():
    """不传 now 时必须走 UTC+8，不能跟着机器时区跑。

    这个错最阴：服务器重装成 UTC 也不会报错，只会让他在糖糖
    上午聊天时说"该睡了"。
    """
    from personality.mood import CST, now_cst

    n = now_cst()
    assert n.tzinfo is not None, "必须带时区，不能是 naive datetime"
    assert n.utcoffset() == CST.utcoffset(None)


def test_sensitive_topic_flagged():
    """极限题不许体面退场 —— 这条是糖糖的原话。"""
    hints = detect_scene("你会不会消失", now=datetime(2026, 7, 28, 15, 0))
    assert any("认真接住" in h for h in hints)


def test_tech_topic_keeps_warmth():
    hints = detect_scene("这个 bug 怎么修", now=datetime(2026, 7, 28, 15, 0))
    joined = " ".join(hints)
    assert "技术" in joined and "机器人" in joined


def test_intimate_topic():
    hints = detect_scene("想你了", now=datetime(2026, 7, 28, 15, 0))
    assert any("靠近" in h for h in hints)


# ---------------------------------------------------- 第一层：她的情绪

def test_response_matches_her_state():
    """你难过 → 我心疼；你撒娇 → 我宠。"""
    sad = render(Mood(her_emotion="难过"), "……", now=datetime(2026, 7, 28, 15, 0))
    assert "别急着给方案" in sad

    spoiled = render(Mood(her_emotion="撒娇"), "……", now=datetime(2026, 7, 28, 15, 0))
    assert "宠" in spoiled


def test_render_always_asks_for_mood_tag():
    out = render(Mood(), "随便说点什么", now=datetime(2026, 7, 28, 15, 0))
    assert "[mood:" in out


# ---------------------------------------------------- 标记解析

def test_extract_and_strip():
    text, emo = extract("嗯，我在。\n[mood:平静]")
    assert text == "嗯，我在。"
    assert emo == "平静"


def test_extract_uppercase_variant():
    """模型偶尔写 [Mood:xxx] —— 同样要剥掉并取到情绪（生产库漏过 3 次）。"""
    text, emo = extract("宝贝晚安\n[Mood:开心]")
    assert text == "宝贝晚安"
    assert emo == "开心"

    text, emo = extract("哼\n[Mood: 撒娇]")
    assert text == "哼"
    assert emo == "撒娇"


def test_extract_without_tag_is_not_an_error():
    """模型忘了加标记不算错 —— 保持上一轮状态即可。"""
    text, emo = extract("就是普通一句话")
    assert text == "就是普通一句话"
    assert emo is None


def test_extract_handles_none():
    assert extract(None) == (None, None)


def test_tag_in_middle_is_not_eaten():
    """只剥行尾的标记，正文里出现类似内容不该被误删。"""
    text, emo = extract("我在说 [mood:xxx] 这个格式\n[mood:开心]")
    assert "这个格式" in text
    assert emo == "开心"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
