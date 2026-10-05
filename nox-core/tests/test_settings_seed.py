"""种子：灌进去的东西与今天写死的配置一一对得上；不泄密；幂等；不碰用户行。"""

from __future__ import annotations

import json

from agent.adapters import AnthropicAdapter, OpenAICompatAdapter, supports_vision
from config import BACKENDS, LLMConfig
from settings_store.seed import resolve_key, seed, seed_cache_style
from settings_store.store import SettingsStore

from tests.settings_helpers import MARKERS, assert_no_markers, make_cfg, scan_bytes


def _store(tmp_path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.db")


def test_厂商就是BACKENDS(tmp_path, monkeypatch):
    cfg = make_cfg(monkeypatch)
    s = _store(tmp_path)
    seed(s, cfg)
    rows = {p["id"]: p for p in s.providers()}
    assert set(rows) == set(BACKENDS)
    for name, b in BACKENDS.items():
        assert rows[name]["protocol"] == b.provider
        assert rows[name]["base_url"] == b.base_url
        assert rows[name]["key_envs"] == list(b.key_envs)       # 只存环境变量**名字**
        assert rows[name]["source"] == "seed"


def test_cache_style与真adapter的行为对得上(tmp_path, monkeypatch):
    """使用方断言：种子填的 cache_style，必须等于今天 adapter 实际打不打断点。"""
    cfg = make_cfg(monkeypatch)
    s = _store(tmp_path)
    seed(s, cfg)
    for p in s.providers():
        llm = LLMConfig(provider=p["protocol"], model="m", api_key="k", base_url=p["base_url"])
        if p["protocol"] == "anthropic":
            AnthropicAdapter(llm)                                 # 能建；原生就是显式断点
            assert p["cache_style"] == "explicit_breakpoint"
        else:
            sniffed = OpenAICompatAdapter(llm)._supports_cache    # adapters.py:302 的嗅探结果
            assert (p["cache_style"] == "passthrough") is sniffed, p["id"]


def test_cache_style三种取值():
    assert seed_cache_style("anthropic", "") == "explicit_breakpoint"
    assert seed_cache_style("openai_compat", "https://openrouter.ai/api/v1") == "passthrough"
    assert seed_cache_style("openai_compat", "https://api.deepseek.com/v1") == "auto_prefix"


def test_型号清单一一对应(tmp_path, monkeypatch):
    cfg = make_cfg(monkeypatch)
    s = _store(tmp_path)
    seed(s, cfg)
    by_short = {m["short_name"]: m for m in s.models() if m["short_name"]}
    assert set(by_short) == set(cfg.models)
    for short, c in cfg.models.items():
        m = by_short[short]
        assert (m["model_name"], m["provider_id"]) == (c.model, c.backend)
        assert m["label"] == (c.label or c.model)
        assert ("vision" in m["capabilities"]) is supports_vision(c.model)
        assert m["price"] == cfg.PRICING_CNY.get(c.model)         # 手填价格表原样作种子
        assert m["origin"] == "seed"


def test_槽位指向对的型号(tmp_path, monkeypatch):
    cfg = make_cfg(monkeypatch)
    s = _store(tmp_path)
    seed(s, cfg)
    primary = s.slot("chat.primary")["targets"][0]
    m = s.model(primary["model_id"])
    assert (m["provider_id"], m["model_name"]) == ("deepseek", "deepseek-flash")
    assert primary["params"]["max_tokens"] == cfg.primary.max_tokens
    util = s.model(s.slot("chat.utility")["targets"][0]["model_id"])
    assert (util["provider_id"], util["model_name"]) == ("zhipu", "glm-5.3-flash")
    assert s.slot("vision") is not None


def test_环境变量直接指定的型号也能表示(tmp_path, monkeypatch):
    """NOX_PRIMARY_MODEL 填了个不在下拉清单里的名字：补一行 short_name 为空的型号。"""
    cfg = make_cfg(monkeypatch, NOX_PRIMARY_MODEL="some/brand-new-model")
    s = _store(tmp_path)
    seed(s, cfg)
    m = s.model(s.slot("chat.primary")["targets"][0]["model_id"])
    assert m["model_name"] == "some/brand-new-model" and m["short_name"] is None


def test_自定义地址与覆盖项能表示(tmp_path, monkeypatch):
    cfg = make_cfg(
        monkeypatch,
        NOX_UTILITY_PROVIDER="openai_compat",
        NOX_UTILITY_BASE_URL="https://relay.example.invalid/v1",
        NOX_UTILITY_API_KEY="KEY-MARK-UTILITY-OVERRIDE",
    )
    s = _store(tmp_path)
    seed(s, cfg)
    p = s.provider("env-utility")
    assert p and p["base_url"] == "https://relay.example.invalid/v1"
    t = s.slot("chat.utility")["targets"][0]
    assert t["params"]["key_envs"] == ["NOX_UTILITY_API_KEY"]    # 只存名字
    assert_no_markers(scan_bytes(tmp_path / "settings.db"), "库文件")


def test_库文件里没有任何密钥(tmp_path, monkeypatch):
    cfg = make_cfg(monkeypatch)
    s = _store(tmp_path)
    seed(s, cfg)
    s.close()
    blob = scan_bytes(tmp_path / "settings.db")
    assert_no_markers(blob, "库文件")
    for mark in MARKERS.values():
        assert mark.encode() not in blob


def test_幂等_再灌一次不重复_摘要不变(tmp_path, monkeypatch):
    cfg = make_cfg(monkeypatch)
    s = _store(tmp_path)
    r1 = seed(s, cfg)
    n = (len(s.providers()), len(s.models()), len(s.slots()))
    r2 = seed(s, cfg)
    assert (len(s.providers()), len(s.models()), len(s.slots())) == n
    assert r1.changed is True and r2.changed is False
    assert r1.digest == r2.digest


def test_配置变了摘要跟着变(tmp_path, monkeypatch):
    s = _store(tmp_path)
    r1 = seed(s, make_cfg(monkeypatch))
    r2 = seed(s, make_cfg(monkeypatch, NOX_PRIMARY_MODEL="glm-5.3", NOX_PRIMARY_BACKEND="zhipu"))
    assert r2.changed is True and r1.digest != r2.digest


def test_刷新不碰用户行(tmp_path, monkeypatch):
    """source='user' 是 P2 起前端改出来的行：种子刷新一行都不许动。"""
    cfg = make_cfg(monkeypatch)
    s = _store(tmp_path)
    seed(s, cfg)
    with s.transaction() as c:
        c.execute("INSERT INTO providers(id,label,protocol,base_url,cache_style,source,created_at,updated_at)"
                  " VALUES('mine','我的','openai_compat','https://x.invalid','none','user','t','t')")
        c.execute("INSERT INTO models(provider_id,model_name,origin,created_at,updated_at)"
                  " VALUES('mine','m1','manual','t','t')")
        mid = c.execute("SELECT id FROM models WHERE model_name='m1'").fetchone()["id"]
        c.execute("INSERT INTO slots(slot,targets,updated_at,updated_by) VALUES('tts',?,'t','she')",
                  (json.dumps([{"model_id": mid, "params": {}}]),))
    seed(s, cfg)
    assert s.provider("mine")["label"] == "我的"
    assert [m["model_name"] for m in s.models() if m["origin"] == "manual"] == ["m1"]
    assert s.slot("tts")["updated_by"] == "she"


def test_事务回滚_灌到一半炸了库保持原样(tmp_path, monkeypatch):
    import settings_store.seed as seed_mod

    cfg = make_cfg(monkeypatch)
    s = _store(tmp_path)
    seed(s, cfg)
    before = (len(s.providers()), len(s.models()), len(s.slots()))

    class Boom(Exception):
        pass

    real = seed_mod.json.dumps
    calls = {"n": 0}

    def flaky(obj, *a, **k):
        calls["n"] += 1
        if calls["n"] == 12:           # 事务进行到一半
            raise Boom
        return real(obj, *a, **k)

    monkeypatch.setattr(seed_mod.json, "dumps", flaky)
    try:
        seed(s, cfg)
    except Boom:
        pass
    monkeypatch.setattr(seed_mod.json, "dumps", real)
    assert (len(s.providers()), len(s.models()), len(s.slots())) == before


def test_resolve_key口径与config的api_key一致(monkeypatch):
    """`Backend.api_key`：按顺序找第一个非空、会 strip。"""
    monkeypatch.setenv("ZHIPU_API_KEY", "  ")
    monkeypatch.setenv("GLM_API_KEY", " abc ")
    assert BACKENDS["zhipu"].api_key == "abc"
    assert resolve_key(list(BACKENDS["zhipu"].key_envs), __import__("os").environ) == "abc"


def test_被用户型号引用的种子厂商_刷新时被对齐而不是被删(tmp_path, monkeypatch):
    """种子厂商若还被用户型号引用（P2 起会发生），不能删（外键），但内容要与代码对齐。"""
    cfg = make_cfg(monkeypatch)
    s = _store(tmp_path)
    seed(s, cfg)
    with s.transaction() as c:
        c.execute("INSERT INTO models(provider_id,model_name,origin,created_at,updated_at)"
                  " VALUES('deepseek','my-own-model','manual','t','t')")
        c.execute("UPDATE providers SET label='被篡改', base_url='https://evil.invalid/v1'"
                  " WHERE id='deepseek'")
    seed(s, cfg)
    p = s.provider("deepseek")
    assert p["label"] == "DeepSeek" and p["base_url"] == BACKENDS["deepseek"].base_url
    assert any(m["model_name"] == "my-own-model" for m in s.models())
