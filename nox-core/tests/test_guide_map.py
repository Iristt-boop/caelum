"""环境地图：来自运行期、只给白名单字段、依赖缺失不拖垮别的段。"""

from __future__ import annotations

import logging

import pytest

from agent.llm import ToolSpec
from agent.loop import AgentLoop, Tool
from guide.world_map import SCOPES, MapDeps, build_map


def _tool(name: str, effect: str = "none", module: str = "fake.mod") -> Tool:
    def handler(_args: dict) -> str:
        return ""

    handler.__module__ = module
    spec = ToolSpec(side_effect=effect, name=name, description="d",
                    parameters={"type": "object", "properties": {}})
    return Tool(spec=spec, handler=handler)


def _loop(*tools: Tool) -> AgentLoop:
    loop = AgentLoop(adapter=None)  # type: ignore[arg-type]
    for t in tools:
        loop.tools[t.spec.name] = t
    return loop


class _P:
    def __init__(self, section="s", ttl=60):
        self.section, self.ttl = section, ttl


class _Reg:
    def __init__(self, **providers):
        self._p = providers

    def names(self):
        return list(self._p)

    def get(self, n):
        return self._p.get(n)


# ---------------------------------------------------------------- 工具段：来自运行期


def test_工具计数跟着运行期工具表变():
    loop = _loop(_tool("a", module="tools.eryu"), _tool("b", module="tools.eryu"))
    deps = MapDeps(loop=loop)
    assert "共听：2 个" in build_map(deps, "tools")
    loop.tools["c"] = _tool("c", module="tools.eryu")
    assert "共听：3 个" in build_map(deps, "tools")
    del loop.tools["a"], loop.tools["b"]
    assert "共听：1 个" in build_map(deps, "tools")


def test_需要她点头的计数():
    loop = _loop(_tool("x", module="tools.luckin"), _tool("y", "spend", "tools.luckin"),
                 _tool("z", "irreversible", "tools.luckin"))
    out = build_map(MapDeps(loop=loop), "tools")
    assert "3 个，分 1 组；2 个需要她点头" in out
    assert "其中 2 个需要她点头" in out
    loop.tools["w"] = _tool("w", "spend", "tools.luckin")
    assert "3 个需要她点头" in build_map(MapDeps(loop=loop), "tools")


def test_未登记的模块显示模块名():
    out = build_map(MapDeps(loop=_loop(_tool("a", module="brand.new.module"))), "tools")
    assert "brand.new.module：1 个" in out


def test_总览只给分组数量_scope才列名字():
    loop = _loop(_tool("alpha", module="tools.eryu"))
    assert "alpha" not in build_map(MapDeps(loop=loop))
    assert "alpha" in build_map(MapDeps(loop=loop), "tools")


# ---------------------------------------------------------------- 不泄密、不含正文


def test_模型段不含密钥():
    marker = "SECRET-MARKER-123"
    deps = MapDeps(models=lambda: [
        {"role": "主线", "backend": "deepseek", "model": "m1", "key_configured": True,
         "api_key": marker, "note": marker},
        {"role": "识图", "backend": "dashscope", "model": "m2", "key_configured": False,
         "token": marker},
    ])
    for scope in (None, "models"):
        out = build_map(deps, scope)
        assert marker not in out
    out = build_map(deps, "models")
    assert "主线：deepseek / m1，key 已配置" in out
    assert "识图：dashscope / m2，key 未配置" in out


def test_其余段不含夹带的正文():
    marker = "BODY-MARKER-456"
    deps = MapDeps(
        pending=lambda: {"orders": 2, "tasks": 1, "note": marker},
        health=lambda: {"ok": True, "model": "m", "background_stale": ["job-a"],
                        "store": marker, "attention": {"x": marker}, "sessions_cached": marker},
        os_pages=lambda: [{"id": "home", "title": "主页", "body": marker, "path": marker}],
    )
    for scope in (None, *SCOPES):
        assert marker not in build_map(deps, scope)
    assert "点单 2、任务 1" in build_map(deps, "pending")
    assert "停摆的后台任务：job-a" in build_map(deps, "health")
    assert "home：主页" in build_map(deps, "os")


def test_上下文段只写在场不编开关():
    reg = _Reg(mood=_P("sec", 5), time=_P())
    out = build_map(MapDeps(context_registry=reg), "context")
    assert "mood（section=sec, ttl=5）" in out
    assert "没有启用/关闭开关" in out
    assert "在场 2 个" in build_map(MapDeps(context_registry=reg))


# ---------------------------------------------------------------- 缺失与出错


def test_依赖缺失显示未接入():
    out = build_map(MapDeps())
    assert out.count("（未接入") >= 5


def test_依赖抛异常_该段取不到_其它段照常_且留痕(caplog):
    def boom():
        raise RuntimeError("炸了")

    deps = MapDeps(loop=_loop(_tool("a", module="tools.eryu")), models=boom,
                   pending=lambda: {"orders": 0, "tasks": 0})
    with caplog.at_level(logging.WARNING, logger="guide.world_map"):
        out = build_map(deps)
    assert "（取不到：RuntimeError）" in out
    assert "共听：1 个" in out and "点单 0、任务 0" in out
    assert any("models" in r.getMessage() or "模型" in r.getMessage() for r in caplog.records)
    assert any(r.exc_info for r in caplog.records), "warning 必须带 exc_info 留痕"


@pytest.mark.parametrize("bad", ["nope", 7, ["tools"]])
def test_不认识的scope不抛(bad):
    out = build_map(MapDeps(), bad)
    assert "不认识的范围" in out and "tools" in out


# ---------------------------------------------------------------- 长度


def test_两百个工具总览也不超长():
    loop = _loop(*[_tool(f"t{i}", "spend" if i % 3 == 0 else "none", f"fake.m{i}") for i in range(200)])
    deps = MapDeps(
        loop=loop,
        context_registry=_Reg(**{f"p{i}": _P() for i in range(60)}),
        models=lambda: [{"role": f"r{i}", "backend": "b", "model": "m", "key_configured": True} for i in range(60)],
        pending=lambda: {"orders": 1, "tasks": 1},
        health=lambda: {"ok": True, "model": "m", "background_stale": []},
        os_pages=lambda: [{"id": f"p{i}", "title": "t"} for i in range(60)],
    )
    out = build_map(deps)
    assert len(out) <= 1500
    assert "另有" in out or "已截断" in out
    # 工具再多，排在后面的短段也不许被整体截断吞掉
    for must in ("待她确认：点单 1、任务 1", "健康：正常", "桌面端页面（60 个）", "上下文来源（在场 60 个）", "模型线路："):
        assert must in out, f"{must!r} 被挤没了"
    assert len(build_map(deps, "tools")) <= 4000
