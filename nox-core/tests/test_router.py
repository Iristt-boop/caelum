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


# ------------------------------------------------- RouteResult 的透明代理

def test_RouteResult_把_LoopResult_的每个字段都代理了():
    """包一层只是为了多带「走了哪条路」，其余必须**逐字段**透传。

    🔴 这条是补票的 —— 已经漏过两次，而且两次都是**静默的**：

      1. 第一次端到端冒烟崩在 `outcome` 上（类自己的 docstring 记着）
      2. 2026-09-12 发现漏了 `attachments`：`nox.py` 的 `chat()` 在
         「模型把 `[tag]` 写进正文、没调 send_meme」那条兜底里调
         `result.attachments.extend(...)` —— 一调就 `AttributeError`。
         而那条兜底正是 2026-09-06 她报的降级场景。
         流式那条路没事（它拿到的是 LoopResult 本身），所以一直没暴露。

    所以按**字段名**对齐来测，而不是逐个 `assert`：以后 `LoopResult` 加字段，
    这条会自动红，不需要谁记得回来补一行。
    """
    import dataclasses

    from agent.loop import LoopResult
    from router.router import RouteResult

    missing = [f.name for f in dataclasses.fields(LoopResult)
               if not hasattr(RouteResult, f.name)]
    assert not missing, f"RouteResult 漏代理了这些字段: {missing}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
