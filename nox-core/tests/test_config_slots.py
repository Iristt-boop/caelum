"""配置层 P3：Slots 里换杂活 / 看图（/ 视频 / 画图），下一句就生效，能换回去（2026-10-13）。

从使用方断言：杂活看「各路持有者手里那个 adapter 的 SDK 客户端打向哪、用哪把 key」，看图看「现取出来的线路」。
钉住的：
  · 🔴 Router、Archiver（外面包着计量）、话题池 / 日卡 / 电话拿的是同一个代理 —— 换一次全换，不会一半换一半没换
  · 只许选能用的（能力对、开着、有 key；看图还得是 OpenAI 兼容协议）
  · 换回去一步步往回退；没得退就说
  · 🔴 她的选择不进 slots 表：种子刷新照常、影子对账照常（不然下次启动档案就被停用）
  · 重启后接着用她选的；选的型号被删了就回服务器默认
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest

from agent import meter
from agent.adapters import make_adapter
from settings_store import shadow, slots, startup
from settings_store.keys import FILENAME, KeyStore
from settings_store.seed import seed
from settings_store.slots import SlotAdapter

from tests.settings_helpers import MARKERS, make_cfg


def _core(monkeypatch, tmp_path, **env):
    cfg = dataclasses.replace(make_cfg(monkeypatch, **env), db_path=str(tmp_path / "nox.db"))
    proxy = SlotAdapter("chat.utility", make_adapter(cfg.utility))
    core = SimpleNamespace(cfg=cfg, loop=SimpleNamespace(adapter=make_adapter(cfg.primary)),
                           router=SimpleNamespace(light_adapter=proxy), _adapters={})
    startup.start_shadow(core)
    return core, proxy


def _model_id(store, name):
    return next(m["id"] for m in store.models() if m["model_name"] == name)


def _add_user_model(store, provider_id, name, caps):
    with store.transaction() as c:
        c.execute("INSERT INTO models(provider_id,model_name,short_name,label,capabilities,origin,enabled,"
                  "created_at,updated_at) VALUES(?,?,?,?,?,'manual',1,'t','t')",
                  (provider_id, name, "u-" + name.replace("/", "-"), name, json.dumps(caps)))
    return _model_id(store, name)


def test_红线_换杂活_所有持有者同时换_打向新的地址和key(monkeypatch, tmp_path):
    core, proxy = _core(monkeypatch, tmp_path)
    archiver_holds = meter.tag(proxy, "memory-archive")          # Archiver 那样包一层计量
    pool_holds = meter.tag(proxy, "topic-filter")                 # 话题池那样
    assert proxy.current()._client.api_key == MARKERS["ZHIPU_API_KEY"]          # 默认：智谱
    mid = _model_id(core.profiles, "deepseek-flash")
    slots.set_choice(core.profiles, core.config_keys, "chat.utility", mid)
    slots.apply(core)
    for holder in (core.router.light_adapter, archiver_holds, pool_holds):
        assert holder.cfg.model == "deepseek-flash"
        assert holder._client.api_key == MARKERS["DEEPSEEK_API_KEY"], "这一个持有者还拿着旧的"
        assert "deepseek" in str(holder._client.base_url)
    assert core.router.light_adapter is proxy, "代理本身没换，只换里面那个"


def test_回到服务器默认_和换回去(monkeypatch, tmp_path):
    core, proxy = _core(monkeypatch, tmp_path)
    s = core.profiles
    slots.set_choice(s, core.config_keys, "chat.utility", _model_id(s, "deepseek-flash"))
    slots.apply(core)
    slots.set_choice(s, core.config_keys, "chat.utility", None)            # 回到服务器默认
    slots.apply(core)
    assert proxy.cfg.model == "glm-5.3-flash"
    assert slots.revert(s, "chat.utility") == "deepseek/deepseek-flash"         # 退一步：回到 deepseek
    slots.apply(core)
    assert proxy.cfg.model == "deepseek-flash"
    assert slots.revert(s, "chat.utility") is None                         # 再退：服务器默认
    with pytest.raises(slots.SlotError, match="没有上一个"):
        slots.revert(s, "chat.utility")


def test_只许选能用的(monkeypatch, tmp_path):
    core, _ = _core(monkeypatch, tmp_path, OPENROUTER_API_KEY="")
    s, ks = core.profiles, core.config_keys
    util = {o["model_name"] for o in slots.options(s, ks, "chat.utility")}
    vis = {o["model_name"] for o in slots.options(s, ks, "vision")}
    assert "deepseek-flash" in util and "anthropic/claude-sonnet-5" not in util, "OpenRouter 没 key 不许选"
    assert "deepseek-flash" in vis and "deepseek-v4-pro" not in vis, "看不见图的不许放进看图位置"
    _add_user_model(s, "anthropic", "claude-x", ["chat", "vision"])
    assert "claude-x" not in {o["model_name"] for o in slots.options(s, ks, "vision")}, "看图只接 OpenAI 兼容"
    assert slots.options(s, ks, "video") == [] and slots.options(s, ks, "image") == []
    with pytest.raises(slots.SlotError, match="放不进"):
        slots.set_choice(s, ks, "vision", _model_id(s, "deepseek-v4-pro"))
    with pytest.raises(slots.SlotError) as e:
        slots.set_choice(s, ks, "chat.primary", 1)
    assert e.value.status == 404, "Primary 走 /api/nox/model，不在这里换"


def test_红线_看图每次现取_换了下一张就生效(monkeypatch, tmp_path):
    import nox as nox_mod
    from agent import vision

    core, _ = _core(monkeypatch, tmp_path)
    core.vision_cfg = nox_mod.Nox.vision_cfg.__get__(core)
    assert core.vision_cfg() is core.cfg.vision, "没选：服务器默认那份"
    slots.set_choice(core.profiles, core.config_keys, "vision", _model_id(core.profiles, "deepseek-flash"))
    got = core.vision_cfg()
    assert got.model == "deepseek-flash" and got.api_key == MARKERS["DEEPSEEK_API_KEY"]
    assert got.max_tokens == core.cfg.vision.max_tokens, "长度 / 超时沿用默认那份"
    seen = []
    monkeypatch.setattr("openai.OpenAI", lambda **kw: seen.append(kw) or (_ for _ in ()).throw(RuntimeError("stop")))
    vision.describe(["data:image/png;base64,AAAA"], core.vision_cfg)       # 传的是取值函数（电脑 / Stack-chan 那样）
    assert seen and seen[0]["api_key"] == MARKERS["DEEPSEEK_API_KEY"], "拿着取值函数的地方没用上新选的"


def test_红线_种子刷新换了型号id_她选的还在(monkeypatch, tmp_path):
    """种子每次启动删了重灌，型号 id 会变 —— 第一版按 id 存，重启一次她的选择就指空、悄悄回了默认。"""
    core, _ = _core(monkeypatch, tmp_path)
    s = core.profiles
    old_id = _model_id(s, "deepseek-flash")
    slots.set_choice(s, core.config_keys, "chat.utility", old_id)
    seed(s, core.cfg)
    assert _model_id(s, "deepseek-flash") != old_id, "前提：种子刷新后 id 真变了"
    assert slots.choice(s, "chat.utility")["model"]["model_name"] == "deepseek-flash"


def test_红线_她的选择不碰种子和影子对账(monkeypatch, tmp_path):
    core, _ = _core(monkeypatch, tmp_path)
    s = core.profiles
    slots.set_choice(s, core.config_keys, "chat.utility", _model_id(s, "deepseek-flash"))
    slots.set_choice(s, core.config_keys, "vision", _model_id(s, "deepseek-flash"))
    seed(s, core.cfg)
    assert shadow.compare(s, core.cfg).ok, "她一换槽位，下次启动对账就不过、档案被停用"
    assert slots.choice(s, "chat.utility")["model"]["model_name"] == "deepseek-flash"


def test_重启后接着用她选的_选的型号没了回默认(monkeypatch, tmp_path):
    core, _ = _core(monkeypatch, tmp_path)
    slots.set_choice(core.profiles, core.config_keys, "chat.utility", _model_id(core.profiles, "deepseek-flash"))
    core2, proxy2 = _core(monkeypatch, tmp_path)                   # 重启：新的进程、同一个库
    assert proxy2.cfg.model == "deepseek-flash"
    with core2.profiles.transaction() as c:
        c.execute("UPDATE models SET enabled=0 WHERE model_name='deepseek-flash'")
    slots.apply(core2)
    assert proxy2.cfg.model == "glm-5.3-flash"


def test_换了key_她选的杂活跟着重建(monkeypatch, tmp_path):
    from settings_store import profile

    core, proxy = _core(monkeypatch, tmp_path)
    slots.set_choice(core.profiles, core.config_keys, "chat.utility", _model_id(core.profiles, "deepseek-flash"))
    slots.apply(core)
    core.config_keys.set("deepseek", "KEY-MARK-FRONT-SLOT")
    profile.refresh(core)
    assert proxy._client.api_key == "KEY-MARK-FRONT-SLOT"


def test_代理身上赋值落到里面那个上():
    inner = SimpleNamespace(cfg="a")
    p = SlotAdapter("chat.utility", inner)
    p.cfg = "b"
    assert inner.cfg == "b" and p.cfg == "b"


def test_话题池日卡电话拿的是同一个代理():
    from api.server import _shared_utility

    proxy = SlotAdapter("chat.utility", SimpleNamespace(cfg=None))
    assert _shared_utility(SimpleNamespace(router=SimpleNamespace(light_adapter=proxy))) is proxy
    assert _shared_utility(SimpleNamespace(router=SimpleNamespace(light_adapter=object()))) is None


# ---- 接口 ----------------------------------------------------------------------


def test_接口_查_换_换回去_旧视图也跟着显示(monkeypatch, tmp_path):
    from tests.test_settings_views import _client

    c, nox = _client(monkeypatch, tmp_path)
    r = c.get("/api/nox/config/slot-choices").json()
    util = next(x for x in r["slots"] if x["slot"] == "chat.utility")
    assert util["choice"] is None and util["default"]["model_name"] == "glm-5.3-flash"
    assert any(o["model_name"] == "deepseek-flash" for o in util["options"])
    assert [x["slot"] for x in r["slots"]] == ["chat.utility", "vision", "video", "image"]
    mid = next(o["model_id"] for o in util["options"] if o["model_name"] == "deepseek-flash")
    r = c.put("/api/nox/config/slots/chat.utility", json={"model_id": mid}).json()
    util = next(x for x in r["slots"] if x["slot"] == "chat.utility")
    assert util["choice"]["model_name"] == "deepseek-flash" and util["can_revert"] is True
    old_view = next(x for x in c.get("/api/nox/config/slots").json()["slots"] if x["slot"] == "chat.utility")
    assert old_view["model"]["name"] == "deepseek-flash" and old_view["updated_by"] == "she", "地图 / 旧视图还写着种子那个"
    r = c.post("/api/nox/config/slots/chat.utility/revert").json()
    assert next(x for x in r["slots"] if x["slot"] == "chat.utility")["choice"] is None
    assert c.put("/api/nox/config/slots/chat.primary", json={"model_id": mid}).status_code == 404
    v4pro = next(m["id"] for m in nox.settings.models() if m["model_name"] == "deepseek-v4-pro")
    assert c.put("/api/nox/config/slots/vision", json={"model_id": v4pro}).status_code == 400


# ---- 真的 Nox：杂活确实包在代理里（离线构造，同 test_guide_nox_assembly 的做法）----------------


def test_红线_真Nox里_Router和Archiver拿的是同一个槽位代理(monkeypatch):
    import re
    from pathlib import Path

    import config as config_mod
    import nox as nox_mod
    from config import Config, LLMConfig
    from tests.test_guide_nox_assembly import _SECRETISH, _FakeOB

    src = Path(config_mod.__file__).read_text(encoding="utf-8")
    for name in set(re.findall(r'_env(?:_int|_float)?\(\s*"([A-Z0-9_]+)"', src)):
        if _SECRETISH.search(name):
            monkeypatch.setenv(name, "")
    monkeypatch.setattr(nox_mod, "OmbreBrain", _FakeOB)
    off = dict(api_key="KEY-MARK-OFFLINE", base_url="http://127.0.0.1:9/v1")
    cfg = dataclasses.replace(
        Config(),
        primary=LLMConfig(provider="openai_compat", model="asm-model", **off),
        utility=LLMConfig(provider="openai_compat", model="util-model", **off),
        bridge_url="http://127.0.0.1:9", bridge_token="t", ob_url="http://127.0.0.1:9",
    )
    n = nox_mod.Nox(cfg)
    light = n.router.light_adapter
    assert isinstance(light, SlotAdapter), "杂活没包代理：Slots 里换了杂活，Router 还用旧的"
    held = n.archiver.summarizer
    assert getattr(held, "_inner", None) is light, "Archiver 拿的不是同一个代理（会一半换一半没换）"
    assert n.vision_cfg() is cfg.vision, "没装档案时看图走服务器默认"


def test_她选的那一家改了地址_杂活跟着打向新地址(monkeypatch, tmp_path):
    """只换 key 时档案自己会重套；**改地址**档案不管（地址不从档案改），得靠重新按她的选择建一次。"""
    from settings_store import profile

    core, proxy = _core(monkeypatch, tmp_path)
    s = core.profiles
    with s.transaction() as c:
        c.execute("INSERT INTO providers(id,label,protocol,base_url,cache_style,auth,key_envs,source,note,created_at,"
                  "updated_at) VALUES('relay','中转','openai_compat','https://relay-a.example.com/v1','auto_prefix',"
                  "'api_key','[]','user','','t','t')")
    core.config_keys.set("relay", "KEY-MARK-RELAY")
    slots.set_choice(s, core.config_keys, "chat.utility", _add_user_model(s, "relay", "relay-chat", ["chat"]))
    slots.apply(core)
    assert "relay-a" in str(proxy._client.base_url)
    with s.transaction() as c:
        c.execute("UPDATE providers SET base_url='https://relay-b.example.com/v1' WHERE id='relay'")
    profile.refresh(core)
    assert "relay-b" in str(proxy._client.base_url), "改了地址，杂活还打着旧地址"


def test_红线_聊天里替主线看图_用的是现取的那条(monkeypatch):
    import nox as nox_mod
    from agent.adapters import OpenAICompatAdapter
    from config import LLMConfig

    picked = LLMConfig(provider="openai_compat", model="her-vision", api_key="k", base_url="https://v.example.com/v1")
    blind = LLMConfig(provider="openai_compat", model="deepseek-v4-pro", api_key="k", base_url="https://x/v1", vision=False)
    got = []
    monkeypatch.setattr(nox_mod.vision, "describe", lambda images, cfg, **kw: got.append(cfg) or "一块红色")
    monkeypatch.setattr(nox_mod.vision, "wrap", lambda d, n: d)
    fake = SimpleNamespace(adapter_for=lambda m: None, loop=SimpleNamespace(adapter=OpenAICompatAdapter(blind)),
                           cfg=SimpleNamespace(vision="服务器默认那份", vision_timeout=5), vision_cfg=lambda: picked)
    nox_mod.Nox._see(fake, "看", ["data:image/png;base64,AAAA"], None)
    assert got == [picked], "替主线看图还在用服务器默认那份，Slots 里换了不生效"
