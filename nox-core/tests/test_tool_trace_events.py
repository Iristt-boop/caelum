"""工具调用展示的事件载荷（2026-09-19）。

钉三样：start 带入参预览（下划线键滤掉）、end 带结果摘要 + 耗时 +
子步骤（ToolContext.report_step 上报的那层）、子步骤顺序保持。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.loop import AgentLoop, _arg_preview
from agent.llm import StreamEvent, ToolCall, Turn
from tests.test_stream import FakeStreamAdapter, spec


def test_tool_events_carry_trace_payload():
    from tools import context as tool_context

    adapter = FakeStreamAdapter([
        ([], Turn(stop_reason="tool_use",
                  tool_calls=[ToolCall(id="c1", name="add_todo",
                                       arguments={"text": "买牛奶", "_internal": "x"})])),
        (["好的"], Turn(stop_reason="end_turn", text="好的")),
    ])
    loop = AgentLoop(adapter=adapter)

    def handler(a):
        ctx = tool_context.current()
        assert ctx is not None, "工具执行时 context 必须在（bind 过的）"
        ctx.report_step("校验待办内容", raw_cmd="validate()")
        ctx.report_step("写入待办库", diff="+1 -0")
        return "已添加：买牛奶"

    loop.register(spec("add_todo"), handler)

    saw_start = saw_end = False
    for ev in loop.run_stream("帮我加个待办"):
        if ev.type == "tool_start":
            saw_start = True
            assert ev.args == {"text": "买牛奶"}, "下划线内部键不该进展示卡片"
        elif ev.type == "tool_end":
            saw_end = True
            assert ev.ok is True
            assert "已添加" in ev.summary
            assert ev.duration_ms >= 0
            assert [s["desc"] for s in ev.sub_commands] == ["校验待办内容", "写入待办库"]
            assert ev.sub_commands[1]["diff"] == "+1 -0"
    assert saw_start and saw_end


def test_arg_preview_truncates_and_filters():
    p = _arg_preview({"a": "x" * 500, "_skip": "y", "n": 3})
    assert p["a"].endswith("…") and len(p["a"]) <= 82
    assert "_skip" not in p
    assert p["n"] == "3"   # 非字符串值 json 化成显示文本
