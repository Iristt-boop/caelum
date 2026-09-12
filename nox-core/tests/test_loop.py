"""Loop 的核心行为测试 —— 全部用假 adapter，不打真 API。

这些用例覆盖的是 loop 最容易写错的地方：结局区分、失败回传、
并行结果打包。跑得快，改 loop 时随手就能验。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message, ToolCall, ToolSpec, Turn, Usage  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402


class FakeAdapter:
    """按预设脚本逐轮返回 Turn，并记录每轮收到的 messages。"""

    name = "fake"

    def __init__(self, script: list[Turn]) -> None:
        self.script = script
        self.seen: list[list[Message]] = []
        self.seen_dynamic: list[str | None] = []

    def complete(
        self, messages, tools, *, system=None, dynamic_system=None,
        depth=None, max_tokens=None,
    ):
        self.seen.append([*messages])
        self.seen_dynamic.append(dynamic_system)
        if not self.script:
            return Turn(stop_reason="end_turn", text="脚本用完了")
        return self.script.pop(0)


def spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="测试用", parameters={"type": "object", "properties": {}})


def test_answered_directly():
    loop = AgentLoop(adapter=FakeAdapter([Turn(stop_reason="end_turn", text="在的")]))
    r = loop.run("在吗")
    assert r.outcome == "answered"
    assert r.ok and r.text == "在的" and r.iterations == 1


def test_max_tokens_is_not_answered():
    """截断必须和答完区分开，否则半截答案会被当成最终回复发出去。"""
    loop = AgentLoop(adapter=FakeAdapter([Turn(stop_reason="max_tokens", text="说到一半")]))
    r = loop.run("讲个长故事")
    assert r.outcome == "truncated"
    assert not r.ok
    assert "截断" in (r.detail or "")


def test_refusal_with_empty_text():
    """拒答时 text 是 None —— loop 不能去读 content 里的东西。"""
    loop = AgentLoop(adapter=FakeAdapter([Turn(stop_reason="refusal", error="拒绝")]))
    r = loop.run("...")
    assert r.outcome == "refused"
    assert r.text is None


def test_tool_success_then_answer():
    adapter = FakeAdapter([
        Turn(stop_reason="tool_use", tool_calls=[ToolCall(id="c1", name="weather", arguments={})]),
        Turn(stop_reason="end_turn", text="今天晴"),
    ])
    loop = AgentLoop(adapter=adapter)
    loop.register(spec("weather"), lambda a: "晴，26 度")

    r = loop.run("天气如何")
    assert r.outcome == "answered" and r.iterations == 2

    # 第二轮必须能看到工具结果，且不是错误
    second = adapter.seen[1]
    results = [m for m in second if m.role == "tool_results"]
    assert len(results) == 1
    assert results[0].tool_results[0].is_error is False


def test_tool_failure_is_reported_verbatim():
    """工具抛异常时，错误类型和消息必须原样进上下文 —— 这是不许编的地基。"""
    adapter = FakeAdapter([
        Turn(stop_reason="tool_use", tool_calls=[ToolCall(id="c1", name="ha", arguments={"x": 1})]),
        Turn(stop_reason="end_turn", text="没能开灯"),
    ])
    loop = AgentLoop(adapter=adapter)

    def boom(_args):
        raise ConnectionError("HA 连不上")

    loop.register(spec("ha"), boom)
    r = loop.run("开灯")

    assert r.outcome == "answered"
    result = [m for m in adapter.seen[1] if m.role == "tool_results"][0].tool_results[0]
    assert result.is_error is True
    assert "ConnectionError" in result.content
    assert "HA 连不上" in result.content
    assert "{'x': 1}" in result.content  # 参数也要给模型看


def test_empty_result_is_not_an_error():
    """查不到 ≠ 调用失败。混在一起模型必编。"""
    adapter = FakeAdapter([
        Turn(stop_reason="tool_use", tool_calls=[ToolCall(id="c1", name="search", arguments={})]),
        Turn(stop_reason="end_turn", text="没查到"),
    ])
    loop = AgentLoop(adapter=adapter)
    loop.register(spec("search"), lambda a: "")

    loop.run("找找")
    result = [m for m in adapter.seen[1] if m.role == "tool_results"][0].tool_results[0]
    assert result.is_error is False
    assert "不是错误" in result.content


def test_unknown_tool_returns_available_list():
    adapter = FakeAdapter([
        Turn(stop_reason="tool_use", tool_calls=[ToolCall(id="c1", name="nope", arguments={})]),
        Turn(stop_reason="end_turn", text="没有这个工具"),
    ])
    loop = AgentLoop(adapter=adapter)
    loop.register(spec("real_one"), lambda a: "ok")

    loop.run("试试")
    result = [m for m in adapter.seen[1] if m.role == "tool_results"][0].tool_results[0]
    assert result.is_error is True
    assert "real_one" in result.content


def test_parallel_results_share_one_message():
    """并行调用的结果必须在同一条消息里，拆开会让模型以后不再并行。"""
    adapter = FakeAdapter([
        Turn(
            stop_reason="tool_use",
            tool_calls=[
                ToolCall(id="c1", name="a", arguments={}),
                ToolCall(id="c2", name="b", arguments={}),
            ],
        ),
        Turn(stop_reason="end_turn", text="都好了"),
    ])
    loop = AgentLoop(adapter=adapter)
    loop.register(spec("a"), lambda x: "A")
    loop.register(spec("b"), lambda x: "B")

    loop.run("并行")
    msgs = [m for m in adapter.seen[1] if m.role == "tool_results"]
    assert len(msgs) == 1, "结果被拆成了多条消息"
    assert len(msgs[0].tool_results) == 2


def test_repeated_failure_stops_retrying():
    """同一工具连续失败到上限就收手，别烧钱空转。"""
    adapter = FakeAdapter([
        Turn(stop_reason="tool_use", tool_calls=[ToolCall(id="c1", name="ha", arguments={})]),
        Turn(stop_reason="tool_use", tool_calls=[ToolCall(id="c2", name="ha", arguments={})]),
        Turn(stop_reason="end_turn", text="不该走到这"),
    ])
    loop = AgentLoop(adapter=adapter, failure_limit=2)

    def boom(_args):
        raise TimeoutError("超时")

    loop.register(spec("ha"), boom)
    r = loop.run("开灯")

    assert r.outcome == "tool_stuck"
    assert "ha" in (r.detail or "")


def test_exhausted_is_distinct_from_answered():
    """跑满上限和答完是两种结局 —— 那个 for/else 就是为这个。"""
    adapter = FakeAdapter([
        Turn(stop_reason="tool_use", tool_calls=[ToolCall(id=f"c{i}", name="loop_tool", arguments={})])
        for i in range(10)
    ])
    loop = AgentLoop(adapter=adapter, max_iterations=3)
    loop.register(spec("loop_tool"), lambda a: "还没完")

    r = loop.run("绕圈")
    assert r.outcome == "exhausted"
    assert not r.ok
    assert r.iterations == 3


def test_bad_json_arguments_do_not_crash():
    """参数解析失败要变成一次工具失败回给模型，不是抛异常炸掉整轮。"""
    adapter = FakeAdapter([
        Turn(
            stop_reason="tool_use",
            tool_calls=[ToolCall(id="c1", name="t", arguments={"_parse_error": "不是合法 JSON"})],
        ),
        Turn(stop_reason="end_turn", text="参数写错了"),
    ])
    loop = AgentLoop(adapter=adapter)
    loop.register(spec("t"), lambda a: "不该被调到")

    r = loop.run("试试")
    assert r.outcome == "answered"
    result = [m for m in adapter.seen[1] if m.role == "tool_results"][0].tool_results[0]
    assert result.is_error is True
    assert "不是合法 JSON" in result.content


def test_usage_accumulates_across_iterations():
    adapter = FakeAdapter([
        Turn(
            stop_reason="tool_use",
            tool_calls=[ToolCall(id="c1", name="t", arguments={})],
            usage=Usage(input_tokens=100, output_tokens=20),
        ),
        Turn(stop_reason="end_turn", text="好", usage=Usage(input_tokens=150, output_tokens=30)),
    ])
    loop = AgentLoop(adapter=adapter)
    loop.register(spec("t"), lambda a: "ok")

    r = loop.run("算账")
    assert r.usage.input_tokens == 250
    assert r.usage.output_tokens == 50


# ---------------------------------------------------------------------------
# 墙钟 deadline（审计 1.1）
#
# 判据原文是「打一个假死的上游，Core 不会整体卡住」。所以这里的假 adapter
# **真的会慢**（每轮 sleep），而不是我直接把时钟改掉 —— 后者测的是我的
# mock 写对没有，不是超时到底拦不拦得住。
# 慢多少：每轮 0.05s，预算 0.12s，所以第 3 轮之前必然被拦下。
# ---------------------------------------------------------------------------


class SlowAdapter:
    """每轮都要花点时间、而且**永远不结束**的上游 —— 模拟半死的 provider。"""

    name = "slow"

    def __init__(self, per_call_s: float = 0.05) -> None:
        self.per_call_s = per_call_s
        self.calls = 0

    def complete(self, messages, tools, **kw):
        self.calls += 1
        time.sleep(self.per_call_s)
        # 一直要调工具 = 一直不收尾，把循环喂满
        return Turn(
            stop_reason="tool_use",
            tool_calls=[ToolCall(id=f"c{self.calls}", name="t", arguments={})],
        )


def _slow_loop(**kw) -> tuple[AgentLoop, SlowAdapter]:
    adapter = SlowAdapter()
    loop = AgentLoop(adapter=adapter, max_iterations=50, **kw)
    loop.register(spec("t"), lambda a: "ok")
    return loop, adapter


def test_deadline_stops_a_slow_upstream():
    """🔴 这条是 1.1 的判据：上游一直慢，循环必须自己收尾。"""
    loop, adapter = _slow_loop(deadline_s=0.12)
    r = loop.run("在吗")

    assert r.outcome == "timeout"
    # 没有跑满 50 轮 —— 是被时间拦下的，不是被轮数
    assert adapter.calls < 50
    assert r.detail and "预算" in r.detail


def test_timeout_is_not_exhausted():
    """🔴 两种结局不许混：一个是他在打转，一个是上游在拖。

    合并了就等于把"该去看提示词"和"该去看 provider"这两条线索都丢掉。
    """
    slow, _ = _slow_loop(deadline_s=0.12)
    assert slow.run("嗯").outcome == "timeout"

    # 同样跑不完，但这次是**轮数**用光的：上游很快，只是一直要调工具
    fast = AgentLoop(
        adapter=FakeAdapter([
            Turn(stop_reason="tool_use", tool_calls=[ToolCall(id="c", name="t", arguments={})])
            for _ in range(3)
        ]),
        max_iterations=3,
    )
    fast.register(spec("t"), lambda a: "ok")
    assert fast.run("嗯").outcome == "exhausted"


@pytest.mark.parametrize("deadline", [None, 0, -1])
def test_no_deadline_keeps_old_behaviour(deadline):
    """不配 deadline 的调用方（后台任务、测试）行为一个字都不能变。"""
    loop = AgentLoop(
        adapter=FakeAdapter([Turn(stop_reason="end_turn", text="在的")]),
        deadline_s=deadline,
    )
    assert loop.run("在吗").outcome == "answered"


def test_deadline_does_not_cut_into_a_call_in_flight():
    """⚠️ 把 deadline 的**真实边界**钉死：它只拦"开下一轮"。

    正在进行的那次调用（SDK 自己在重试）是插不进去的。所以最坏时长是
    `deadline + 一次调用的最坏值`。这条测试存在的意义是：以后谁把它读成
    "180 秒一定返回"，这里会告诉他不是。
    """
    adapter = SlowAdapter(per_call_s=0.25)
    loop = AgentLoop(adapter=adapter, max_iterations=50, deadline_s=0.01)
    loop.register(spec("t"), lambda a: "ok")

    started = time.monotonic()
    r = loop.run("在吗")
    elapsed = time.monotonic() - started

    assert r.outcome == "timeout"
    # 预算只有 0.01s，但第一次调用是完整跑完的 —— 所以实际耗时 ≥ 0.25s
    assert elapsed >= 0.25
    assert adapter.calls == 1


def test_her_message_exists_for_timeout():
    """结局必须有对应的人话，否则超时那次她屏幕上是空的。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from api.server import _OUTCOME_TEXT  # noqa: PLC0415

    assert "timeout" in _OUTCOME_TEXT
    assert _OUTCOME_TEXT["timeout"] != _OUTCOME_TEXT["exhausted"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
