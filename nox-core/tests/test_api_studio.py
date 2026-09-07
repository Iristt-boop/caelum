"""Studio 两页的接口 —— 真走 HTTP。

tools 读 loop 里现成的 ToolSpec；integrations 在裸配置上必须
一排 not_configured，探活循环一个请求都不该发（空 targets）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api.server import create_app  # noqa: E402
from agent.llm import ToolSpec  # noqa: E402
from data.store import Store  # noqa: E402
from tests.test_api import FakeNox  # noqa: E402
from tests.test_api_world import _Ctx  # noqa: E402


class _Tool:
    def __init__(self, spec):
        self.spec = spec


class _Loop:
    """有 tools 也有 register（_build_attention 要往上面注册工具）。"""

    def __init__(self):
        self.tools = {
            "eryu_play": _Tool(ToolSpec("eryu_play", "放一首歌", {})),
            "search_food": _Tool(ToolSpec("search_food", "查食物热量", {})),
        }

    def register(self, spec, handler):
        self.tools[spec.name] = _Tool(spec)


def _client(monkeypatch, tmp_path, nox=None):
    monkeypatch.setenv("NOX_ATTENTION", "1")
    nox = nox or FakeNox()
    nox.cfg.db_path = str(tmp_path / "nox.db")
    nox.context = _Ctx()
    nox.bridge = None
    nox.current_session_id = None
    if not getattr(nox, "loop", None):
        nox.loop = _Loop()
    nox.router = type("R", (), {"light_adapter": None})()
    return TestClient(create_app(nox, Store(tmp_path / "sessions.db")))


def test_tools_lists_specs(monkeypatch, tmp_path):
    # ⚠️ FakeNox 自带的 _Loop.register 是 no-op、tools 里只有一个 None——
    # 测工具清单必须显式换上会真注册的 _Loop
    nox = FakeNox()
    nox.loop = _Loop()
    c = _client(monkeypatch, tmp_path, nox=nox)
    d = c.get("/api/nox/tools").json()
    assert d["ok"] is True
    # _build_attention 之后还会注册 record/remind 那几件，所以只断言"包含"
    names = [x["name"] for x in d["items"]]
    assert "eryu_play" in names and "search_food" in names
    mine = next(x for x in d["items"] if x["name"] == "eryu_play")
    assert mine["description"] == "放一首歌"


def test_integrations_bare_config_all_not_configured(monkeypatch, tmp_path):
    """裸配置：插口全列出、全 not_configured、一次网络都不碰。

    ⚠️ **不钉死条数**（2026-09-06 改）。原来写死 19，加一家 MCP 就红一次 ——
    而它想守的从来不是「正好 19 个」，是「**全部**列出来、状态全对」。
    钉死数字只会让每次加集成都顺手把它改大一格，那这条测试就没意义了。
    """
    c = _client(monkeypatch, tmp_path)
    d = c.get("/api/nox/integrations").json()
    assert d["ok"] is True and d["cached"] is False
    assert len(d["items"]) >= 19, "插口少了 —— 有集成没被列出来"
    # taobao 是特例：不靠 URL 配置（走 LocalLink 反向链路，工具恒注册），
    # 状态 = 她电脑的网关链路通不通——测试环境没有网关，如实是 down
    assert all(x["status"] == "not_configured" for x in d["items"] if x["name"] != "taobao")
    taobao = next(x for x in d["items"] if x["name"] == "taobao")
    assert taobao["status"] == "down"
    # 第二次吃缓存
    d2 = c.get("/api/nox/integrations").json()
    assert d2["cached"] is True


def test_integrations_reports_down_when_unreachable(monkeypatch, tmp_path):
    """配了但连不上 = down，不是 not_configured。探的是 127.0.0.1 死口。"""
    nox = FakeNox()
    # _Cfg 是每个实例新建的嵌套类，直接设类属性就是这个实例自己的
    nox.cfg.bridge_url = "http://127.0.0.1:9"   # 一定连不上
    nox.cfg.eryu_url = "http://127.0.0.1:9"
    c = _client(monkeypatch, tmp_path, nox=nox)
    d = c.get("/api/nox/integrations").json()
    by_name = {x["name"]: x for x in d["items"]}
    assert by_name["bridge"]["status"] == "down"
    assert by_name["eryu"]["status"] == "down"
    assert by_name["co-reading"]["status"] == "not_configured"
