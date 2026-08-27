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
    tail_within,
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


class Test增量压缩:
    """🔴 只压新增的，不把旧的重压一遍。

    2026-08-26 查账发现：每次压缩都读「窗口之前的全部」——
    那条会话 118K token ≈ 0.12 元一次，一天二三十次。
    **而且这个成本永远不会下降，只会随历史变长而涨。**

    `SUMMARIZE_SYSTEM` 本来就写着「有旧摘要就合并」，
    也就是说它本来就是为增量设计的，只是调用方一直按全量喂。
    """

    @staticmethod
    def _hist(n: int) -> list[Message]:
        #: 每条都够长，保证会超预算
        return [Message(role="user" if i % 2 == 0 else "assistant",
                        text=f"第{i}条" + "内容" * 200) for i in range(n)]

    def test_有水位时只压新增的(self):
        full = self._hist(60)
        plan = plan_compaction(full, recent_window_tokens=500,
                               budget_tokens=2000, already=40)
        assert plan.should_compact
        #: 压的是第 40 条之后，不是从头
        assert plan.compressible[0].text.startswith("第40条")
        assert len(plan.compressible) < 25, "不该把前 40 条重压一遍"

    def test_没水位时从头压(self):
        full = self._hist(60)
        plan = plan_compaction(full, recent_window_tokens=500,
                               budget_tokens=2000, already=0)
        assert plan.compressible[0].text.startswith("第0条")

    def test_没有新增就不压_也不调模型(self):
        """重压一遍旧的既费钱、又不会让上下文变短。"""
        full = self._hist(60)
        # 水位已经到窗口起点了
        recent = tail_within(full, 500)
        plan = plan_compaction(full, recent_window_tokens=500, budget_tokens=2000,
                               already=len(full) - len(recent))
        assert not plan.should_compact
        assert "没有新增" in plan.reason

    def test_水位越界时重压一遍_而不是从错的位置开始(self, caplog):
        """🔴 从错的位置开始会**跳过中间一段，而且没有任何报错** ——
        表现是他忘了中间那段对话，而日志里什么都看不到。"""
        full = self._hist(60)
        with caplog.at_level("WARNING"):
            plan = plan_compaction(full, recent_window_tokens=500,
                                   budget_tokens=2000, already=9999)
        assert plan.should_compact
        assert plan.compressible[0].text.startswith("第0条"), "该退回全压"
        assert "水位" in " ".join(r.message for r in caplog.records)

    def test_水位默认_0_不传也能用(self):
        """老调用方不传 already 时行为不变。"""
        full = self._hist(60)
        a = plan_compaction(full, 500, 2000)
        b = plan_compaction(full, 500, 2000, already=0)
        assert len(a.compressible) == len(b.compressible)


class Test给够_max_tokens:
    """🔴 2026-08-26 查账查出来的一个静默漏钱。

    日志里每天 10-21 条「压缩未产出内容: max_tokens」——
    `stop_reason` 是 max_tokens，而 `turn.text` 是空的：
    **会思考的模型 reasoning 和正文共用这个预算，4096 全花在思考上了。**

    代价不只是「这次没压成」：每次失败都完整读了一遍历史
    （实测那条会话 118K token ≈ 0.12 元），16 次/天 ≈ 1.9 元/天全打水漂。
    而且压不掉的话上下文继续涨，下一次更贵 —— 是个会自己变大的洞。

    见记忆 `reasoning-tokens-eat-max-tokens`。
    """

    def test_预算要远大于_summary_上限(self):
        seen = {}

        class Spy:
            def complete(self, messages, tools, **kw):
                seen.update(kw)
                return Turn(stop_reason="end_turn", text="ok", usage=Usage())

        summarize(Spy(), None, [Message(role="user", text="hi")])
        #: summary 本身上限约 3000 token（MAX_SUMMARY_CHARS）。
        #: 预算只比它大一点的话，思考一多就又出不来正文了
        assert seen["max_tokens"] >= 8000, "给少了会被思考 token 吃光"

    def test_没产出时要报出白读了多少(self, caplog):
        """⚠️ 只报 stop_reason 的话，看不出一次失败有多贵 ——
        这个洞就是因此漏了不知道多久。"""
        class Empty:
            def complete(self, *a, **kw):
                return Turn(stop_reason="max_tokens", text="", usage=Usage())

        seg = [Message(role="user", text="很长的一段" * 200) for _ in range(5)]
        with caplog.at_level("WARNING"):
            assert summarize(Empty(), None, seg) is None
        msg = " ".join(r.message for r in caplog.records)
        assert "max_tokens" in msg
        assert "白读了" in msg and "5 条" in msg


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
