"""流式输出测试。假 adapter，不打网络。

重点是两件事：文本要真的边生成边吐、[mood:] 标记绝不能漏到屏幕上。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message, MoodTagFilter, StreamEvent, ToolCall, ToolSpec, Turn  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402


# ------------------------------------------------------ MoodTagFilter

def test_filter_blocks_mood_tag():
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "在呢，糖糖\n[mood:平静]") + f.flush()
    assert out == "在呢，糖糖\n"


def test_filter_passes_normal_brackets():
    """正文里的方括号不能被吃掉。"""
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "这是 [重点] 内容") + f.flush()
    assert out == "这是 [重点] 内容"


def test_filter_handles_split_chunks():
    """标记被切成好几片到达也要挡住 —— 流式下这是常态。"""
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in ["好的", "\n[mo", "od:开", "心]"]) + f.flush()
    assert out == "好的\n"


def test_filter_flushes_incomplete_bracket():
    f = MoodTagFilter()
    out = f.feed("结尾有个 [") + f.flush()
    assert out == "结尾有个 ["


def test_filter_blocks_uppercase_mood_tag():
    """模型偶尔手滑写 [Mood:xxx] —— 生产库漏过 3 次全是大写（2026-09-06）。
    过滤器必须大小写不敏感，且放行普通文本时保持原始大小写。"""
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "在呢\n[Mood:开心]") + f.flush()
    assert out == "在呢\n"

    # 带空格的大写变体（库里那条 [Mood: 撒娇]）
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in ["[Mood", ": 撒娇]"]) + f.flush()
    assert out == ""

    # 大小写混合 + 普通方括号不受影响
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "x[Mo od:]y [OK] [mood:平静]") + f.flush()
    assert out == "x[Mo od:]y [OK] "


def test_filter_blocks_meme_tag_anywhere():
    """[开心] 写在正文里也要吞 —— 收尾时 nox.py 会转成表情事件，
    这里只管别让它当文字上屏（2026-09-06 她报的降级问题）。"""
    # 行首
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "[开心]早安呀") + f.flush()
    assert out == "早安呀"
    # 混在一段话中间
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "得了你一个早安亲亲[早安亲亲]真好") + f.flush()
    assert out == "得了你一个早安亲亲真好"
    # 分片到达
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in ["好[", "呜呜", "呜]好"]) + f.flush()
    assert out == "好好"
    # 不是 tag 的方括号照常放行
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "备注[重要]一下") + f.flush()
    assert out == "备注[重要]一下"
    # 未闭合的 [ 开头流结束放行
    f = MoodTagFilter()
    out = f.feed("写了个 [开心") + f.flush()
    assert out == "写了个 [开心"


def test_filter_blocks_bare_mood_line():
    """行首「mood: 平静」（不带方括号）也要吞 —— 2026-09-06 截图实锤。"""
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "晚安，做个好梦\nmood: 撒娇") + f.flush()
    assert out == "晚安，做个好梦\n"

    # 中文冒号变体
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "嗯\nMood：平静\n") + f.flush()
    assert out == "嗯\n"

    # 词不在她的七个情绪词里 → 不吞，原样放行
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "mood: 不确定\n") + f.flush()
    assert out == "mood: 不确定\n"

    # 行中（非行首）的 mood: 不碰 —— "in a good mood: happy" 是正常英文
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "in a good mood: happy\n") + f.flush()
    assert out == "in a good mood: happy\n"

    # 行首 m 开头的普通英文行，整行缓冲后放行
    f = MoodTagFilter()
    out = "".join(f.feed(c) for c in "me too\n") + f.flush()
    assert out == "me too\n"


# ------------------------------------------------------ 流式 loop

class FakeStreamAdapter:
    name = "fake"

    def __init__(self, scripts: list[tuple[list[str], Turn]]) -> None:
        self.scripts = scripts
        self.calls = 0

    def complete(self, *a, **kw):
        raise AssertionError("流式测试不该走 complete")

    def stream(self, messages, tools, **kw):
        chunks, turn = self.scripts[self.calls]
        self.calls += 1
        for c in chunks:
            yield StreamEvent("text", text=c)
        yield StreamEvent("done", turn=turn)


def spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="测试", parameters={"type": "object", "properties": {}})


def collect(loop: AgentLoop, text: str):
    """跑一遍流，返回（拼起来的文本, 最终 LoopResult）。"""
    parts, result = [], None
    for ev in loop.run_stream(text):
        if ev.type == "text":
            parts.append(ev.text)
        else:
            result = ev.result  # type: ignore[attr-defined]
    return "".join(parts), result


def test_text_streams_out():
    adapter = FakeStreamAdapter([
        (["在", "呢，", "糖糖"], Turn(stop_reason="end_turn", text="在呢，糖糖")),
    ])
    text, r = collect(AgentLoop(adapter=adapter), "在吗")
    assert text == "在呢，糖糖"
    assert r.outcome == "answered"


def test_mood_tag_never_reaches_output():
    """这是流式最容易漏的地方 —— 标记会一个字一个字出现在屏幕上。"""
    adapter = FakeStreamAdapter([
        (["好的", "\n[mood:", "开心]"], Turn(stop_reason="end_turn", text="好的\n[mood:开心]")),
    ])
    text, _ = collect(AgentLoop(adapter=adapter), "嗨")
    assert "mood" not in text
    assert text.strip() == "好的"


def test_text_before_tool_call_is_streamed():
    """模型常先说"让我看看"再调工具，那句话该立刻可见，不该等工具跑完。"""
    adapter = FakeStreamAdapter([
        (["让我看看"], Turn(
            stop_reason="tool_use",
            text="让我看看",
            tool_calls=[ToolCall(id="c1", name="check", arguments={})],
        )),
        (["灯开着呢"], Turn(stop_reason="end_turn", text="灯开着呢")),
    ])
    loop = AgentLoop(adapter=adapter)
    loop.register(spec("check"), lambda a: "on")

    text, r = collect(loop, "灯什么状态")
    assert text == "让我看看灯开着呢"
    assert r.outcome == "answered" and r.iterations == 2


def test_stream_reports_truncated():
    adapter = FakeStreamAdapter([
        (["说到一半"], Turn(stop_reason="max_tokens", text="说到一半")),
    ])
    text, r = collect(AgentLoop(adapter=adapter), "讲个长故事")
    assert text == "说到一半"
    assert r.outcome == "truncated"


def test_stream_reports_error():
    adapter = FakeStreamAdapter([
        ([], Turn(stop_reason="error", error="连不上")),
    ])
    _, r = collect(AgentLoop(adapter=adapter), "在吗")
    assert r.outcome == "error"
    assert "连不上" in (r.detail or "")


def test_stream_tool_failure_is_reported():
    adapter = FakeStreamAdapter([
        ([], Turn(stop_reason="tool_use", tool_calls=[ToolCall(id="c1", name="ha", arguments={})])),
        (["没能开灯"], Turn(stop_reason="end_turn", text="没能开灯")),
    ])
    loop = AgentLoop(adapter=adapter)

    def boom(_a):
        raise ConnectionError("HA 连不上")

    loop.register(spec("ha"), boom)
    _, r = collect(loop, "开灯")

    results = [m for m in r.messages if m.role == "tool_results"][0].tool_results[0]
    assert results.is_error is True
    assert "ConnectionError" in results.content


def test_stream_usage_accumulates():
    from agent.llm import Usage

    adapter = FakeStreamAdapter([
        ([], Turn(stop_reason="tool_use",
                  tool_calls=[ToolCall(id="c1", name="t", arguments={})],
                  usage=Usage(input_tokens=100, output_tokens=10))),
        (["好"], Turn(stop_reason="end_turn", text="好",
                     usage=Usage(input_tokens=150, output_tokens=20))),
    ])
    loop = AgentLoop(adapter=adapter)
    loop.register(spec("t"), lambda a: "ok")
    _, r = collect(loop, "算账")
    assert r.usage.input_tokens == 250 and r.usage.output_tokens == 30


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
