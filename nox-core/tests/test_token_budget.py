"""token 估算的单元测试。纯函数，不打网络。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message, ToolCall, ToolResult  # noqa: E402
from context.token_budget import (  # noqa: E402
    estimate_text_tokens,
    estimate_tokens,
    tail_within_budget,
)


def test_estimate_cjk_dense():
    """纯中文消息：0.6 token/字 + 结构开销。"""
    m = Message(role="user", text="晚安")
    n = estimate_tokens([m])
    # 2 字 × 0.6 = 1.2 → int 1，+ 4 结构 = 5
    assert n == 5


def test_estimate_mixed():
    """中英混合：CJK 按 0.6、其余按 0.3。"""
    text = "今天天气不错 sunny day 42"
    cjk = 4  # 今天天气不错
    other = len(text) - cjk  # 剩下的英文/数字/空格
    expected = int(cjk * 0.6) + int(other * 0.3) + 4
    assert estimate_tokens([Message(role="user", text=text)]) == expected


def test_estimate_empty_message():
    assert estimate_tokens([]) == 0
    assert estimate_tokens([Message(role="user", text="")]) == 4  # 只有结构开销


def test_estimate_includes_tool_calls():
    """工具调用参数也算进预算（它们不压缩，但预算要算）。"""
    m = Message(
        role="assistant",
        tool_calls=[ToolCall(id="c1", name="ha_get_state", arguments={"entity": "灯"})],
    )
    n = estimate_tokens([m])
    assert n > 4  # 结构开销之上还有内容


def test_estimate_includes_tool_results():
    m = Message(role="tool_results", tool_results=[ToolResult(call_id="c1", content="灯是开的")])
    n = estimate_tokens([m])
    assert n > 4


def test_estimate_text():
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("晚安") == 1  # 2×0.6=1.2 → int 1
    assert estimate_text_tokens("abcd") == 1  # 4×0.3=1.2 → int 1


def test_tail_within_budget_keeps_latest():
    """从尾部取最近窗口，老消息被丢掉（预算内）。"""
    msgs = [
        Message(role="user", text=f"第{i}句很长的内容" * 5) for i in range(20)
    ]
    got = tail_within_budget(msgs, budget=60)
    # 20 条 × (约 10 字 × 0.6 + 4) ≈ 20 × 10 = 200 token，60 预算只能装几条
    assert 1 <= len(got) < 20
    assert got[-1].text == msgs[-1].text  # 最新一条必在


def test_tail_keeps_everything_when_under_budget():
    msgs = [Message(role="user", text="短") for _ in range(5)]
    got = tail_within_budget(msgs, budget=1000)
    assert len(got) == 5


def test_tail_single_oversized_message_kept():
    """单条超预算的极端消息也必须保留（那是最近的话）。"""
    huge = Message(role="user", text="长" * 5000)
    got = tail_within_budget([huge], budget=100)
    assert len(got) == 1
    assert got[0] is huge
