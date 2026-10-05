"""两个工具的登记：只走 loop.register、不静默覆盖、日志格式固定。"""

from __future__ import annotations

import logging

import pytest

from agent.llm import ToolSpec
from agent.loop import AgentLoop
from guide import tools as guide_tools
from guide.loader import load_topics, render_directory
from guide.tools import CAELUM_MAP_SPEC, GUIDE_READ_SPEC, register_all
from guide.world_map import MapDeps
from tools import context as tool_context


def _loop() -> AgentLoop:
    return AgentLoop(adapter=None)  # type: ignore[arg-type]


def test_两个工具登记上了_且都是只读():
    loop = _loop()
    register_all(loop)
    for name in ("guide_read", "caelum_map"):
        assert name in loop.tools
        assert loop.tools[name].spec.side_effect == "none"
    assert GUIDE_READ_SPEC.side_effect == "none" and CAELUM_MAP_SPEC.side_effect == "none"


def test_登记只走loop_register(monkeypatch):
    """绕过 loop.register 直接写 loop.tools 就绕过了「没声明副作用就拒收」那道闸。"""
    loop = _loop()
    seen: list[str] = []
    real = loop.register

    def spy(spec, handler):
        seen.append(spec.name)
        return real(spec, handler)

    monkeypatch.setattr(loop, "register", spy)
    register_all(loop)
    assert sorted(seen) == ["caelum_map", "guide_read"]


def test_register闸会拒收没声明副作用的工具():
    loop = _loop()
    bad = ToolSpec(name="guide_read", description="d", parameters={"type": "object"})
    with pytest.raises(ValueError):
        loop.register(bad, lambda a: "")


def test_重名不许静默覆盖():
    loop = _loop()
    loop.register(ToolSpec(side_effect="none", name="guide_read", description="d",
                           parameters={"type": "object"}), lambda a: "旧的")
    with pytest.raises(ValueError, match="guide_read"):
        register_all(loop)
    assert loop.tools["guide_read"].handler({}) == "旧的"


def test_guide_read_真的返回正文_没命中返回目录():
    loop = _loop()
    topics = load_topics()
    register_all(loop, topics=topics)
    assert loop.tools["guide_read"].handler({"topic": "memory"}) == topics[3].body
    miss = loop.tools["guide_read"].handler({"topic": "x"})
    assert render_directory(topics) in miss
    assert render_directory(topics) in loop.tools["guide_read"].handler({})


def test_caelum_map_走到地图():
    loop = _loop()
    register_all(loop)
    out = loop.tools["caelum_map"].handler({})
    assert "工具（" in out and "（未接入" in out        # 默认 deps 只有工具段有数据
    assert "手册与地图：2 个" in loop.tools["caelum_map"].handler({"scope": "tools"})


def test_caelum_map_用的是传进来的deps():
    loop = _loop()
    register_all(loop, deps=MapDeps(loop=loop, pending=lambda: {"orders": 4, "tasks": 5}))
    assert "点单 4、任务 5" in loop.tools["caelum_map"].handler({"scope": "pending"})


def test_日志格式固定(caplog):
    loop = _loop()
    register_all(loop)
    with caplog.at_level(logging.INFO, logger="guide.tools"):
        with tool_context.bind(tool_context.ToolContext(session_id="test-s1")):
            loop.tools["guide_read"].handler({"topic": "memory"})
            loop.tools["guide_read"].handler({"topic": "nope"})
            loop.tools["caelum_map"].handler({"scope": "tools"})
    msgs = [r.getMessage() for r in caplog.records]
    assert "guide_read topic=memory hit=True session=test-s1" in msgs
    assert "guide_read topic=nope hit=False session=test-s1" in msgs
    assert "caelum_map scope=tools session=test-s1" in msgs


def test_不在轮次里session是None(caplog):
    loop = _loop()
    register_all(loop)
    with caplog.at_level(logging.INFO, logger="guide.tools"):
        loop.tools["caelum_map"].handler({})
    assert "caelum_map scope=None session=None" in [r.getMessage() for r in caplog.records]


def test_模块路径落在手册与地图一组():
    assert guide_tools.register_all.__module__ == "guide.tools"
