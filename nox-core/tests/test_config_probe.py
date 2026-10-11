"""配置层 P2c：真测（2026-10-11）。

假 adapter 代替真厂商（线上那一遍用真 API 跑过，记在设计稿）。要守的：
  · 「通」= 正文非空，不是 HTTP 200（思考模型 200 但正文空 → 失败，并说清原因）
  · 缓存单独一行：第二次命中才算；没命中不影响「通」
  · 看图：答出「红」才算；答「无法确定」= 失败（v4-pro 那种悄悄丢图的）；**测看图时图必须真的发出去**
  · 走生产同一个工厂（make_adapter + 计量），key 前端 > 环境变量
  · 1 分钟内同一项不重复花钱；结论里没有回答正文；错误里的 key 抹掉
  · 徽章：缺失 / 未测 / 正常 / 失败；换了 key 回到未测
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest

from agent.llm import Turn, Usage
from settings_store import probe
from settings_store.keys import FILENAME, KeyStore
from settings_store.seed import seed
from settings_store.store import SettingsStore

from tests.settings_helpers import MARKERS, make_cfg, scan_bytes

REPLY = "REPLY-MARK-好"
FRONT = "KEY-MARK-FRONT-P2C"


class FakeAdapter:
    def __init__(self, cfg, turns):
        self.cfg, self.turns, self.calls = cfg, turns, []          # 共用一串：测看图时会另建一个 adapter

    def complete(self, messages, tools, **kw):
        self.calls.append({"messages": messages, **kw})
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return t


def ok(text=REPLY, inp=1300, hit=0, out=5):
    return Turn(stop_reason="end_turn", text=text,
                usage=Usage(input_tokens=inp, output_tokens=out, cache_read_tokens=hit))


def _setup(monkeypatch, tmp_path, turns, **kw):
    cfg = make_cfg(monkeypatch)
    s = SettingsStore(tmp_path / "settings.db")
    seed(s, cfg)
    ks = KeyStore(tmp_path / FILENAME)
    made = []

    def make(llm):
        a = FakeAdapter(llm, turns)
        made.append(a)
        return a

    p = probe.Prober(s, ks, make=make, list_models=kw.pop("list_models", lambda llm: ["a", "b"]),
                     sleep=lambda s_: None, **kw)
    return p, s, ks, made


def test_红线_通不通看正文_不看200(monkeypatch, tmp_path):
    p, s, _, _ = _setup(monkeypatch, tmp_path, [ok(text="", out=2048)])
    r = p.test_model("deepseek", "deepseek-flash", vision=False)
    assert [x.kind for x in r] == ["chat"] and r[0].ok is False
    assert "正文是空的" in r[0].detail and "思考" in r[0].detail


def test_通了_缓存命中单独一行(monkeypatch, tmp_path):
    p, s, _, made = _setup(monkeypatch, tmp_path, [ok(), ok(hit=1280)])
    r = p.test_model("deepseek", "deepseek-flash", vision=False)
    assert [(x.kind, x.ok) for x in r] == [("chat", True), ("cache", True)]
    assert "1280" in r[1].detail
    a = made[0]
    assert a.calls[0]["system"] == a.calls[1]["system"] == probe.TEST_SYSTEM, "两次前缀必须逐字节相同"
    assert a.calls[0]["max_tokens"] >= 2048 and a.calls[0]["depth"] == "low", "思考模型要给足、压低档位"


def test_缓存没命中_不影响通(monkeypatch, tmp_path):
    p, s, _, _ = _setup(monkeypatch, tmp_path, [ok(), ok(hit=0)])
    r = p.test_model("zhipu", "glm-5.3-flash", vision=False)
    assert [(x.kind, x.ok) for x in r] == [("chat", True), ("cache", False)]
    assert probe.status_of(s, probe.target_of("zhipu", "glm-5.3-flash"), True)["status"] == "正常"


def test_红线_看图_答出红才算_图真的发出去了(monkeypatch, tmp_path):
    p, s, _, made = _setup(monkeypatch, tmp_path, [ok(), ok(hit=9), ok(text="红色", inp=225)])
    r = p.test_model("deepseek", "deepseek-flash")                     # 档案里 flash 有 vision → 自动测
    assert [(x.kind, x.ok) for x in r][-1] == ("vision", True)
    v = made[-1]
    assert v.cfg.vision is True, "测看图时不许让档案把图降成文字"
    assert v.calls[0]["messages"][0].images, "图没发出去"


def test_红线_看图_悄悄丢图的判失败(monkeypatch, tmp_path):
    p, s, _, _ = _setup(monkeypatch, tmp_path, [ok(), ok(hit=9), ok(text="无法确定", inp=99)])
    r = p.test_model("deepseek", "deepseek-v4-pro", vision=True)
    v = r[-1]
    assert v.kind == "vision" and v.ok is False and "99" in v.detail


def test_走生产的工厂_计量_前端key优先(monkeypatch, tmp_path):
    from agent import adapters, meter

    cfg = make_cfg(monkeypatch)
    s = SettingsStore(tmp_path / "settings.db")
    seed(s, cfg)
    ks = KeyStore(tmp_path / FILENAME)
    ks.set("deepseek", FRONT)
    llm = probe.llm_for(s, ks, "deepseek", "deepseek-flash")
    assert llm.api_key == FRONT
    a = probe._default_make(llm)
    assert isinstance(a, meter.Metered) and a._task == "config-test", "测试花的钱要进账本"
    assert a._inner._client.api_key == FRONT
    ks.clear("deepseek")
    assert probe.llm_for(s, ks, "deepseek", "deepseek-flash").api_key == MARKERS["DEEPSEEK_API_KEY"]
    adapters.use_profiles(None)


def test_没配key_是缺失_不花钱(monkeypatch, tmp_path):
    cfg = make_cfg(monkeypatch, OPENROUTER_API_KEY="")
    s = SettingsStore(tmp_path / "settings.db")
    seed(s, cfg)
    called = []
    p = probe.Prober(s, KeyStore(tmp_path / FILENAME), make=lambda llm: called.append(1))
    r = p.test_model("openrouter", "anthropic/claude-sonnet-5")
    assert r[0].ok is False and "缺失" in r[0].detail and not called
    assert probe.status_of(s, probe.target_of("openrouter"), False)["status"] == "缺失"


def test_一分钟内不重复花钱(monkeypatch, tmp_path):
    p, s, _, made = _setup(monkeypatch, tmp_path, [ok(), ok(hit=5)])
    p.test_model("deepseek", "deepseek-flash", vision=False)
    again = p.test_model("deepseek", "deepseek-flash", vision=False)
    assert len(made) == 1, "又建了一个 adapter 去打了"
    assert [x.kind for x in again] == ["chat", "cache"] and all(x.reused for x in again)


def test_红线_结论里没有回答正文_错误里没有key(monkeypatch, tmp_path):
    leak = RuntimeError(f"401 Incorrect API key provided: {MARKERS['DEEPSEEK_API_KEY']} and sk-abcdef123456")
    p, s, _, _ = _setup(monkeypatch, tmp_path, [ok(), ok(hit=1)])
    p.test_model("deepseek", "deepseek-flash", vision=False)
    p2, s2, _, _ = _setup(monkeypatch, tmp_path / "b", [leak])
    r = p2.test_model("zhipu", "glm-5.3-flash", vision=False)
    assert r[0].ok is False
    blob = scan_bytes(tmp_path / "settings.db") + scan_bytes(tmp_path / "b" / "settings.db")
    text = blob.decode("utf-8", "ignore") + json.dumps([x.as_dict() for x in r], ensure_ascii=False)
    assert REPLY not in text, "回答正文进了库"
    assert MARKERS["DEEPSEEK_API_KEY"] not in text and "sk-abcdef123456" not in text, "错误信息里的 key 没抹"


def test_整家测_型号列表和对话分开存(monkeypatch, tmp_path):
    p, s, _, _ = _setup(monkeypatch, tmp_path, [ok(), ok(hit=3)], list_models=lambda llm: [])
    r = p.test_provider("zhipu")
    kinds = [(x.kind, x.ok) for x in r]
    assert kinds[0] == ("models", False) and ("chat", True) in kinds
    assert probe.status_of(s, probe.target_of("zhipu"), True)["status"] == "失败"


# ---- 接口 ----------------------------------------------------------------------


def test_接口_真测_型号徽章_换key回到未测(monkeypatch, tmp_path):
    from tests.test_settings_views import _client

    c, _ = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(probe, "_default_make", lambda llm: FakeAdapter(llm, [ok(), ok(hit=7)]))
    monkeypatch.setattr(probe, "_default_list_models", lambda llm: ["x"])
    r = c.post("/api/nox/config/test", json={"provider_id": "zhipu", "model_name": "glm-5.3-flash", "vision": False})
    assert r.status_code == 200 and [x["kind"] for x in r.json()["results"]] == ["chat", "cache"]
    m = next(x for x in c.get("/api/nox/config/models").json()["models"] if x["model_name"] == "glm-5.3-flash")
    assert m["status"] == "正常" and m["tests"]["cache"]["ok"] is True
    assert REPLY not in json.dumps(m, ensure_ascii=False)
    c.put("/api/nox/config/providers/zhipu/key", json={"key": FRONT})
    m = next(x for x in c.get("/api/nox/config/models").json()["models"] if x["model_name"] == "glm-5.3-flash")
    assert m["status"] == "未测", "换了 key 还挂着上一把 key 测出来的「正常」"
    assert c.post("/api/nox/config/test", json={"provider_id": "nope"}).status_code == 404
