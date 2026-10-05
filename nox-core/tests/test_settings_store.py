"""settings.db 的表结构与存储类：结构、事务、约束，以及「库里不许有叫 key 的列」。"""

from __future__ import annotations

import sqlite3

import pytest

from settings_store.schema import ALLOWED_COLUMNS, FORBIDDEN_COLUMN_HINTS
from settings_store.store import SettingsStore


def _tables(store: SettingsStore) -> list[str]:
    rows = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return sorted(r["name"] for r in rows)


def test_建出设计稿里的全部表(tmp_path):
    s = SettingsStore(tmp_path / "settings.db")
    assert _tables(s) == sorted(
        ["meta", "providers", "models", "slots", "slot_history", "test_results", "shadow_log"])


def test_重复打开幂等(tmp_path):
    p = tmp_path / "settings.db"
    s = SettingsStore(p)
    with s.transaction() as c:
        c.execute("INSERT INTO meta(k,v) VALUES('x','1')")
    s.close()
    s2 = SettingsStore(p)
    assert s2._conn.execute("SELECT v FROM meta WHERE k='x'").fetchone()["v"] == "1"


def test_WAL模式(tmp_path):
    s = SettingsStore(tmp_path / "settings.db")
    assert s._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_任何表都不许有叫key或token的列(tmp_path):
    """🔴 这个库里不存密钥。providers 只存环境变量**名字**（key_envs）。"""
    s = SettingsStore(tmp_path / "settings.db")
    bad = []
    for t in _tables(s):
        for col in s._conn.execute(f"PRAGMA table_info({t})"):
            name = col["name"].lower()
            if name in ALLOWED_COLUMNS:
                continue
            if any(h in name for h in FORBIDDEN_COLUMN_HINTS):
                bad.append(f"{t}.{col['name']}")
    assert not bad, f"这些列看起来会存密钥：{bad}"


def test_外键生效_型号必须挂在存在的厂商下(tmp_path):
    s = SettingsStore(tmp_path / "settings.db")
    with pytest.raises(sqlite3.IntegrityError):
        with s.transaction() as c:
            c.execute(
                "INSERT INTO models(provider_id,model_name,origin,created_at,updated_at)"
                " VALUES('nope','m','seed','t','t')")


def test_同厂商同型号不能重复_但多个空短名可以并存(tmp_path):
    s = SettingsStore(tmp_path / "settings.db")
    with s.transaction() as c:
        c.execute("INSERT INTO providers(id,label,protocol,base_url,cache_style,source,created_at,updated_at)"
                  " VALUES('p','P','openai_compat','u','none','seed','t','t')")
        for name in ("a", "b"):          # short_name 都是 NULL：UNIQUE 不能把它们当成重复
            c.execute("INSERT INTO models(provider_id,model_name,origin,created_at,updated_at)"
                      " VALUES('p',?,'seed','t','t')", (name,))
    with pytest.raises(sqlite3.IntegrityError):
        with s.transaction() as c:
            c.execute("INSERT INTO models(provider_id,model_name,origin,created_at,updated_at)"
                      " VALUES('p','a','seed','t','t')")


def test_事务出错整体回滚(tmp_path):
    s = SettingsStore(tmp_path / "settings.db")
    with s.transaction() as c:
        c.execute("INSERT INTO meta(k,v) VALUES('keep','1')")
    with pytest.raises(RuntimeError):
        with s.transaction() as c:
            c.execute("INSERT INTO meta(k,v) VALUES('half','1')")
            raise RuntimeError("中途炸了")
    keys = {r["k"] for r in s._conn.execute("SELECT k FROM meta")}
    assert "keep" in keys and "half" not in keys


def test_影子账本汇总(tmp_path):
    s = SettingsStore(tmp_path / "settings.db")
    s.log_shadow(ok=True, n_checked=10, n_diff=0, seed_added=1, detail=[])
    s.log_shadow(ok=False, n_checked=10, n_diff=2, seed_added=0, detail=[{"path": "x"}])
    sm = s.shadow_summary(7)
    assert (sm["runs"], sm["bad_runs"], sm["diffs"]) == (2, 1, 2)
    assert s.shadow_summary(0)["runs"] in (0, 2)    # 窗口为 0 天：边界不炸
