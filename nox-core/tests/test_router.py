"""Router 的分类规则测试 —— 不打网络。

重点验证「宁可放行也不误判」：把该走完整路径的判成闲聊，
代价是他答不上事；反过来最多多花几个 token。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router.intent import Intent, classify  # noqa: E402


@pytest.mark.parametrize(
    "text",
    ["在吗", "在吗？", "晚安", "早安", "嗯", "好的", "ok", "抱抱", "哈哈", "hi", "谢谢"],
)
def test_greetings_go_light(text):
    assert classify(text).intent is Intent.SMALL_TALK


@pytest.mark.parametrize(
    "text",
    [
        "在吗？昨天那个表情改好了吗",   # 问候开头但带正事
        "开灯",                          # 很短但要控设备
        "关空调",
        "还记得上次那个吗",              # 要查记忆
        "帮我看看",
        "为什么会这样",
        "今天吃了两个包子",              # 普通陈述，可能要算热量
        "",                              # 空输入
    ],
)
def test_everything_else_goes_full(text):
    assert classify(text).intent is Intent.FULL


def test_long_text_always_full():
    assert classify("嗯" * 20).intent is Intent.FULL


def test_action_word_beats_greeting():
    """'在吗' 命中问候表，但只要带上动作词就必须放行。"""
    assert classify("在吗开灯").intent is Intent.FULL


def test_trailing_punctuation_stripped():
    for s in ["晚安。", "晚安！", "晚安~", "好的，"]:
        assert classify(s).intent is Intent.SMALL_TALK, s


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
