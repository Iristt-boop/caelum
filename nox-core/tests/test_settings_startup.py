"""启动钩子：真的接进 `create_app`；失败 / 关掉 / 假 cfg 都不拖垮启动。"""

from __future__ import annotations

import dataclasses
import logging
from types import SimpleNamespace

from settings_store import startup
from settings_store.store import SettingsStore

from tests.settings_helpers import assert_no_markers, make_cfg, scan_bytes


def _core(monkeypatch, tmp_path):
    cfg = dataclasses.replace(make_cfg(monkeypatch), db_path=str(tmp_path / "nox.db"))
    return SimpleNamespace(cfg=cfg), tmp_path / "settings.db"


def test_启动钩子建库_灌种子_对账_并挂到core上(monkeypatch, tmp_path):
    core, db = _core(monkeypatch, tmp_path)
    startup.start_shadow(core)
    assert db.exists()
    assert isinstance(core.settings, SettingsStore)
    sm = core.settings.shadow_summary(7)
    assert sm["runs"] == 1 and sm["bad_runs"] == 0
    assert_no_markers(scan_bytes(db), "库文件")


def test_每次启动记一笔(monkeypatch, tmp_path):
    core, _ = _core(monkeypatch, tmp_path)
    startup.start_shadow(core)
    startup.start_shadow(core)
    assert core.settings.shadow_summary(7)["runs"] == 2


def test_开关关掉_不建库(monkeypatch, tmp_path):
    core, db = _core(monkeypatch, tmp_path)
    for off in ("off", "0", "false", "OFF"):
        monkeypatch.setenv("NOX_CONFIG_SHADOW", off)
        startup.start_shadow(core)
        assert not db.exists() and not hasattr(core, "settings")


def test_假cfg不是Config_跳过_不报错(monkeypatch, tmp_path):
    monkeypatch.delenv("NOX_CONFIG_SHADOW", raising=False)
    core = SimpleNamespace(cfg=SimpleNamespace(db_path=str(tmp_path / "nox.db")))
    startup.start_shadow(core)
    assert not (tmp_path / "settings.db").exists()


def test_任何异常都不抛_只留warning带堆栈(monkeypatch, tmp_path, caplog):
    core, _ = _core(monkeypatch, tmp_path)
    import settings_store.seed as seed_mod

    def boom(*a, **k):
        raise RuntimeError("灌库炸了")

    monkeypatch.setattr(seed_mod, "seed", boom)
    with caplog.at_level(logging.WARNING, logger="settings_store.startup"):
        startup.start_shadow(core)                      # 不许抛
    recs = [r for r in caplog.records if "配置影子失败" in r.getMessage()]
    assert recs and recs[0].exc_info, "必须留痕且带堆栈"
    assert not hasattr(core, "settings")


def test_库打不开也不拖垮启动(monkeypatch, tmp_path, caplog):
    core, db = _core(monkeypatch, tmp_path)
    db.mkdir()                                          # 同名目录：sqlite 打不开
    with caplog.at_level(logging.WARNING, logger="settings_store.startup"):
        startup.start_shadow(core)
    assert any("配置影子失败" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- 真的接进了 create_app（使用方断言）


def test_create_app启动路径真的跑了影子(monkeypatch, tmp_path):
    """不是单测里自己调 start_shadow：走真实的 create_app，断言库建出来、账本有记录。
    （教训：测试把接线这步假设掉了，测试全绿而线上什么都没发生。）"""
    from fastapi.testclient import TestClient

    from api.server import create_app
    from data.store import Store
    from tests.test_api import FakeNox
    from tests.test_api_world import _Ctx, _Loop

    monkeypatch.setenv("NOX_ATTENTION", "1")
    monkeypatch.delenv("NOX_CONFIG_SHADOW", raising=False)
    nox = FakeNox()
    nox.cfg = dataclasses.replace(make_cfg(monkeypatch), db_path=str(tmp_path / "nox.db"))
    nox.context = _Ctx()
    nox.bridge = None
    nox.current_session_id = None
    nox.loop = _Loop()
    nox.router = type("R", (), {"light_adapter": None})()
    TestClient(create_app(nox, Store(tmp_path / "sessions.db")))

    assert (tmp_path / "settings.db").exists(), "create_app 启动时没有跑配置影子"
    assert nox.settings.shadow_summary(7)["runs"] == 1
