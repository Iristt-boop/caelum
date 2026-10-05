"""影子对比：从库里重建配置，与现状逐字段比。

这是 P0 的验收口径，所以要证明两件事：
  ① 对得上的时候真的报「零不一致」；
  ② **对不上的时候真的会报** —— 红不了的检查不算检查（每一类不一致各改坏一次）。
外加：密钥不出现在任何输出里（日志 / 返回值 / shadow_log / 库文件）。
"""

from __future__ import annotations

import dataclasses
import logging

from config import ModelChoice
from settings_store import shadow
from settings_store.seed import seed
from settings_store.store import SettingsStore

from tests.settings_helpers import MARKERS, assert_no_markers, make_cfg, scan_bytes


def _ready(tmp_path, monkeypatch, **env):
    cfg = make_cfg(monkeypatch, **env)
    s = SettingsStore(tmp_path / "settings.db")
    rep = seed(s, cfg)
    return cfg, s, rep


def _paths(res) -> list[str]:
    return [d.path for d in res.diffs]


def test_默认配置零不一致(tmp_path, monkeypatch):
    cfg, s, _ = _ready(tmp_path, monkeypatch)
    res = shadow.compare(s, cfg)
    assert res.ok and res.diffs == []
    assert res.n_checked > 50           # 不是空跑：厂商 + 型号 + 三个槽位 × 七个字段


def test_自定义地址与覆盖项也零不一致(tmp_path, monkeypatch):
    cfg, s, _ = _ready(
        tmp_path, monkeypatch,
        NOX_UTILITY_PROVIDER="openai_compat", NOX_UTILITY_BASE_URL="https://relay.example.invalid/v1",
        NOX_UTILITY_API_KEY="KEY-MARK-UTILITY-OVERRIDE", NOX_PRIMARY_MODEL="some/brand-new-model")
    assert shadow.compare(s, cfg).diffs == []


def test_重建出的LLMConfig与现状每个字段相同(tmp_path, monkeypatch):
    cfg, s, _ = _ready(tmp_path, monkeypatch)
    for slot, attr in (("chat.primary", "primary"), ("chat.utility", "utility"), ("vision", "vision")):
        rebuilt = shadow.rebuild_llm(s, slot, __import__("os").environ)
        assert dataclasses.asdict(rebuilt) == dataclasses.asdict(getattr(cfg, attr))


# ---------------------------------------------------------------- 红得起来：每一类不一致各改坏一次


def test_库里地址被改_会报(tmp_path, monkeypatch):
    cfg, s, _ = _ready(tmp_path, monkeypatch)
    with s.transaction() as c:
        c.execute("UPDATE providers SET base_url='https://evil.invalid/v1' WHERE id='deepseek'")
    res = shadow.compare(s, cfg)
    assert "providers.deepseek.base_url" in _paths(res)
    assert "slots.chat.primary.base_url" in _paths(res)        # 重建出的槽位也跟着不一致
    assert not res.ok


def test_库里key来源被改_会报_且只报摘要(tmp_path, monkeypatch):
    cfg, s, _ = _ready(tmp_path, monkeypatch)
    with s.transaction() as c:
        c.execute("UPDATE providers SET key_envs='[\"OPENROUTER_API_KEY\"]' WHERE id='deepseek'")
    res = shadow.compare(s, cfg)
    d = next(x for x in res.diffs if x.path == "slots.chat.primary.api_key")
    assert d.live.startswith("sha256:") and d.store.startswith("sha256:") and d.live != d.store
    assert_no_markers(repr(res.diffs), "不一致报告")


def test_环境里的key换了不算不一致_因为库里只存名字(tmp_path, monkeypatch):
    cfg, s, _ = _ready(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "KEY-MARK-DEEPSEEK-CHANGED-999")
    cfg2 = make_cfg(monkeypatch, DEEPSEEK_API_KEY="KEY-MARK-DEEPSEEK-CHANGED-999")
    # 库只存名字，重建时按名字去读环境 → 读到的是新值，与新的现状一致；
    # 所以「环境变量换了 key」不构成不一致 —— 这正是「库里不存 key」的设计结果
    assert shadow.compare(s, cfg2).diffs == []


def test_现状型号变了而库没跟上_会报(tmp_path, monkeypatch):
    cfg, s, _ = _ready(tmp_path, monkeypatch)
    live = dict(cfg.models)
    live["v4-flash"] = ModelChoice("deepseek-v9-future", "deepseek", "未来")
    cfg2 = dataclasses.replace(cfg, models=live)
    res = shadow.compare(s, cfg2)
    assert "models.v4-flash.model" in _paths(res)


def test_库里缺一个型号_会报(tmp_path, monkeypatch):
    cfg, s, _ = _ready(tmp_path, monkeypatch)
    with s.transaction() as c:
        # 先摘掉引用它的槽位，再删型号（外键）
        c.execute("DELETE FROM models WHERE short_name='glm-4.6'")
    res = shadow.compare(s, cfg)
    assert "models.glm-4.6" in _paths(res)


