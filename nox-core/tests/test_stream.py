"""流式输出测试。假 adapter，不打网络。

重点是两件事：文本要真的边生成边吐、[mood:] 标记绝不能漏到屏幕上。
"""

from __future__ import annotations

import sys
import time
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
    # ⚠️ 正文里刻意也有同样的字 —— 验证只吃 `[…]` 那份、不误伤正文。
    #    tag 名跟 `agent/llm.py` 的 MEME_TAGS 走（2026-09-08 改过名）
    out = "".join(f.feed(c) for c in "得了你一个抱抱[抱抱]真好") + f.flush()
    assert out == "得了你一个抱抱真好"
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
    return ToolSpec(name=name, description="测试", parameters={"type": "object", "properties": {}}, side_effect="read")


def collect(loop: AgentLoop, text: str):
    """跑一遍流，返回（拼起来的文本, 最终 LoopResult）。

    ⚠️ 这里必须显式认 `done`，不能写 `else:` —— 流里还有 tool_start / tool_end，
    那两种事件身上没有 `result`，`else` 会当场 AttributeError（2026-09-07）。
    """
    parts, result = [], None
    for ev in loop.run_stream(text):
        if ev.type == "text":
            parts.append(ev.text)
        elif ev.type == "done":
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


def test_tool_progress_events_bracket_each_call():
    """他动手的那几秒，流里必须有东西 —— 否则语音通话里是一段死寂。

    盯两件事：
      ① tool_start 在**执行之前**发出（不是攒到最后一起补）
      ② 顺序是 start → 真的跑 → end
    """
    seen = []

    adapter = FakeStreamAdapter([
        ([], Turn(stop_reason="tool_use",
                  tool_calls=[ToolCall(id="c1", name="add_todo", arguments={})])),
        (["加好了"], Turn(stop_reason="end_turn", text="加好了")),
    ])
    loop = AgentLoop(adapter=adapter)
    loop.register(spec("add_todo"), lambda a: seen.append("ran") or "ok")

    events = []
    for ev in loop.run_stream("帮我加个待办"):
        if ev.type in ("tool_start", "tool_end"):
            events.append((ev.type, ev.tool, ev.ok))
            # 🔴 关键断言：start 到达时工具**还没跑**。
            # 写成列表推导（`[self._execute(c) ...]`）的话这里会是 ["ran"]，
            # 因为那一批工具早已整个跑完才回到调用方
            if ev.type == "tool_start":
                assert seen == [], "tool_start 应该在工具执行之前就发出来"

    assert events == [("tool_start", "add_todo", True), ("tool_end", "add_todo", True)]
    assert seen == ["ran"]


def test_tool_end_reports_failure():
    """做没做成也要报。她该听见「没成」，而不是以为已经办好了。"""
    adapter = FakeStreamAdapter([
        ([], Turn(stop_reason="tool_use",
                  tool_calls=[ToolCall(id="c1", name="computer_write_file", arguments={})])),
        (["没写成"], Turn(stop_reason="end_turn", text="没写成")),
    ])
    loop = AgentLoop(adapter=adapter)

    def boom(_a):
        raise ConnectionError("够不到她的电脑")

    loop.register(spec("computer_write_file"), boom)

    ends = [ev for ev in loop.run_stream("写个文档") if ev.type == "tool_end"]
    assert len(ends) == 1
    assert ends[0].ok is False


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


# ---------------------------------------------------------------------------
# 墙钟 deadline 的流式版（审计 1.1）
#
# 流式这边比非流式多一件必须守的事：**超时前已经吐给她的字不能吞掉**。
# 吞了的话她屏幕上会留半句没有下文的话，比报个错更糟。
# ---------------------------------------------------------------------------


class SlowStreamAdapter:
    """每轮先吐一小段字，然后慢慢地、永远不收尾。"""

    name = "slow"

    def __init__(self, per_call_s: float = 0.05) -> None:
        self.per_call_s = per_call_s
        self.calls = 0

    def complete(self, *a, **kw):
        raise AssertionError("流式测试不该走 complete")

    def stream(self, messages, tools, **kw):
        self.calls += 1
        time.sleep(self.per_call_s)
        yield StreamEvent("text", text=f"第{self.calls}段。")
        yield StreamEvent(
            "done",
            turn=Turn(
                stop_reason="tool_use",
                tool_calls=[ToolCall(id=f"c{self.calls}", name="t", arguments={})],
            ),
        )


def test_stream_deadline_stops_and_keeps_what_was_said():
    adapter = SlowStreamAdapter()
    loop = AgentLoop(adapter=adapter, max_iterations=50, deadline_s=0.12)
    loop.register(spec("t"), lambda a: "ok")

    text, r = collect(loop, "在吗")

    assert r.outcome == "timeout"
    assert adapter.calls < 50                      # 被时间拦下，不是被轮数
    # 🔴 超时前说出去的话必须还在 —— 这是流式独有的那条
    assert "第1段。" in text


def test_stream_without_deadline_unchanged():
    loop = AgentLoop(
        adapter=FakeStreamAdapter([(["在", "的"], Turn(stop_reason="end_turn", text="在的"))]),
        deadline_s=None,
    )
    text, r = collect(loop, "在吗")
    assert r.outcome == "answered" and text == "在的"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
