"""配置层 P2d：她加一家 / 改一家 / 加型号 / 改型号（2026-10-11）。

守：
  · 会影响请求的改动**先真测通过才保存**；没过 = 什么都没写（厂商、型号、key 一个都不留），并把没过的原因带回去
  · 改地址必须重填 key；一个地址只许一家（档案按地址认厂商，两家同地址会拿错 key）
  · 代码自带的不能改（409）；她加的型号短名 `u-` 开头，种子刷新不撞、影子对账不报
  · 新型号能力只给 chat；标能看图要先看图测试通过
  · 加了就能用：Models 清单里有、能选、聊天走它那一家的地址和 key（从 SDK 客户端断言）
"""

from __future__ import annotations

import dataclasses
import json
import logging
import types
from types import SimpleNamespace

import pytest

from agent.llm import Turn, Usage
from settings_store import probe, shadow
from settings_store.edit import EditError, Editor
from settings_store.keys import FILENAME, KeyStore
from settings_store.seed import seed
from settings_store.store import SettingsStore

from tests.settings_helpers import MARKERS, make_cfg

KEY = "KEY-MARK-GEMINI-P2D"
URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def ok(text="好", hit=0):
    return Turn(stop_reason="end_turn", text=text, usage=Usage(input_tokens=1300, output_tokens=2, cache_read_tokens=hit))


class Fake:
    def __init__(self, turns):
        self.turns, self.made = turns, []

    def make(self, llm):
        outer = self

        class A:
            cfg = llm

            def complete(self, messages, tools, **kw):
                return outer.turns.pop(0)

        self.made.append(llm)
        return A()


def _env(monkeypatch, tmp_path, turns):
    cfg = make_cfg(monkeypatch)
    s = SettingsStore(tmp_path / "settings.db")
    seed(s, cfg)
    ks = KeyStore(tmp_path / FILENAME)
    fake = Fake(turns)
    ed = Editor(s, ks, probe.Prober(s, ks, make=fake.make, list_models=lambda llm: ["gemini-3-flash", "gemini-3-pro"],
                                    sleep=lambda _: None))
    return cfg, s, ks, ed, fake


def _add(ed, **kw):
    args = dict(id="gemini", label="Gemini", protocol="openai_compat", base_url=URL, cache_style="auto_prefix",
                key=KEY, models=["gemini-3-flash"])
    args.update(kw)
    return ed.add_provider(**args)


def test_红线_加一家_真测通过才保存_全套落库(monkeypatch, tmp_path):
    cfg, s, ks, ed, fake = _env(monkeypatch, tmp_path, [ok(), ok(hit=1200)])
    out = _add(ed)
    assert [r["kind"] for r in out["results"]] == ["chat", "cache"]
    assert fake.made[0].api_key == KEY and fake.made[0].base_url == URL, "测的是她填的那条线路"
    p = s.provider("gemini")
    assert p["source"] == "user" and ks.get("gemini") == KEY
    m = next(x for x in s.models() if x["provider_id"] == "gemini")
    assert m["short_name"] == "u-gemini-3-flash" and m["capabilities"] == ["chat"], "新型号只给 chat"
    assert s.last_test("model:gemini/gemini-3-flash", "chat")["ok"] == 1
    # 种子刷新不碰她加的；影子对账不把她加的当不一致
    seed(s, cfg)
    assert s.provider("gemini") and any(x["short_name"] == "u-gemini-3-flash" for x in s.models())
    assert shadow.compare(s, cfg).ok, "她加的型号让影子对账报不一致 → 档案会被停用"


def test_红线_真测没过_什么都不留(monkeypatch, tmp_path):
    _, s, ks, ed, _ = _env(monkeypatch, tmp_path, [ok(text="")])
    with pytest.raises(EditError) as e:
        _add(ed)
    assert "没有保存" in str(e.value) and e.value.results and e.value.results[0].ok is False
    assert s.provider("gemini") is None and ks.get("gemini") == ""
    assert not [m for m in s.models() if m["provider_id"] == "gemini"]


@pytest.mark.parametrize("kw,msg", [
    (dict(id="Bad Name"), "小写"),
    (dict(models=[]), "至少"),
    (dict(models=["a", "b", "c", "d"]), "最多"),
    (dict(base_url="http://x.example.com/v1"), "https"),
    (dict(base_url="https://x.example.com/v1?key=abc"), "https"),
    (dict(base_url="https://api.deepseek.com/v1"), "已经是"),
    (dict(key=""), "key"),
    (dict(id="deepseek"), "已经有"),
])
def test_加一家_不合规的拒收(monkeypatch, tmp_path, kw, msg):
    _, s, _, ed, fake = _env(monkeypatch, tmp_path, [ok()] * 8)
    with pytest.raises(EditError, match=msg):
        _add(ed, **kw)
    assert not fake.made, "不合规的不许先花钱去测"


