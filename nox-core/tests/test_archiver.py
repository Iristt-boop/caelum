"""归档器测试 —— 假 OB、假摘要模型，不打网络。

重点验证「什么该归档、什么不该」，那才是这一层的价值所在。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message, Turn  # noqa: E402
from memory.archiver import Archiver  # noqa: E402
from memory.ob_client import MemoryResult  # noqa: E402


class FakeOB:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.grown: list[str] = []

    def grow(self, content: str) -> MemoryResult:
        self.grown.append(content)
        return MemoryResult(self.ok, text="已归档" if self.ok else "", error=None if self.ok else "写入失败")


class FakeSummarizer:
    def __init__(self, summary: str = "2026-07-28 糖糖和 Nox 聊了归档功能。") -> None:
        self.summary = summary
        self.seen_prompts: list[str] = []

    def complete(self, messages, tools, *, system=None, dynamic_system=None,
                 depth=None, max_tokens=None):
        self.seen_prompts.append(messages[-1].text or "")
        return Turn(stop_reason="end_turn", text=self.summary)


def convo(n: int) -> list[Message]:
    out: list[Message] = []
    for i in range(n):
        out.append(Message(role="user", text=f"第{i}个问题，说点有内容的东西"))
        out.append(Message(role="assistant", text=f"第{i}个回答"))
    return out


def test_short_chat_is_not_archived():
    """打个招呼就走的不算一段对话 —— 宁可漏掉，不要污染 OB。"""
    ob, s = FakeOB(), FakeSummarizer()
    r = Archiver(ob, s).archive(convo(2))
    assert r.archived is False
    assert "不够一段对话" in r.reason
    assert ob.grown == [], "不该调用 grow"


def test_long_chat_is_archived():
    ob, s = FakeOB(), FakeSummarizer()
    r = Archiver(ob, s).archive(convo(5))
    assert r.archived is True
    assert len(ob.grown) == 1
    assert "2026-07-28" in ob.grown[0]


def test_force_overrides_length_check():
    ob, s = FakeOB(), FakeSummarizer()
    r = Archiver(ob, s).archive(convo(1), force=True)
    assert r.archived is True


def test_model_can_veto():
    """让模型自己判断「不值得记」，比写一堆规则去猜可靠。"""
    ob, s = FakeOB(), FakeSummarizer(summary="不值得记")
    r = Archiver(ob, s).archive(convo(5))
    assert r.archived is False
    assert "值得记" in r.reason
    assert ob.grown == [], "被否决就不该写进 OB"


def test_ob_failure_does_not_raise():
    """归档发生在对话之后 —— 这时候抛异常只会吓到糖糖。"""
    ob, s = FakeOB(ok=False), FakeSummarizer()
    r = Archiver(ob, s).archive(convo(5))
    assert r.archived is False
    assert "OB 写入失败" in r.reason
    assert r.summary, "摘要该保留着，方便排查"


def test_summarizer_failure_is_handled():
    class Broken:
        def complete(self, *a, **kw):
            return Turn(stop_reason="error", error="模型挂了")

    ob = FakeOB()
    r = Archiver(ob, Broken()).archive(convo(5))
    assert r.archived is False
    assert ob.grown == []


def test_transcript_includes_both_sides():
    ob, s = FakeOB(), FakeSummarizer()
    Archiver(ob, s).archive(convo(4))
    prompt = s.seen_prompts[0]
    assert "糖糖：" in prompt and "Nox：" in prompt


def test_today_is_passed_to_model():
    """摘要要带日期 —— 记忆桶多了容易分不清先后，这是糖糖定的规矩。"""
    ob, s = FakeOB(), FakeSummarizer()
    Archiver(ob, s).archive(convo(4))
    assert "今天是 2026-" in s.seen_prompts[0]


def test_tool_messages_are_skipped():
    from agent.llm import ToolCall, ToolResult

    ob, s = FakeOB(), FakeSummarizer()
    history = convo(4) + [
        Message(role="assistant", tool_calls=[ToolCall(id="c", name="ha_switch", arguments={})]),
        Message(role="tool_results", tool_results=[ToolResult(call_id="c", content="ok")]),
    ]
    Archiver(ob, s).archive(history)
    assert "ha_switch" not in s.seen_prompts[0]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
