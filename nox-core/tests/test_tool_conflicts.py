"""工具重名不许静默（R-2，2026-10-29）。

工具登记在 `nox.py` 和 `api/server.py` 两处，外加远端 MCP 报上来的名字；
`test_tool_names_unique.py` 只静态扫得到 11 个模块。撞名时后登记的会盖掉先登记的 ——
08-05 `add_todo` 就这么让他再也记不进「今天」页，一个错都没报。
"""

from __future__ import annotations

import pytest

from agent.llm import ToolSpec
from agent.loop import AgentLoop


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="x", parameters={"type": "object", "properties": {}},
                    side_effect="read")


def _loop() -> AgentLoop:
    return AgentLoop(adapter=None)  # type: ignore[arg-type]


def test_测试里撞名直接炸():
    loop = _loop()
    loop.register(_spec("add_todo"), lambda **_: "a")
    with pytest.raises(ValueError, match="add_todo"):
        loop.register(_spec("add_todo"), lambda **_: "b")


def test_线上撞名不炸但记下来_并且照旧后者赢(monkeypatch):
    monkeypatch.delenv("NOX_STRICT_TOOLS", raising=False)
    loop = _loop()
    loop.register(_spec("add_todo"), lambda **_: "a")
    loop.register(_spec("other"), lambda **_: "o")
    loop.register(_spec("add_todo"), lambda **_: "b")
    assert loop.tool_conflicts == ["add_todo"]
    assert loop.tools["add_todo"].handler() == "b"


def test_不撞名就是空():
    loop = _loop()
    loop.register(_spec("a"), lambda **_: "a")
    loop.register(_spec("b"), lambda **_: "b")
    assert loop.tool_conflicts == []


def test_health_带出_tool_conflicts():
    """晨检读的是 /health 的这个字段；字段没了晨检只会写「没这个字段」，不会红 —— 钉住它在。"""
    import inspect

    from api import server

    src = inspect.getsource(server.create_app)
    assert '"tool_conflicts"' in src
