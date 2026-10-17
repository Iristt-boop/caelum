"""减重第二步：终结型工具不再多跑一轮（2026-10-17）。

10-16 起账本：23 轮多次调用里 14 轮是「说完话 + 发表情 / 记心情」后又多调一次模型，而那一次也都写了字 ——
多出来的收尾句，10-17 冒烟里还漏出一句草稿「用户问的是 N150 小主机功耗，我查了两条来源。」。

守的几条：
  · 说了话 + 全是终结型 + 都成功 → 只调一次模型（流式 / 非流式）
  · 混了一个要看结果的 / 终结型失败了 / 还没说话 / 开关关了 → 照旧再跑一轮
  · 发表情、发语音、记心情、玩具 四件是终结型；查东西的不是
  · Anthropic：历史以 tool_results 结尾、下一条又是 user → 并成一条（tool_result 在前）
"""

from __future__ import annotations

import pytest

from agent.adapters import _merge_same_role
from agent.llm import StreamEvent, ToolCall, ToolSpec, Turn, Usage
from agent.loop import AgentLoop


def _spec(name, terminal=False):
    return ToolSpec(name=name, description="t", parameters={"type": "object", "properties": {}},
                    side_effect="write", terminal=terminal)


class _Ad:
    name = "fake"

    def __init__(self, first_text="想你啦", tools=("send_meme",)):
        self.calls = 0
        self.first = Turn(stop_reason="tool_use", text=first_text,
                          tool_calls=[ToolCall(id=f"c{i}", name=t, arguments={}) for i, t in enumerate(tools)],
                          usage=Usage(input_tokens=30000))
        self.second = Turn(stop_reason="end_turn", text="用户问的是什么什么，我查了两条来源。", usage=Usage(input_tokens=31000))

    def _next(self):
        self.calls += 1
        return self.first if self.calls == 1 else self.second

    def complete(self, messages, tools, **kw):
        return self._next()

    def stream(self, messages, tools, **kw):
        t = self._next()
        if t.text:
            yield StreamEvent("text", text=t.text)
        yield StreamEvent("done", turn=t)


def _loop(ad, fail=()):
    loop = AgentLoop(adapter=ad)
    for name, term in (("send_meme", True), ("set_mood", True), ("web_search", False)):
        def h(args, n=name):
            if n in fail:
                raise RuntimeError("没发出去")
            return "ok"
        loop.register(_spec(name, term), h)
    return loop


def _stream_text(loop):
    evs = list(loop.run_stream("在吗", history=[]))
    return "".join(e.text for e in evs if e.type == "text"), evs[-1].result


def test_红线_说了话_全是终结型_只调一次():
    ad = _Ad(tools=("send_meme", "set_mood"))
    r = _loop(ad).run("在吗", history=[])
    assert ad.calls == 1 and r.ok and r.text == "想你啦"
    assert r.messages[-1].role == "tool_results", "发表情的结果照常留在历史里"


def test_红线_流式也一样_那句草稿不会出来():
    ad = _Ad()
    text, r = _stream_text(_loop(ad))
    assert ad.calls == 1 and "想你啦" in text and "我查了两条来源" not in text and r.outcome == "answered"


@pytest.mark.parametrize("kw,fail", [
    ({"tools": ("send_meme", "web_search")}, ()),
    ({"tools": ("send_meme",)}, ("send_meme",)),
    ({"first_text": "", "tools": ("set_mood",)}, ()),
], ids=["混了要看结果的", "终结型失败了", "还没说话"])
def test_这些情况照旧再跑一轮(kw, fail):
    ad = _Ad(**kw)
    _loop(ad, fail).run("在吗", history=[])
    assert ad.calls == 2


def test_开关关了退回老行为(monkeypatch):
    monkeypatch.setenv("NOX_TERMINAL_STOP", "off")
    ad = _Ad()
    _loop(ad).run("在吗", history=[])
    assert ad.calls == 2


def test_四件终结型_查东西的不是():
    from tools import intimate, mood_tool
    assert intimate.MEME_SPEC.terminal and intimate.VOICE_SPEC.terminal and intimate.TOY_SET_SPEC.terminal
    assert mood_tool.MOOD_SPEC.terminal
    from tools import desk
    assert not any(s.terminal for s in desk.SPECS)


def test_Anthropic_相邻两条user并成一条_tool_result在前():
    native = [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": [{"type": "text", "text": "想你啦"}, {"type": "tool_use", "id": "c1"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}]},
        {"role": "user", "content": "嗯嗯"},
    ]
    out = _merge_same_role(native)
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[-1]["content"] == [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"},
                                  {"type": "text", "text": "嗯嗯"}]


def test_红线_表情名写错了_工具登记没做成_照旧再跑一轮让他改():
    from tools import context

    class Ad(_Ad):
        pass

    ad = Ad()
    loop = AgentLoop(adapter=ad)

    def bad_meme(args):
        context.unfinished("send_meme")
        return "没有「摸摸头」这个表情，没发出去。"

    loop.register(_spec("send_meme", True), bad_meme)
    loop.run("在吗", history=[])
    assert ad.calls == 2


def test_真的send_meme_名单外的名字会登记没做成():
    from tools import context, intimate
    ctx = context.ToolContext(session_id="s")
    h = intimate.make_handlers(bridge=None)
    with context.bind(ctx):
        h["send_meme"]({"tag": "这个表情肯定不存在"})
    assert ctx.unfinished == {"send_meme"}


def test_上一次写错名字_这次改对了_就该能收尾():
    from tools import context

    class Ad3:
        name = "fake"

        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools, **kw):
            self.calls += 1
            tag = "错名" if self.calls == 1 else "开心"
            return Turn(stop_reason="tool_use", text="想你啦" if self.calls == 2 else "",
                        tool_calls=[ToolCall(id=f"c{self.calls}", name="send_meme", arguments={"tag": tag})],
                        usage=Usage()) if self.calls <= 2 else Turn(stop_reason="end_turn", text="多余的一句", usage=Usage())

    def meme(args):
        if args["tag"] == "错名":
            context.unfinished("send_meme")
            return "没有这个表情"
        return "发了"

    ad = Ad3()
    loop = AgentLoop(adapter=ad)
    loop.register(_spec("send_meme", True), meme)
    r = loop.run("在吗", history=[])
    assert ad.calls == 2 and r.text == "想你啦"


def test_红线_Anthropic真发出去的请求里没有相邻两条user():
    """从使用方断言：看 adapter 真拼出来的 messages，不只看 _merge_same_role 本身"""
    from types import SimpleNamespace

    from agent.adapters import AnthropicAdapter
    from agent.llm import Message, ToolResult

    ad = AnthropicAdapter.__new__(AnthropicAdapter)
    ad.cfg = SimpleNamespace(model="claude-x", max_tokens=1000, cache_ttl=None, cache_style=None)
    hist = [
        Message(role="user", text="在吗"),
        Message(role="assistant", text="想你啦", tool_calls=[ToolCall(id="c1", name="send_meme", arguments={})]),
        Message(role="tool_results", tool_results=[ToolResult(call_id="c1", content="发了")]),
        Message(role="user", text="嗯嗯"),
    ]
    kw = ad._build_kwargs(hist, [], system="人设", dynamic_system=None, depth=None, max_tokens=None)
    roles = [m["role"] for m in kw["messages"]]
    assert all(a != b for a, b in zip(roles, roles[1:])), roles
