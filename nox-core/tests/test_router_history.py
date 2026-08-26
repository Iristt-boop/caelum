"""验证轻量路径的历史裁剪 —— 不打网络。

背景：实测发现"晚安"这种一个字的问候，会背着前面 28K 的对话历史发出去。
省掉了 12K 前缀却付了 28K 历史，越聊越贵。裁剪之后要保证两件事同时成立：
  1. 发出去的请求只带最近几条
  2. 返回的 history 仍然是完整的（不能真把上下文截断）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message, ToolCall, ToolResult, Turn, Usage  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402
from router.router import Router  # noqa: E402


class RecordingAdapter:
    """记录每次收到的 messages。"""

    name = "recording"

    def __init__(self, reply: str = "好") -> None:
        self.reply = reply
        self.seen: list[list[Message]] = []

    def complete(
        self, messages, tools, *, system=None, dynamic_system=None,
        depth=None, max_tokens=None,
    ):
        self.seen.append([*messages])
        return Turn(stop_reason="end_turn", text=self.reply, usage=Usage(input_tokens=10))


def make_router(light: RecordingAdapter) -> Router:
    full = AgentLoop(adapter=RecordingAdapter("完整路径的回答"))
    return Router(full, system_prompt="（12K 静态前缀）", light_adapter=light)


def long_history(turns: int) -> list[Message]:
    out: list[Message] = []
    for i in range(turns):
        out.append(Message(role="user", text=f"用户第 {i} 句"))
        out.append(Message(role="assistant", text=f"回复第 {i} 句"))
    return out


def test_light_path_trims_history():
    light = RecordingAdapter("晚安糖糖")
    router = make_router(light)

    r = router.handle("晚安", long_history(20))

    assert r.decision.light
    sent = light.seen[0]
    # 最近 4 条 + 当前这句
    assert len(sent) == 5, f"发出去 {len(sent)} 条，应该是 5"
    assert sent[-1].text == "晚安"
    # 最老的那句不该出现在请求里
    assert all("第 0 句" not in (m.text or "") for m in sent)


def test_light_path_returns_full_history():
    """裁剪只作用于这一次请求 —— 返回的上下文必须完整，
    否则下一句正事就丢了前情。"""
    light = RecordingAdapter("晚安糖糖")
    router = make_router(light)

    history = long_history(20)
    r = router.handle("晚安", history)

    # 原有 40 条 + 这轮的 user + assistant
    assert len(r.messages) == 42
    assert r.messages[0].text == "用户第 0 句"
    assert r.messages[-1].text == "晚安糖糖"


def test_light_path_skips_tool_records():
    """轻量路径没有工具，带着工具调用记录只会让模型困惑，而且很长。"""
    light = RecordingAdapter("在呢")
    router = make_router(light)

    history = [
        Message(role="user", text="开灯"),
        Message(
            role="assistant",
            text="好",
            tool_calls=[ToolCall(id="c1", name="ha", arguments={})],
        ),
        Message(role="tool_results", tool_results=[ToolResult(call_id="c1", content="已开灯")]),
        Message(role="assistant", text="开好了"),
    ]
    router.handle("在吗", history)

    sent = light.seen[0]
    assert all(not m.tool_calls for m in sent)
    assert all(m.role != "tool_results" for m in sent)


def test_full_path_keeps_everything():
    """完整路径不裁剪 —— 它需要全部上下文。"""
    light = RecordingAdapter()
    full_adapter = RecordingAdapter("查到了")
    full = AgentLoop(adapter=full_adapter)
    router = Router(full, system_prompt="（前缀）", light_adapter=light)

    r = router.handle("还记得上次那个表情吗", long_history(20))

    assert not r.decision.light
    sent = full_adapter.seen[0]
    assert len(sent) == 41  # 40 条历史 + 当前
    assert not light.seen  # 轻量 adapter 完全没被调用


def test_light_failure_falls_back_to_full():
    """轻量失败要退回完整路径 —— 省钱不能以答不上话为代价。"""

    class FailingAdapter:
        name = "failing"

        def complete(self, messages, tools, **kw):
            return Turn(stop_reason="error", error="网络抖了")

    full_adapter = RecordingAdapter("完整路径接住了")
    full = AgentLoop(adapter=full_adapter)
    router = Router(full, system_prompt="（前缀）", light_adapter=FailingAdapter())

    r = router.handle("在吗", [])

    assert r.ok
    assert r.text == "完整路径接住了"
    assert full_adapter.seen, "完整路径没有被调用"


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
