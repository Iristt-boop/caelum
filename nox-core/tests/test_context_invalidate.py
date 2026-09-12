"""「写过了 → 把 Provider 缓存打掉」这条链路。

## 为什么专门测它

Provider 是按 TTL 缓存的（`health` 6 小时、`todo` 30 分钟、`music` 3 分钟、
`memory` 5 分钟），而 **2026-09-12 之前生产代码里 `invalidate()` 一次都没被
调用过** —— 只有 `_client.py` 那个同名的 MCP token 缓存在用。

后果不是报错，是**「他不记得」**：她刚说「我来例假了」、他答「记下了」，
然后最多 6 小时内他照旧按「没有经期记录」说话。TTL 实际上成了
**「你刚告诉他的事，他最长能多久当作没听见」**。

## 这条链有三个环节

    工具 → ToolContext.wrote() → LoopResult.dirty_providers
         → Nox._flush_dirty() → registry.invalidate()

任何一环没接上，表现**完全一样**（静悄悄地记不住），所以每一环都单独测，
最后一条是端到端回归：写完之后下一轮**必须**看得见。
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import StreamEvent, ToolCall, ToolSpec, Turn  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402
from context import BaseContextProvider, ContextProviderRegistry  # noqa: E402
from nox import Nox  # noqa: E402
from tools import context  # noqa: E402


# --------------------------------------------------------------- 假的零件

class _FakeAdapter:
    """第一轮调工具，第二轮说话。"""

    name = "fake"

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.calls = 0

    def _turns(self):
        return [
            Turn(stop_reason="tool_use", text="",
                 tool_calls=[ToolCall(id="t1", name=self.tool_name, arguments={})]),
            Turn(stop_reason="end_turn", text="记下了"),
        ]

    def complete(self, messages, tools, **kw):
        turn = self._turns()[self.calls]
        self.calls += 1
        return turn

    def stream(self, messages, tools, **kw):
        turn = self._turns()[self.calls]
        self.calls += 1
        if turn.text:
            yield StreamEvent("text", text=turn.text)
        yield StreamEvent("done", turn=turn)


class _StoreProvider(BaseContextProvider):
    """读一个可变 dict 的 Provider —— 用来模拟「库被写了」。"""

    name = "todo"
    section = "user"
    ttl = timedelta(minutes=30)

    def __init__(self, store: dict, **kw) -> None:
        super().__init__(**kw)
        self.store = store
        self.calls = 0

    def _fetch(self, turn) -> dict:
        self.calls += 1
        return {"available": True, "items": list(self.store["items"])}

    def render(self, state: dict) -> str:
        return "、".join(state.get("items") or [])


class _StubNox:
    """只为了调用**真实的** `Nox._flush_dirty` —— 它只用到 `self.context`。"""

    def __init__(self, registry) -> None:
        self.context = registry


MARK_SPEC = ToolSpec(name="mark", description="登记写过 todo",
                     parameters={"type": "object", "properties": {}}, side_effect="read")


def _mark_tool(_args: dict) -> str:
    context.wrote("todo")
    return "记下了"


def _make_write_tool(store: dict):
    """写库 + 登记，一次做全（模拟真实工具）。"""
    def handler(_args: dict) -> str:
        store["items"].append("买传感器")
        context.wrote("todo")
        return "记下了"
    return handler


def _loop(tool_name: str, handler) -> AgentLoop:
    loop = AgentLoop(adapter=_FakeAdapter(tool_name))
    loop.register(ToolSpec(name=tool_name, description="x",
                           parameters={"type": "object", "properties": {}}, side_effect="read"), handler)
    return loop


# ------------------------------------------------- 环节一：工具 → ToolContext

def test_不在轮次里登记是静默的():
    """工具被单元测试/脚本直接调用时没有轮次上下文 —— 不许炸。

    所以 `context.wrote()` 内部自己判空，调用方不用写 `if ctx:`。
    少一处判空，就少一处「忘了判」的机会。
    """
    assert context.current() is None
    context.wrote("todo")          # 不抛


def test_登记多个会去重():
    with context.scope() as ctx:
        ctx.wrote("todo", "health")
        ctx.wrote("todo")
        assert ctx.dirty == {"todo", "health"}


# ------------------------------------- 环节二：ToolContext → LoopResult

def test_非流式把写过的状态带出来():
    r = _loop("mark", _mark_tool).run("记一下")
    assert r.outcome == "answered"
    assert r.dirty_providers == ["todo"]


def test_流式也把写过的状态带出来():
    """`run_stream` 是另一个入口 —— 非流式接上了不代表这条也接上了。

    （这条和 `test_attachments.py` 里那条是同一个理由：
      附件当初就是漏了流式这一条，附件的教训这里照样适用。）
    """
    result = None
    for ev in _loop("mark", _mark_tool).run_stream("记一下"):
        if ev.type == "done":
            result = ev.result  # type: ignore[attr-defined]
    assert result is not None
    assert result.dirty_providers == ["todo"]


def test_没调工具时是空的():
    loop = _loop("mark", _mark_tool)
    loop.adapter.calls = 1        # 直接跳到「说话」那轮
    assert loop.run("在吗").dirty_providers == []


def test_两轮之间不串():
    """上下文按轮次隔离。

    ⚠️ 这条比附件那条更要紧：附件串了会多发一张图（看得见），
        dirty 串了会**每轮都清一次别人的缓存**（看不见，只是变慢变贵）。
    """
    loop = _loop("mark", _mark_tool)
    first = loop.run("记一下")
    loop.adapter.calls = 1
    second = loop.run("在吗")
    assert first.dirty_providers == ["todo"]
    assert second.dirty_providers == []


# ------------------------------- 环节三 + 端到端：真的把缓存打掉了

def test_flush_把登记过的_provider_缓存清掉():
    store = {"items": ["买猫粮"]}
    reg = ContextProviderRegistry()
    p = _StoreProvider(store)
    reg.register(p)

    assert reg.render(["todo"]) == "买猫粮"
    assert p.calls == 1

    store["items"].append("买传感器")

    # 没登记 → 缓存照挡（这是对的：TTL 6 小时的 Provider 不该每轮重拉）
    assert reg.render(["todo"]) == "买猫粮"
    assert p.calls == 1

    # 登记 + 收口层清缓存
    with context.scope() as ctx:
        ctx.wrote("todo")
        Nox._flush_dirty(_StubNox(reg), _StubResult(ctx.dirty))

    assert reg.render(["todo"]) == "买猫粮、买传感器"
    assert p.calls == 2


class _StubResult:
    def __init__(self, dirty) -> None:
        self.dirty_providers = sorted(dirty)


def test_端到端_工具写完下一轮他就看得见():
    """🔴 这条是「他记不记得」的正身。

    她说了句话 → 工具真的写进库了 → 他嘴上说「记下了」 →
    **下一轮他手里那份快照必须已经包含这件事**。

    2026-09-12 之前这条链是断的：写成功了，但没有任何人通知缓存，
    于是他照旧按写之前那份说话，而且全程不报错。
    """
    store = {"items": ["买猫粮"]}
    reg = ContextProviderRegistry()
    p = _StoreProvider(store)
    reg.register(p)

    # 写之前他已经有这份快照了
    assert reg.render(["todo"]) == "买猫粮"
    assert p.calls == 1

    # 她的话 → 工具写库 + 登记
    loop = _loop("write", _make_write_tool(store))
    result = loop.run("帮我把买传感器记进待办")
    assert result.dirty_providers == ["todo"], "工具登记的状态没传到 LoopResult"

    # 还没清缓存时，他手里仍是旧的 —— 这就是线上那个 bug 的形状
    assert reg.render(["todo"]) == "买猫粮"

    # 收口层（真实方法）清掉 → 下一轮立刻看得见
    Nox._flush_dirty(_StubNox(reg), result)
    assert reg.render(["todo"]) == "买猫粮、买传感器"
    assert p.calls == 2


def test_flush_对没登记的轮次什么都不做():
    """绝大多数轮次没有写操作 —— 不许因此去真打外部服务。"""
    store = {"items": ["买猫粮"]}
    reg = ContextProviderRegistry()
    p = _StoreProvider(store)
    reg.register(p)
    reg.render(["todo"])
    assert p.calls == 1

    Nox._flush_dirty(_StubNox(reg), _StubResult([]))
    reg.render(["todo"])
    assert p.calls == 1, "没有写操作却把缓存清了，等于每轮真拉一次"


def test_flush_没有_registry_时不炸():
    """假 core / 没启上下文的场景：静默跳过，不许把一轮对话带崩。"""
    Nox._flush_dirty(_StubNox(None), _StubResult(["todo"]))
