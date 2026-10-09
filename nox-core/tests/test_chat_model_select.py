"""她在 Models 页选聊天模型（2026-10-09）。

🔴 起因：手机 Models 页点了 Haiku，Chat 页还显示 GLM —— 因为点选只写了本地的 `nox-chat-model`，
而 Chat 页 09-07 起就**不读也不发**这个值（那次的教训：手机 localStorage 里的陈值盖掉服务端，
显示错和行为错是同一个原因）。所以选择必须住在**服务器上**，和「思考」开关同一个形状：
手机、OS 看到同一份，重启不丢，选了下一句就生效。
"""

from __future__ import annotations

import types

from fastapi.testclient import TestClient

from attention.store import AttentionStore
from nox import Nox
from tests.test_api_models import ModelFakeNox, _client


def _nox(tmp_path, with_cfg=True) -> Nox:
    n = Nox.__new__(Nox)
    n.state_store = AttentionStore(tmp_path / "attn.db")
    if with_cfg:
        from config import ModelChoice
        n.cfg = types.SimpleNamespace(
            primary=types.SimpleNamespace(model="glm-5.3-flash", max_tokens=100),
            models={
                "glm-5.3-flash": ModelChoice("glm-5.3-flash", "zhipu", "GLM 5.3 Flash"),
                "haiku-5-5": ModelChoice("anthropic/claude-haiku-5.5", "openrouter", "Haiku 5.5"),
            },
        )
        n._adapters = {}
    return n


def test_没选过是None_选了就记住_重启也在(tmp_path):
    n = _nox(tmp_path)
    assert n.chat_model_key() is None
    assert n.set_chat_model("haiku-5-5") is True
    assert n.chat_model_key() == "haiku-5-5"
    n2 = _nox(tmp_path)
    assert n2.chat_model_key() == "haiku-5-5", "重启（新的 Nox、同一个状态库）之后要还记得"


def test_清空就回到默认(tmp_path):
    n = _nox(tmp_path)
    n.set_chat_model("haiku-5-5")
    assert n.set_chat_model("") is True
    assert n.chat_model_key() is None


def test_存的名字后来不在清单里_退回默认_不炸(tmp_path):
    n = _nox(tmp_path)
    n.set_chat_model("haiku-5-5")
    del n.cfg.models["haiku-5-5"]
    assert n.chat_model_key() is None


def test_状态库没起来_按没选算_也不假装存上了():
    n = Nox.__new__(Nox)
    assert n.chat_model_key() is None
    assert n.set_chat_model("haiku-5-5") is False


def test_请求没带模型时_用她选的(tmp_path):
    """🔴 Chat 页不发 model，所以真正起作用的就是这一条：model=None 也要落到她选的那个。"""
    n = _nox(tmp_path)
    built = []
    import nox as nox_mod
    orig_build, orig_make = nox_mod._build_llm, nox_mod.make_adapter
    nox_mod._build_llm = lambda **kw: built.append(kw) or types.SimpleNamespace(usable=True, **kw)
    nox_mod.make_adapter = lambda cfg: ("ADAPTER", cfg.model)
    try:
        assert n.adapter_for(None) is None, "没选过 → 默认主模型"
        assert n.model_name(None) == "glm-5.3-flash"
        n.set_chat_model("haiku-5-5")
        assert n.adapter_for(None) == ("ADAPTER", "anthropic/claude-haiku-5.5")
        assert built[-1]["backend"] == "openrouter"
        assert n.model_name(None) == "anthropic/claude-haiku-5.5", "用量记账也要记成它，不然 Usage 页算错钱"
        #: 请求里明确指定了的，还是以请求为准
        assert n.model_name("glm-5.3-flash") == "glm-5.3-flash"
    finally:
        nox_mod._build_llm, nox_mod.make_adapter = orig_build, orig_make


def _client_with_switch(monkeypatch, tmp_path) -> TestClient:
    nox = ModelFakeNox()
    nox.state_store = AttentionStore(tmp_path / "attn.db")
    for name in ("chat_model_key", "set_chat_model", "_state_store"):
        setattr(nox, name, types.MethodType(getattr(Nox, name), nox))
    return _client(monkeypatch, tmp_path, nox)


def test_接口_选了_models_页也看得到(monkeypatch, tmp_path):
    c = _client_with_switch(monkeypatch, tmp_path)
    assert c.get("/api/nox/model").json() == {"ok": True, "key": ""}
    assert c.get("/api/nox/models").json()["selected"] is None
    assert c.post("/api/nox/model", json={"key": "sonnet-5"}).json() == {"ok": True, "key": "sonnet-5"}
    assert c.get("/api/nox/models").json()["selected"] == "sonnet-5"
    assert c.post("/api/nox/model", json={"key": ""}).json() == {"ok": True, "key": ""}


def test_接口_清单里没有的名字是400_不存(monkeypatch, tmp_path):
    c = _client_with_switch(monkeypatch, tmp_path)
    r = c.post("/api/nox/model", json={"key": "gpt-9"})
    assert r.status_code == 400
    assert c.get("/api/nox/model").json()["key"] == ""


def test_接口_存不上是503_不回ok(monkeypatch, tmp_path):
    """存的动作失败（库挂了）要原样告诉前端；库没起来的单元测试在上面。"""
    nox = ModelFakeNox()
    for name in ("chat_model_key", "_state_store"):
        setattr(nox, name, types.MethodType(getattr(Nox, name), nox))
    nox.set_chat_model = lambda key: False
    c = _client(monkeypatch, tmp_path, nox)
    r = c.post("/api/nox/model", json={"key": "sonnet-5"})
    assert r.status_code == 503
