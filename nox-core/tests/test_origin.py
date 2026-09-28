"""`data/origin.her_words` —— 哪些是她说的话（糖糖 2026-09-28）。

接线测试在 `test_temporal_extract.py`（真跑 /chat）；这里钉判断本身。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attention.appraisal_llm import NOT_HER_WORDS, is_injected  # noqa: E402
from data.origin import her_words  # noqa: E402


@pytest.mark.parametrize("text", [
    "【共影·主动】她在看一部本地的片子，刚到第 106 秒，这里换场了，你想说一句。",
    "【共影】她暂停在 57.5 秒问你：【当前画面】这是一个户外",
    "（系统提示：不是她在跟你说话。你就是忽然想起她了。）",
    "(系统提示：半角括号也算)",
    "  （系统提示：前面带空白也算）",
])
def test_主会话里程序拼的不是她的话(text):
    assert her_words("1809ea16d4f7", text) == ""


@pytest.mark.parametrize("sid", ["diary-42", "reading-7"])
def test_程序会话里整段都不是她的话(sid):
    assert her_words(sid, "明天去练腿") == ""


@pytest.mark.parametrize("text", [
    "明天去练腿",
    "我今天在抖音刷到了一个做生物工程的博主",
    #: 她自己打的方括号不算 —— 只认我们提示词的标记
    "【转发】明天去练腿",
    "系统崩了一下午",
])
def test_她的原话原样放行(text):
    assert her_words("1809ea16d4f7", text) == text


def test_空消息是空串不是None():
    """下游用 `if not text` 判「这轮没字」—— 纯图片轮就是这样。"""
    assert her_words("s-1", None) == ""
    assert her_words("s-1", "") == ""


def test_老调用方的会话闸门还在():
    """`is_injected` / `NOT_HER_WORDS` 有别的调用方，委托过去之后行为不变。"""
    assert is_injected("diary-1") and is_injected("reading-1")
    assert not is_injected("1809ea16d4f7")
    assert "diary-" in NOT_HER_WORDS