def test_红线_改地址必须重填key(monkeypatch, tmp_path):
    _, s, ks, ed, fake = _env(monkeypatch, tmp_path, [ok(), ok(hit=1), ok(), ok(hit=1)])
    _add(ed)
    with pytest.raises(EditError, match="重新填 key"):
        ed.patch_provider("gemini", base_url="https://relay.example.com/v1")
    out = ed.patch_provider("gemini", base_url="https://relay.example.com/v1", key="KEY-MARK-NEW-1")
    assert out["tested"] and fake.made[-1].base_url == "https://relay.example.com/v1"
    assert s.provider("gemini")["base_url"] == "https://relay.example.com/v1" and ks.get("gemini") == "KEY-MARK-NEW-1"


def test_只改名字不花钱(monkeypatch, tmp_path):
    _, s, _, ed, fake = _env(monkeypatch, tmp_path, [ok(), ok(hit=1)])
    _add(ed)
    n = len(fake.made)
    assert ed.patch_provider("gemini", label="我的 Gemini")["tested"] is False
    assert len(fake.made) == n and s.provider("gemini")["label"] == "我的 Gemini"


def test_代码自带的改不了_但能在名下加型号(monkeypatch, tmp_path):
    cfg, s, _, ed, _ = _env(monkeypatch, tmp_path, [ok(), ok(hit=1)])
    with pytest.raises(EditError) as e:
        ed.patch_provider("deepseek", label="x")
    assert e.value.status == 409
    seeded = next(m for m in s.models() if m["model_name"] == "deepseek-flash")
    with pytest.raises(EditError) as e:
        ed.patch_model(seeded["id"], label="x")
    assert e.value.status == 409
    out = ed.add_models("deepseek", ["deepseek-v5-preview"])
    assert out["models"] == ["deepseek-v5-preview"]
    seed(s, cfg)
    assert any(m["model_name"] == "deepseek-v5-preview" for m in s.models()), "种子刷新把她加的型号冲掉了"
    assert shadow.compare(s, cfg).ok


def test_红线_标能看图要先看图测试通过(monkeypatch, tmp_path):
    _, s, _, ed, _ = _env(monkeypatch, tmp_path, [ok(), ok(hit=1), ok(text="红色")])
    _add(ed)
    mid = next(m["id"] for m in s.models() if m["provider_id"] == "gemini")
    with pytest.raises(EditError, match="测看图"):
        ed.patch_model(mid, capabilities=["chat", "vision"])
    ed.prober.test_model("gemini", "gemini-3-flash", vision=True)       # 看图测试通过（答「红色」）
    ed.patch_model(mid, capabilities=["chat", "vision", "video"])
    assert s.model(mid)["capabilities"] == ["chat", "vision", "video"]
    with pytest.raises(EditError, match="chat 必须在"):
        ed.patch_model(mid, capabilities=["vision"])


def test_改缓存打法要真测(monkeypatch, tmp_path):
    _, s, _, ed, fake = _env(monkeypatch, tmp_path, [ok(), ok(hit=1), ok(), ok(hit=1)])
    _add(ed)
    mid = next(m["id"] for m in s.models() if m["provider_id"] == "gemini")
    n = len(fake.made)
    ed.patch_model(mid, cache_style="passthrough")
    assert len(fake.made) == n + 1 and fake.made[-1].cache_style == "passthrough"
    assert s.model(mid)["cache_style"] == "passthrough"


def test_删一家_型号和key一起走(monkeypatch, tmp_path):
    _, s, ks, ed, _ = _env(monkeypatch, tmp_path, [ok(), ok(hit=1)])
    _add(ed)
    ed.delete_provider("gemini")
    assert s.provider("gemini") is None and ks.get("gemini") == ""
    assert not [m for m in s.models() if m["provider_id"] == "gemini"]


# ---- 加了就能用（Nox 这一侧）---------------------------------------------------------


def _nox_with_profile(monkeypatch, tmp_path):
    import nox as nox_mod
    from settings_store import startup

    cfg = dataclasses.replace(make_cfg(monkeypatch), db_path=str(tmp_path / "nox.db"))
    core = SimpleNamespace(cfg=cfg, loop=SimpleNamespace(adapter=None), router=SimpleNamespace(light_adapter=None),
                           _adapters={}, chat_model_key=lambda: None)
    startup.start_shadow(core)
    for name in ("user_model", "_user_adapter", "adapter_for", "model_name"):
        setattr(core, name, types.MethodType(getattr(nox_mod.Nox, name), core))
    return core


def test_红线_她加的型号_聊天走它那一家的地址和key(monkeypatch, tmp_path):
    core = _nox_with_profile(monkeypatch, tmp_path)
    fake = Fake([ok(), ok(hit=1)])
    ed = Editor(core.profiles, core.config_keys, probe.Prober(core.profiles, core.config_keys, make=fake.make,
                                                              sleep=lambda _: None))
    _add(ed)
    a = core.adapter_for("u-gemini-3-flash")
    assert a is not None, "加了用不上"
    assert a._client.api_key == KEY and str(a._client.base_url).rstrip("/") == URL
    assert a._client.api_key != MARKERS["DEEPSEEK_API_KEY"]
    assert core.model_name("u-gemini-3-flash") == "gemini-3-flash"
    assert core.adapter_for("u-nope") is None


