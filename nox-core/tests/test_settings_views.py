"""配置层 P1 的只读视图：厂商 / 槽位 / 接口 / caelum_map 的模型段。

核心要证明三件事：
  ① 读得对（与 P0 灌进去的、也与现状一致）；
  ② **不泄密**（key 的值、地址里夹带的 key、params 里夹带的字段，一个字节都不出去）；
  ③ **读不到和没有分开**（库没起来 = 503；槽位指向的东西丢了 = ok:false 带原因；没有行 = 空列表）。
"""

from __future__ import annotations

import dataclasses
import json
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent.loop import AgentLoop
from config import BACKENDS
from guide.wiring import make_deps
from guide.world_map import build_map
from settings_store.seed import seed
from settings_store.store import SettingsStore
from settings_store.views import providers_view, safe_url, slots_view

from tests.settings_helpers import MARKERS, assert_no_markers, make_cfg


def _store(tmp_path, monkeypatch, **env):
    cfg = make_cfg(monkeypatch, **env)
    s = SettingsStore(tmp_path / "settings.db")
    seed(s, cfg)
    return cfg, s


# ---------------------------------------------------------------- safe_url


@pytest.mark.parametrize("raw, want", [
    ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1"),
    ("https://open.bigmodel.cn/api/paas/v4", "https://open.bigmodel.cn/api/paas/v4"),
    ("http://127.0.0.1:8100/x", "http://127.0.0.1:8100/x"),
    ("https://user:KEY-MARK-DEEPSEEK-111@host.invalid/v1", "https://host.invalid/v1"),
    ("https://host.invalid/v1?key=KEY-MARK-ZHIPU-333&x=1", "https://host.invalid/v1"),
    ("https://host.invalid/v1#KEY-MARK-OPENROUTER-222", "https://host.invalid/v1"),
    ("", ""), (None, ""),
])
def test_safe_url(raw, want):
    assert safe_url(raw) == want
    assert_no_markers(safe_url(raw), "safe_url")


def test_safe_url解析不了就返回空串():
    assert safe_url("http://[::1") == ""


# ---------------------------------------------------------------- providers_view


def test_厂商视图_字段与数量(tmp_path, monkeypatch):
    cfg, s = _store(tmp_path, monkeypatch)
    v = providers_view(s)
    assert [p["id"] for p in v["providers"]] == sorted(BACKENDS)
    by = {p["id"]: p for p in v["providers"]}
    assert by["deepseek"]["protocol"] == "openai_compat"
    assert by["deepseek"]["base_url"] == BACKENDS["deepseek"].base_url
    assert by["deepseek"]["cache_style"] == "auto_prefix"
    assert by["openrouter"]["cache_style"] == "passthrough"
    assert by["anthropic"]["cache_style"] == "explicit_breakpoint"
    assert by["deepseek"]["source"] == "seed"
    assert by["deepseek"]["key_envs"] == ["DEEPSEEK_API_KEY"]            # 只有名字
    assert sum(p["models"] for p in v["providers"]) == len(s.models())
    assert v["shadow"]["runs"] == 0


def test_key_configured只说配没配(tmp_path, monkeypatch):
    cfg, s = _store(tmp_path, monkeypatch)
    all_set = {p["id"]: p["key_configured"] for p in providers_view(s)["providers"]}
    assert all(all_set.values())
    only_ds = providers_view(s, env={"DEEPSEEK_API_KEY": "x"})["providers"]
    assert {p["id"]: p["key_configured"] for p in only_ds} == {
        "anthropic": False, "dashscope": False, "deepseek": True, "openrouter": False, "zhipu": False}
    blank = providers_view(s, env={"DEEPSEEK_API_KEY": "   "})["providers"]
    assert not any(p["key_configured"] for p in blank)               # 全空白 = 没配


def test_厂商视图不含任何密钥(tmp_path, monkeypatch):
    cfg, s = _store(tmp_path, monkeypatch)
    # 地址里被人塞了 key / 账号密码，视图也不许带出去
    with s.transaction() as c:
        c.execute("UPDATE providers SET base_url='https://u:KEY-MARK-DEEPSEEK-111@h.invalid/v1?"
                  "key=KEY-MARK-ZHIPU-333#KEY-MARK-OPENROUTER-222' WHERE id='deepseek'")
    v = providers_view(s)
    assert_no_markers(json.dumps(v, ensure_ascii=False), "厂商视图")
    assert {p["id"]: p for p in v["providers"]}["deepseek"]["base_url"] == "https://h.invalid/v1"