def test_库里多出种子行_会报(tmp_path, monkeypatch):
    cfg, s, _ = _ready(tmp_path, monkeypatch)
    with s.transaction() as c:
        c.execute("INSERT INTO providers(id,label,protocol,base_url,cache_style,source,created_at,updated_at)"
                  " VALUES('ghost','幽灵','openai_compat','u','none','seed','t','t')")
        c.execute("INSERT INTO models(provider_id,model_name,short_name,origin,created_at,updated_at)"
                  " VALUES('deepseek','ghost-model','ghost','seed','t','t')")
    res = shadow.compare(s, cfg)
    assert "providers.多出的种子行" in _paths(res) and "models.多出的短名" in _paths(res)


def test_槽位的目标型号丢了_会报重建不出来(tmp_path, monkeypatch):
    cfg, s, _ = _ready(tmp_path, monkeypatch)
    with s.transaction() as c:
        c.execute("UPDATE slots SET targets='[{\"model_id\": 99999, \"params\": {}}]' WHERE slot='vision'")
    assert "slots.vision" in _paths(shadow.compare(s, cfg))


def test_库槽位参数被改_会报(tmp_path, monkeypatch):
    cfg, s, _ = _ready(tmp_path, monkeypatch)
    import json
    t = s.slot("chat.utility")["targets"]
    t[0]["params"]["max_tokens"] = 1
    with s.transaction() as c:
        c.execute("UPDATE slots SET targets=? WHERE slot='chat.utility'", (json.dumps(t),))
    assert "slots.chat.utility.max_tokens" in _paths(shadow.compare(s, cfg))


def test_价格被改_会报(tmp_path, monkeypatch):
    cfg, s, _ = _ready(tmp_path, monkeypatch)
    with s.transaction() as c:
        c.execute("UPDATE models SET price_json='{\"in\": 999}' WHERE short_name='glm-5.3'")
    assert "models.glm-5.3.price" in _paths(shadow.compare(s, cfg))


# ---------------------------------------------------------------- 账本与日志


def test_run写账本且日志说一致(tmp_path, monkeypatch, caplog):
    cfg, s, rep = _ready(tmp_path, monkeypatch)
    with caplog.at_level(logging.INFO, logger="settings_store.shadow"):
        res = shadow.run(s, cfg, rep)
    assert res.ok
    assert any("逐字段一致" in r.getMessage() for r in caplog.records)
    sm = s.shadow_summary(7)
    assert (sm["runs"], sm["bad_runs"], sm["diffs"]) == (1, 0, 0)


def test_run遇到不一致_打warning_记账_且仍然不抛(tmp_path, monkeypatch, caplog):
    cfg, s, rep = _ready(tmp_path, monkeypatch)
    with s.transaction() as c:
        c.execute("UPDATE providers SET base_url='https://evil.invalid/v1' WHERE id='deepseek'")
    with caplog.at_level(logging.WARNING, logger="settings_store.shadow"):
        res = shadow.run(s, cfg, rep)
    assert not res.ok
    assert any("配置影子不一致" in r.getMessage() for r in caplog.records)
    sm = s.shadow_summary(7)
    assert sm["bad_runs"] == 1 and sm["diffs"] == len(res.diffs)


def test_任何输出里都没有密钥(tmp_path, monkeypatch, caplog):
    """日志、返回值、shadow_log、整个库文件：已知标记串一个都不许出现。"""
    cfg, s, rep = _ready(tmp_path, monkeypatch)
    with s.transaction() as c:                                    # 制造一个会报 api_key 摘要的不一致
        c.execute("UPDATE providers SET key_envs='[\"OPENROUTER_API_KEY\"]' WHERE id='deepseek'")
    with caplog.at_level(logging.DEBUG):
        res = shadow.run(s, cfg, rep)
    assert not res.ok
    assert_no_markers("\n".join(r.getMessage() for r in caplog.records), "日志")
    assert_no_markers(repr(res), "返回值")
    log_detail = " ".join(r["detail"] for r in s._conn.execute("SELECT detail FROM shadow_log"))
    assert_no_markers(log_detail, "shadow_log")
    s.close()
    assert_no_markers(scan_bytes(tmp_path / "settings.db"), "库文件")
    for mark in MARKERS.values():
        assert mark not in repr(res.diffs)


def test_digest不可逆且区分空与非空():
    assert shadow.digest("") == "(空)" and shadow.digest(None) == "(空)"
    d = shadow.digest("KEY-MARK-DEEPSEEK-111")
    assert d.startswith("sha256:") and "KEY" not in d and len(d) == len("sha256:") + 8
    assert shadow.digest("a") != shadow.digest("b")
