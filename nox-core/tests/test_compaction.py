"""上下文压缩的单元测试。FakeLLM 代替真实模型，不打网络。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message, Turn, Usage  # noqa: E402
from context.compactor import (  # noqa: E402
    MAX_SUMMARY_CHARS,
    SUMMARY_HEADER,
    build_summarize_messages,
    maybe_compact,
    plan_compaction,
    render_segment,
    summarize,
)
from data.store import Store  # noqa: E402


# ---------------------------------------------------------------- 工具函数


def _history(n: int, text="长消息内容" * 3) -> list[Message]:
    """造 n 条 user/assistant 交替的消息。"""
    out = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append(Message(role=role, text=f"{text} #{i}"))
    return out


class FakeAdapter:
    """返回固定摘要的假模型。"""

    def __init__(self, text="Topic: 测试\nDecisions: 无\nCurrent state: 无\nOpen threads: 无\nFacts: 无"):
        self.text = text
        self.calls = 0

    def complete(self, messages, tools, **kwargs):
        self.calls += 1
        return Turn(stop_reason="end_turn", text=self.text, usage=Usage())


# ---------------------------------------------------------------- plan_compaction


def test_plan_no_compact_when_under_budget():
    h = _history(5, "短")
    plan = plan_compaction(h, recent_window_tokens=1000, budget_tokens=20000)
    assert plan.should_compact is False
    assert plan.recent == h


def test_plan_compacts_when_over_budget():
    # 20 条 × 每条约 (15字×0.6 + 4) ≈ 10+4 = 14 token ≈ 280 token
    # recent 窗口 100 token 只能装几条，剩下的都该压缩
    h = _history(20, "这是一段比较长的中文消息内容" * 2)
    plan = plan_compaction(h, recent_window_tokens=50, budget_tokens=100)
    assert plan.should_compact is True
    assert len(plan.compressible) > 0
    assert len(plan.recent) > 0
    assert plan.compressible + plan.recent == h  # 完整覆盖，不丢不重


def test_plan_recent_is_latest():
    h = _history(10, "这是一段比较长的中文消息内容" * 2)
    plan = plan_compaction(h, recent_window_tokens=60, budget_tokens=50)
    assert plan.should_compact is True
    # recent 必须包含最后一条
    assert plan.recent[-1] is h[-1]


def test_plan_empty_history():
    plan = plan_compaction([], recent_window_tokens=100, budget_tokens=100)
    assert plan.should_compact is False


def test_plan_no_compressible_when_window_covers_all():
    """窗口预算大到覆盖全部历史 → 没有可压缩的，不压。"""
    h = _history(5, "短")
    plan = plan_compaction(h, recent_window_tokens=10000, budget_tokens=100)
    # 预算 100 < 实际，但窗口 10000 全装下了 → compressible 空
    assert plan.should_compact is False
    assert plan.reason  # 有说明


# ---------------------------------------------------------------- render / build


def test_render_segment_roles():
    seg = [
        Message(role="user", text="今天吃什么"),
        Message(role="assistant", text="吃火锅吧"),
    ]
    out = render_segment(seg)
    assert "糖糖：今天吃什么" in out
    assert "Nox：吃火锅吧" in out


def test_build_summarize_messages_no_old():
    msgs = build_summarize_messages(None, [Message(role="user", text="hi")])
    assert msgs[0].role == "system"
    assert "Topic" in msgs[0].text
    assert "hi" in msgs[1].text


def test_build_summarize_messages_with_old():
    msgs = build_summarize_messages("旧摘要内容", [Message(role="user", text="hi")])
    combined = msgs[1].text
    assert "旧摘要内容" in combined
    assert "hi" in combined


# ---------------------------------------------------------------- summarize


def test_summarize_returns_text():
    fa = FakeAdapter()
    out = summarize(fa, None, [Message(role="user", text="hi")])
    assert out == fa.text
    assert fa.calls == 1


def test_summarize_failure_returns_none():
    class BoomAdapter:
        def complete(self, *a, **kw):
            raise RuntimeError("模型挂了")

    assert summarize(BoomAdapter(), None, [Message(role="user", text="hi")]) is None


def test_summarize_truncates_overlong():
    class LongAdapter:
        def complete(self, *a, **kw):
            return Turn(stop_reason="end_turn", text="长" * (MAX_SUMMARY_CHARS + 1000), usage=Usage())

    out = summarize(LongAdapter(), None, [Message(role="user", text="hi")])
    assert len(out) <= MAX_SUMMARY_CHARS


# ---------------------------------------------------------------- maybe_compact（集成）


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def test_maybe_compact_writes_summary(store):
    # 造一个超预算的长会话
    h = _history(30, "这是一段比较长的中文消息内容用来凑预算" * 3)
    store.append("s1", h)

    fa = FakeAdapter()
    ok = maybe_compact(store, fa, "s1", recent_window_tokens=80, budget_tokens=200)
    assert ok is True
    assert store.get_summary("s1") == fa.text


def test_maybe_compact_noop_under_budget(store):
    store.append("s1", [Message(role="user", text="短"), Message(role="assistant", text="也短")])
    fa = FakeAdapter()
    ok = maybe_compact(store, fa, "s1", recent_window_tokens=1000, budget_tokens=20000)
    assert ok is False
    assert store.get_summary("s1") is None


def test_maybe_compact_never_raises(store):
    """压缩是增强不是主线 —— 任何失败都不能抛。"""
    store.append("s1", _history(20))

    class Boom:
        def complete(self, *a, **kw):
            raise RuntimeError("boom")

    # 不应抛异常
    ok = maybe_compact(store, Boom(), "s1", recent_window_tokens=50, budget_tokens=100)
    assert ok is False


def test_load_prepends_summary(store):
    store.append("s1", [Message(role="user", text="早"), Message(role="assistant", text="早啊")])
    store.set_summary("s1", "Topic: 打招呼")

    msgs = store.load("s1")
    assert msgs[0].role == "system"
    assert msgs[0].text == SUMMARY_HEADER + "Topic: 打招呼"
    # 摘要之后是原文（带日期线）
    assert any(m.role == "user" and "早" in m.text for m in msgs)


def test_load_summary_absent_returns_plain(store):
    store.append("s1", [Message(role="user", text="早")])
    msgs = store.load("s1")
    assert msgs[0].role == "user"
    assert not any(m.role == "system" for m in msgs)


def test_load_recent_window_tokens(store):
    """token 预算窗口：保留最新，压掉最老。"""
    h = _history(20, "这是一段比较长的中文消息内容" * 2)
    store.append("s1", h)

    msgs = store.load("s1", recent_window_tokens=60)
    # 预算 60 只能装几条
    assert len(msgs) < len(h)
    # 最后一条必在
    assert any("19" in m.text for m in msgs if m.text)


def test_load_full_returns_all(store):
    h = _history(15, "内容" * 3)
    store.append("s1", h)
    full = store.load_full("s1")
    assert len(full) == 15


def test_set_summary_upsert(store):
    store.set_summary("s1", "第一版")
    assert store.get_summary("s1") == "第一版"
    store.set_summary("s1", "第二版")
    assert store.get_summary("s1") == "第二版"
