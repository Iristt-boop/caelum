"""配置层 P2a：adapter 读档案，不再按名字猜（2026-10-11）。

从**使用方**断言：看 adapter 真要发出去的 messages（`_assemble` / `_to_native`），不看档案表里写了什么。

守：
  1. 种子档案下，每个型号发出去的请求与老路（名字嗅探）**逐字节相同** —— 唯一的例外是真测过能看图的 deepseek-flash
  2. 缓存打不打断点读 `cache_style`，不看地址里有没有 openrouter
  3. ttl 读 `cache_ttl`；能不能收图读 `capabilities`
  4. 启动钩子：对账通过才装；`NOX_CONFIG_STORE=off` / 对账不过 → 老路；Router / Archiver 同一个 adapter 一起换
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest

from agent import adapters
from agent.adapters import OpenAICompatAdapter, make_adapter
from agent.llm import Message
from config import _build_llm
from settings_store import profile, shadow, startup
from settings_store.seed import VERIFIED_VISION, seed
from settings_store.store import SettingsStore

from tests.settings_helpers import make_cfg

IMG = "data:image/png;base64,iVBORw0KGgo="
HIST = [Message(role="user", text="今天好累"), Message(role="assistant", text="累了就靠过来"),
        Message(role="user", text="嗯")]


def _seeded(monkeypatch, tmp_path):
    cfg = make_cfg(monkeypatch)
    s = SettingsStore(tmp_path / "settings.db")
    seed(s, cfg)
    return cfg, s


def _llm(short, cfg):
    c = cfg.models[short]
    return _build_llm(backend=c.backend, model=c.model, max_tokens=cfg.primary.max_tokens)


def _wire(llm) -> str:
    a = OpenAICompatAdapter(llm)
    return json.dumps(a._assemble(HIST, "静态人设", "【你在哪】手机"), ensure_ascii=False, sort_keys=True)


def _img(llm):
    return OpenAICompatAdapter(llm)._to_native(Message(role="user", text="看", images=[IMG]))


def _sees(native) -> bool:
    return "image_url" in json.dumps(native)


def test_红线_种子档案下_每个型号发出去的请求和老路逐字节相同(monkeypatch, tmp_path):
    cfg, s = _seeded(monkeypatch, tmp_path)
    n = 0
    for short in cfg.models:
        old = _llm(short, cfg)
        if old.provider != "openai_compat":
            continue
        new = profile.apply(s, old)
        assert new.cache_style is not None, f"{short} 没套上档案 —— 这条测试就什么都没证明"
        assert _wire(new) == _wire(old), f"{short}：套档案后发出去的东西变了"
        if old.model not in VERIFIED_VISION:
            assert _img(new) == _img(old), f"{short}：收图的方式变了"
        n += 1
    assert n >= 8


def test_红线_deepseek_flash_档案说能看_图原样发给它(monkeypatch, tmp_path):
    cfg, s = _seeded(monkeypatch, tmp_path)
    flash = profile.apply(s, _llm("v4-flash", cfg))
    pro = profile.apply(s, _llm("v4-pro", cfg))
    assert _sees(_img(flash)), "10-11 真测过能看，档案也写了，还被降成文字"
    assert not _sees(_img(pro)), "v4-pro 真测是瞎的（悄悄丢图），不许把图发给它"
    assert not _sees(_img(_llm("v4-flash", cfg))), "老路（名字嗅探）照旧：不认 deepseek 看图"


def test_红线_打不打断点看档案_不看地址(monkeypatch, tmp_path):
    cfg, s = _seeded(monkeypatch, tmp_path)
    sonnet = _llm("sonnet-5", cfg)                        # OpenRouter 上的 claude：今天打断点
    assert "cache_control" in _wire(profile.apply(s, sonnet))
    with s.transaction() as c:
        c.execute("UPDATE providers SET cache_style='auto_prefix' WHERE id='openrouter'")
    assert "cache_control" not in _wire(profile.apply(s, sonnet)), "档案说自动前缀，还在打断点 = 还在嗅探 URL"

    # 反过来：地址里没有 openrouter 的中转站，档案说透传断点 → 要打
    relay = dataclasses.replace(sonnet, base_url="https://relay.example.com/v1")
    with s.transaction() as c:
        c.execute("UPDATE providers SET base_url='https://relay.example.com/v1', cache_style='passthrough'"
                  " WHERE id='openrouter'")
    assert "cache_control" in _wire(profile.apply(s, relay)), "中转站换了名字，断点就没了 —— 这正是要消灭的嗅探"


def test_型号可以覆盖厂商的打法(monkeypatch, tmp_path):
    cfg, s = _seeded(monkeypatch, tmp_path)
    with s.transaction() as c:
        c.execute("UPDATE models SET cache_style='auto_prefix' WHERE model_name='anthropic/claude-sonnet-5'")
    assert "cache_control" not in _wire(profile.apply(s, _llm("sonnet-5", cfg)))
    assert "cache_control" in _wire(profile.apply(s, _llm("opus-4-8", cfg))), "只改了一个型号，别的跟厂商"


def test_ttl_读档案(monkeypatch, tmp_path):
    cfg, s = _seeded(monkeypatch, tmp_path)
    sonnet = _llm("sonnet-5", cfg)
    assert '"ttl": "1h"' in _wire(profile.apply(s, sonnet))
    with s.transaction() as c:
        c.execute("UPDATE models SET cache_ttl='' WHERE model_name='anthropic/claude-sonnet-5'")
    w = _wire(profile.apply(s, sonnet))
    assert "cache_control" in w and '"ttl"' not in w


def test_档案里没有这条线路_原样走老路(monkeypatch, tmp_path):
    _, s = _seeded(monkeypatch, tmp_path)
    stray = _build_llm(backend="deepseek", model="x", base_override="https://nowhere.example/v1")
    assert profile.apply(s, stray) is stray


def test_老库补列(tmp_path):
    import sqlite3

    db = tmp_path / "settings.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE models(id INTEGER PRIMARY KEY, provider_id TEXT, model_name TEXT)")
    c.commit()
    c.close()
    SettingsStore(db)
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(models)")}
    assert {"cache_style", "cache_ttl"} <= cols, "线上的库是 schema 1 建的，不补列种子一灌就炸"


# ---- 启动钩子 ------------------------------------------------------------------


def _core(monkeypatch, tmp_path):
    cfg = dataclasses.replace(make_cfg(monkeypatch), db_path=str(tmp_path / "nox.db"))
    main = make_adapter(cfg.primary)
    light = make_adapter(cfg.utility)
    core = SimpleNamespace(cfg=cfg, loop=SimpleNamespace(adapter=main),
                           router=SimpleNamespace(light_adapter=light), _adapters={"old": object()})
    return core, main, light


def test_红线_启动后_主线和杂活就地换上档案_之后新建的也套(monkeypatch, tmp_path):
    core, main, light = _core(monkeypatch, tmp_path)
    assert main.cfg.cache_style is None
    startup.start_shadow(core)
    assert main.cfg.cache_style == "auto_prefix" and light.cfg.cache_style == "auto_prefix"
    assert main.cfg.vision is True, "主线 deepseek-flash：档案说能看"
    assert core.router.light_adapter is light, "就地换，不是换了个新对象（Archiver 手里那个还是旧的）"
    assert core._adapters == {}, "按请求切的模型缓存要清掉，下次按档案重建"
    later = make_adapter(_build_llm(backend="openrouter", model="anthropic/claude-sonnet-5"))
    assert later.cfg.cache_style == "passthrough" and later.cfg.cache_ttl == "1h"


def test_计量包装着的也换到里面那个(monkeypatch, tmp_path):
    from agent import meter

    core, _, light = _core(monkeypatch, tmp_path)
    core.router.light_adapter = meter.tag(light, "x")
    startup.start_shadow(core)
    assert light.cfg.cache_style == "auto_prefix"


@pytest.mark.parametrize("off", ["off", "0", "false"])
def test_红线_开关关掉_全是老路(monkeypatch, tmp_path, off):
    monkeypatch.setenv("NOX_CONFIG_STORE", off)
    core, main, _ = _core(monkeypatch, tmp_path)
    startup.start_shadow(core)
    assert main.cfg.cache_style is None and main.cfg.vision is None
    assert make_adapter(core.cfg.primary).cfg.cache_style is None
    assert core.settings is not None, "只关档案，库和 P1 视图照旧"


def test_红线_对账不过_不让库说话(monkeypatch, tmp_path):
    core, main, _ = _core(monkeypatch, tmp_path)
    real = shadow.compare

    def broken(*a, **kw):
        r = real(*a, **kw)
        r.diffs.append(shadow.Diff("providers.deepseek.base_url", "a", "b"))
        return r

    monkeypatch.setattr(shadow, "compare", broken)
    startup.start_shadow(core)
    assert main.cfg.cache_style is None
    assert make_adapter(core.cfg.primary).cfg.cache_style is None


def test_上一次装的档案_这次失败要卸掉(monkeypatch, tmp_path):
    core, _, _ = _core(monkeypatch, tmp_path)
    startup.start_shadow(core)
    assert adapters._PROFILE is not None
    monkeypatch.setenv("NOX_CONFIG_STORE", "off")
    startup.start_shadow(core)
    assert adapters._PROFILE is None


# ---- 看图的决定点：Nox._see ------------------------------------------------------


def test_红线_see_听档案的_能看就不替他看(monkeypatch, tmp_path):
    import nox as nox_mod

    cfg, s = _seeded(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(nox_mod.vision, "describe", lambda *a, **kw: called.append(1) or "一块红色")
    monkeypatch.setattr(nox_mod.vision, "wrap", lambda d, n: f"[图：{d}]")

    def see(llm):
        fake = SimpleNamespace(adapter_for=lambda m: None,
                               loop=SimpleNamespace(adapter=OpenAICompatAdapter(llm)), cfg=cfg)
        return nox_mod.Nox._see(fake, "看看", [IMG], None)

    assert see(profile.apply(s, _llm("v4-flash", cfg))) == ("看看", [IMG]) and not called
    text, imgs = see(profile.apply(s, _llm("v4-pro", cfg)))
    assert imgs is None and "一块红色" in text and called, "v4-pro 看不见，要先让识图模型看"