def test_档案没装_她加的型号当没有(monkeypatch, tmp_path):
    core = _nox_with_profile(monkeypatch, tmp_path)
    core.profiles = None
    assert core.user_model("u-gemini-3-flash") is None


# ---- 接口 ----------------------------------------------------------------------


def test_接口_加一家_清单里能选到_没过带原因_日志没有key(monkeypatch, tmp_path, caplog):
    from tests.test_settings_views import _client

    import nox as nox_mod

    c, nox = _client(monkeypatch, tmp_path)
    nox.user_model = types.MethodType(nox_mod.Nox.user_model, nox)      # FakeNox 没有，用真的那个
    picked = []
    nox.set_chat_model = lambda k: picked.append(k) or True
    nox.chat_model_key = lambda: picked[-1] if picked else None
    turns = [ok(text=""), ok(), ok(hit=5)]
    monkeypatch.setattr(probe, "_default_make", lambda llm: Fake(turns).make(llm))
    body = {"id": "gemini", "label": "Gemini", "base_url": URL, "key": KEY, "models": ["gemini-3-flash"]}
    with caplog.at_level(logging.DEBUG):
        bad = c.post("/api/nox/config/providers", json=body)
        assert bad.status_code == 400
        d = bad.json()["detail"]
        assert "没有保存" in d["error"] and d["results"][0]["ok"] is False
        good = c.post("/api/nox/config/providers", json=body)
        assert good.status_code == 200, good.text
    models = c.get("/api/nox/models").json()
    mine = [x for x in models["choices"] if x.get("user")]
    assert [x["key"] for x in mine] == ["u-gemini-3-flash"]
    assert models["providers"]["gemini"] == {"label": "Gemini", "configured": True}
    sel = c.post("/api/nox/model", json={"key": "u-gemini-3-flash"})
    assert sel.status_code == 200 and picked == ["u-gemini-3-flash"], "清单里有却不让选"
    assert c.patch("/api/nox/config/providers/deepseek", json={"label": "x"}).status_code == 409
    for blob in (bad.text, good.text, json.dumps(models), caplog.text):
        assert KEY not in blob
    pre = c.get("/api/nox/config/presets").json()
    z = next(p for p in pre["presets"] if p["preset"] == "zhipu")
    assert z["base_url"].endswith("/v4") and z["exists"] is True
    flags = {p["preset"]: p["exists"] for p in pre["presets"]}
    assert flags["anthropic"] is True, "Anthropic 本来就有（地址为空，按名字认）"
    assert flags["gemini"] is True and flags["minimax"] is False, "刚加的 Gemini 算有；MiniMax 没加"


def test_改完都会让已经建好的线路重套(monkeypatch, tmp_path):
    from settings_store import profile
    from tests.test_settings_views import _client

    c, _ = _client(monkeypatch, tmp_path)
    turns = [ok(), ok(hit=5)]
    monkeypatch.setattr(probe, "_default_make", lambda llm: Fake(turns).make(llm))
    calls = []
    monkeypatch.setattr(profile, "refresh", lambda core: calls.append(1) or 0)
    c.post("/api/nox/config/providers", json={"id": "gemini", "base_url": URL, "key": KEY, "models": ["gemini-3-flash"]})
    c.patch("/api/nox/config/providers/gemini", json={"label": "G"})
    c.delete("/api/nox/config/providers/gemini")
    assert len(calls) == 3, "改了不重套：按请求切模型缓存着的旧 adapter 还拿着旧 key / 旧地址"


def test_红线_测试回话_只测不存(monkeypatch, tmp_path):
    _, s, ks, ed, fake = _env(monkeypatch, tmp_path, [ok()])
    before = (len(s.providers()), len(s.models()))
    r = ed.try_route(model="gemini-3-flash", base_url=URL, key=KEY)
    assert [(x.kind, x.ok) for x in r] == [("chat", True)]
    assert fake.made[0].api_key == KEY and fake.made[0].base_url == URL
    assert (len(s.providers()), len(s.models())) == before and ks.get("gemini") == ""
    assert s.last_test("model:/gemini-3-flash", "chat") is None, "只测不存：结论也不落库"


def test_测试回话_已有的厂商用存着的key(monkeypatch, tmp_path):
    _, s, _, ed, fake = _env(monkeypatch, tmp_path, [ok()])
    ed.try_route(provider_id="openrouter", model="google/gemini-3-flash")
    assert fake.made[0].api_key == MARKERS["OPENROUTER_API_KEY"] and "openrouter" in fake.made[0].base_url


def test_接口_测试回话(monkeypatch, tmp_path):
    from tests.test_settings_views import _client

    c, _ = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(probe, "_default_make", lambda llm: Fake([ok(text="")]).make(llm))
    r = c.post("/api/nox/config/try", json={"model": "m1", "base_url": URL, "key": KEY})
    assert r.status_code == 200 and r.json()["results"][0]["ok"] is False
    assert KEY not in r.text