def test_影子摘要出现在厂商视图里(tmp_path, monkeypatch):
    cfg, s = _store(tmp_path, monkeypatch)
    s.log_shadow(ok=True, n_checked=91, n_diff=0, seed_added=1, detail=[])
    s.log_shadow(ok=False, n_checked=91, n_diff=2, seed_added=0, detail=[])
    sh = providers_view(s)["shadow"]
    assert (sh["runs"], sh["bad_runs"], sh["diffs"]) == (2, 1, 2)


# ---------------------------------------------------------------- slots_view


def test_槽位视图_顺序与内容(tmp_path, monkeypatch):
    cfg, s = _store(tmp_path, monkeypatch)
    items = slots_view(s)["slots"]
    assert [i["slot"] for i in items] == ["chat.primary", "chat.utility", "vision"]
    p = items[0]
    assert p["ok"] and p["provider"]["id"] == "deepseek" and p["model"]["name"] == "deepseek-flash"
    assert p["max_tokens"] == cfg.primary.max_tokens
    assert p["key_configured"] is True and p["updated_by"] == "seed"
    assert items[1]["provider"]["id"] == "zhipu"
    assert "vision" in items[2]["model"]["capabilities"]


def test_槽位视图_key覆盖项生效(tmp_path, monkeypatch):
    cfg, s = _store(tmp_path, monkeypatch, NOX_UTILITY_API_KEY="KEY-MARK-UTILITY-OVERRIDE")
    util = slots_view(s, env={"NOX_UTILITY_API_KEY": "x"})["slots"][1]
    assert util["key_configured"] is True                    # 取的是覆盖用的那个环境变量
    util = slots_view(s, env={"ZHIPU_API_KEY": "x"})["slots"][1]
    assert util["key_configured"] is False                   # 厂商默认的 key 在也不算：这个槽位用的是覆盖项


def test_槽位params只放白名单字段(tmp_path, monkeypatch):
    cfg, s = _store(tmp_path, monkeypatch)
    t = s.slot("chat.primary")["targets"]
    t[0]["params"].update({"secret_note": "KEY-MARK-DEEPSEEK-111", "api_key": "KEY-MARK-ZHIPU-333"})
    with s.transaction() as c:
        c.execute("UPDATE slots SET targets=? WHERE slot='chat.primary'", (json.dumps(t),))
    v = slots_view(s)
    assert_no_markers(json.dumps(v, ensure_ascii=False), "槽位视图")
    assert "secret_note" not in json.dumps(v)


def test_读不到和没有分开(tmp_path, monkeypatch):
    cfg, s = _store(tmp_path, monkeypatch)
    # 没有：库里一个槽位都没有 → 空列表，不是错误
    with s.transaction() as c:
        c.execute("DELETE FROM slots")
    assert slots_view(s) == {"slots": []}


def test_槽位指向的东西丢了_如实报坏(tmp_path, monkeypatch):
    cfg, s = _store(tmp_path, monkeypatch)
    with s.transaction() as c:
        c.execute("UPDATE slots SET targets='[{\"model_id\": 99999, \"params\": {}}]' WHERE slot='vision'")
        c.execute("UPDATE slots SET targets='[]' WHERE slot='chat.utility'")
    items = {i["slot"]: i for i in slots_view(s)["slots"]}
    assert items["vision"]["ok"] is False and "型号" in items["vision"]["error"]
    assert items["chat.utility"]["ok"] is False and "目标" in items["chat.utility"]["error"]
    assert items["chat.primary"]["ok"] is True                # 坏一个不拖累别的


def test_将来的槽位排在后面(tmp_path, monkeypatch):
    cfg, s = _store(tmp_path, monkeypatch)
    row = s.slot("chat.primary")
    with s.transaction() as c:
        c.execute("INSERT INTO slots(slot,targets,updated_at,updated_by) VALUES('tts',?,'t','she')",
                  (json.dumps(row["targets"]),))
    assert [i["slot"] for i in slots_view(s)["slots"]] == ["chat.primary", "chat.utility", "vision", "tts"]


# ---------------------------------------------------------------- 接口（真走 create_app）


def _client(monkeypatch, tmp_path, *, real_cfg=True):
    from api.server import create_app
    from data.store import Store
    from tests.test_api import FakeNox
    from tests.test_api_world import _Ctx, _Loop

    monkeypatch.setenv("NOX_ATTENTION", "1")
    monkeypatch.delenv("NOX_CONFIG_SHADOW", raising=False)
    nox = FakeNox()
    if real_cfg:
        nox.cfg = dataclasses.replace(make_cfg(monkeypatch), db_path=str(tmp_path / "nox.db"))
    else:
        nox.cfg.db_path = str(tmp_path / "nox.db")
    nox.context = _Ctx()
    nox.bridge = None
    nox.current_session_id = None
    nox.loop = _Loop()
    nox.router = type("R", (), {"light_adapter": None})()
    return TestClient(create_app(nox, Store(tmp_path / "sessions.db"))), nox


