"""Models 设置页的接口 —— 真走 HTTP。

`/api/nox/models` 必须防「假 core 缺字段」：FakeNox 的 _Cfg 只有
primary.model，getattr 兜底少一行，这页就把整个 Settings 拖成 500
—— 2026-08-31 共读接线时 `_Cfg 没有 reading_url` 那一课的续集。
（usage-stats 是 bridge 的 Node 接口，不在这测。）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api.server import create_app  # noqa: E402
from config import ModelChoice  # noqa: E402
from data.store import Store  # noqa: E402
from tests.test_api import FakeNox  # noqa: E402
from tests.test_api_world import _Ctx, _Loop  # noqa: E402


class ModelFakeNox(FakeNox):
    """带上 Models 页需要的真实形状。"""

    def __init__(self):
        super().__init__()
        # primary 是 _Cfg 的嵌套类；backend 是从 base_url 反推的（LLMConfig
        # 里根本没有 backend 字段），所以这里给的是地址
        self.cfg.primary.base_url = "https://api.deepseek.com/v1"
        self.cfg.utility = type(
            "U", (), {"base_url": "https://api.deepseek.com/v1",
                      "model": "deepseek-v4-flash"})()
        self.cfg.models = {
            "v4-flash": ModelChoice("deepseek-v4-flash", "deepseek", "DeepSeek V4 Flash"),
            "sonnet-5": ModelChoice("anthropic/claude-sonnet-5", "openrouter", "Sonnet 5"),
        }


def _client(monkeypatch, tmp_path, nox=None):
    monkeypatch.setenv("NOX_ATTENTION", "1")
    nox = nox or ModelFakeNox()
    nox.cfg.db_path = str(tmp_path / "nox.db")
    nox.context = _Ctx()
    nox.bridge = None
    nox.current_session_id = None
    nox.loop = _Loop()
    nox.router = type("R", (), {"light_adapter": None})()
    return TestClient(create_app(nox, Store(tmp_path / "sessions.db")))


def test_models_lists_choices_and_current(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    r = c.get("/api/nox/models")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["current"]["model"] == "fake-model"
    assert d["current"]["backend"] == "deepseek"
    assert [x["key"] for x in d["choices"]] == ["v4-flash", "sonnet-5"]
    assert d["choices"][0]["label"] == "DeepSeek V4 Flash"
    assert set(d["providers"]) == {"deepseek", "openrouter"}
    assert isinstance(d["providers"]["deepseek"]["configured"], bool)
    assert d["utility"]["model"] == "deepseek-v4-flash"


def test_models_survives_a_bare_fake_core(monkeypatch, tmp_path):
    """老形状的假 core（没有 models / utility / backend）也不许 500。"""
    c = _client(monkeypatch, tmp_path, nox=FakeNox())
    d = c.get("/api/nox/models").json()
    assert d["ok"] is True
    assert d["choices"] == []
    assert d["current"]["backend"] == ""