def test_接口返回真实视图_且不含密钥(monkeypatch, tmp_path):
    c, _ = _client(monkeypatch, tmp_path)
    r = c.get("/api/nox/config/providers")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and {p["id"] for p in d["providers"]} == set(BACKENDS)
    assert d["shadow"]["runs"] == 1 and d["shadow"]["bad_runs"] == 0       # 启动时跑过一次影子
    assert_no_markers(r.text, "/config/providers 响应")

    r = c.get("/api/nox/config/slots")
    assert r.status_code == 200
    s = r.json()
    assert s["ok"] is True and [i["slot"] for i in s["slots"]] == ["chat.primary", "chat.utility", "vision"]
    assert_no_markers(r.text, "/config/slots 响应")


def test_库没起来是503_不是空列表(monkeypatch, tmp_path):
    """FakeNox 的 cfg 不是真 Config，影子被跳过 → core.settings 不存在。「读不到」≠「没有」。"""
    c, _ = _client(monkeypatch, tmp_path, real_cfg=False)
    for path in ("/api/nox/config/providers", "/api/nox/config/slots"):
        r = c.get(path)
        assert r.status_code == 503
        assert "配置库没起来" in r.json()["detail"]


def test_开关关掉也是503(monkeypatch, tmp_path):
    from api.server import create_app
    from data.store import Store
    from tests.test_api import FakeNox
    from tests.test_api_world import _Ctx, _Loop

    monkeypatch.setenv("NOX_ATTENTION", "1")
    monkeypatch.setenv("NOX_CONFIG_SHADOW", "off")
    nox = FakeNox()
    nox.cfg = dataclasses.replace(make_cfg(monkeypatch), db_path=str(tmp_path / "nox.db"))
    nox.context, nox.bridge, nox.current_session_id = _Ctx(), None, None
    nox.loop = _Loop()
    nox.router = type("R", (), {"light_adapter": None})()
    c = TestClient(create_app(nox, Store(tmp_path / "sessions.db")))
    assert c.get("/api/nox/config/providers").status_code == 503


# ---------------------------------------------------------------- caelum_map 的模型段读库


def _nox_with_store(tmp_path, monkeypatch, **env):
    cfg, s = _store(tmp_path, monkeypatch, **env)
    return SimpleNamespace(cfg=cfg, loop=AgentLoop(adapter=None), context=None,
                           orders=None, tasks=None, settings=s), s


def test_地图的模型段读的是库_不是cfg(tmp_path, monkeypatch):
    nox, s = _nox_with_store(tmp_path, monkeypatch)
    # 只改库、不改 cfg：地图要跟着库走，才说明它真的在读库
    with s.transaction() as c:
        c.execute("UPDATE models SET model_name='deepseek-from-db' WHERE short_name='v4-flash'")
    out = build_map(make_deps(nox), "models")
    assert "deepseek-from-db" in out and "deepseek-flash" not in out


def test_地图的模型段与cfg一致(tmp_path, monkeypatch):
    """P1 里库与现状是一致的（P0 影子盯着）：读库和读 cfg 给出同样的话。"""
    nox, s = _nox_with_store(tmp_path, monkeypatch)
    from_store = build_map(make_deps(nox), "models")
    nox.settings = None
    assert build_map(make_deps(nox), "models") == from_store


def test_地图读库失败退回cfg_且留痕(tmp_path, monkeypatch, caplog):
    nox, s = _nox_with_store(tmp_path, monkeypatch)
    import settings_store.views as views

    def boom(*a, **k):
        raise RuntimeError("库炸了")

    monkeypatch.setattr(views, "slots_view", boom)
    with caplog.at_level(logging.WARNING, logger="guide.wiring"):
        out = build_map(make_deps(nox), "models")
    assert "deepseek-flash" in out                             # 退回读 cfg，不是空
    assert any("地图读配置库失败" in r.getMessage() and r.exc_info for r in caplog.records)


def test_地图里坏槽位如实说_不当它不存在(tmp_path, monkeypatch):
    nox, s = _nox_with_store(tmp_path, monkeypatch)
    with s.transaction() as c:
        c.execute("UPDATE slots SET targets='[]' WHERE slot='vision'")
    out = build_map(make_deps(nox), "models")
    assert "识图" in out and "读不到" in out


def test_地图的模型段不含密钥(tmp_path, monkeypatch):
    nox, s = _nox_with_store(tmp_path, monkeypatch)
    for scope in (None, "models"):
        assert_no_markers(build_map(make_deps(nox), scope), "地图")
