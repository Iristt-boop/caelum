"""迁移包带上她自己的选择（R-4 补，2026-10-29）。

10-29 核对：每个能改的值都只有一处可写，但迁移包只带厂商 / 型号 / key ——
搬家后她的槽位、声音链、聊天模型、思考开关全回默认。「Caelum 是载体，可迁移性优先」。
"""

from __future__ import annotations

from settings_store import migrate, slots, voice
from settings_store.keys import FILENAME, KeyStore
from settings_store.seed import seed
from settings_store.store import SettingsStore

from tests.settings_helpers import make_cfg
from tests.test_config_keys_migrate import PASS, _with_user_rows


class Prefs:
    """假的 core：只有迁移包会碰的那两个入口。"""

    def __init__(self, ok: bool = True):
        self.chat_model, self.thinking, self.ok = None, None, ok

    def set_chat_model(self, key):
        self.chat_model = key
        return self.ok

    def set_thinking(self, on):
        self.thinking = on
        return self.ok


def _machine(monkeypatch, root):
    cfg = make_cfg(monkeypatch)
    s = SettingsStore(root / "settings.db")
    seed(s, cfg)
    return cfg, s, KeyStore(root / FILENAME)


def _old_machine(monkeypatch, tmp_path):
    """她现在这台：加了中转 + gemini-3（能看视频），视频位选了它，Call 链改过，聊天用 gem3，思考开着。"""
    cfg, s, ks = _machine(monkeypatch, tmp_path / "a")
    _with_user_rows(s)
    ks.set("relay", "KEY-MARK-RELAY-000")
    gem = next(o for o in slots.options(s, ks, "video") if o["model_name"] == "gemini-3")
    slots.set_choice(s, ks, "video", gem["model_id"])
    voice.set_chain(s, "call", ["doubao", "eleven-v4"])
    return cfg, s, ks


def _pack(s, ks):
    return migrate.open_package(
        migrate.export_package(s, ks, PASS, prefs={"chat_model": "gem3", "thinking": True}), PASS)


def test_红线_往返_四样选择到了新机器都在(monkeypatch, tmp_path):
    _, s, ks = _old_machine(monkeypatch, tmp_path)
    data = _pack(s, ks)

    _, s2, ks2 = _machine(monkeypatch, tmp_path / "b")
    prefs = Prefs()
    result = migrate.apply_package(s2, ks2, data, prefs=prefs)

    assert result["choices"] == 4 and result["skipped"] == []
    c = slots.choice(s2, "video")
    assert c is not None and c["model"]["model_name"] == "gemini-3"
    assert voice.chain(s2, "call") == ["doubao", "eleven-v4"]
    assert voice.chain(s2, "live") == voice.DEFAULTS["live"], "没改过的链别动"
    assert prefs.chat_model == "gem3" and prefs.thinking is True
    # 换回去照样能用：导入走的是原来的写入口，记了上一个
    assert slots.revert(s2, "video") is None and slots.choice(s2, "video") is None


def test_预览和导入说的是同一件事(monkeypatch, tmp_path):
    _, s, ks = _old_machine(monkeypatch, tmp_path)
    data = _pack(s, ks)
    _, s2, ks2 = _machine(monkeypatch, tmp_path / "b")
    pv = migrate.preview(s2, data)
    whats = {c["what"]: c for c in pv["choices"]}
    assert set(whats) == {"视频", "Call（打电话）", "聊天模型", "思考"}
    assert all(c["ok"] for c in pv["choices"])
    assert pv["slots"]["note"] == "", "新包不该再说「槽位等 P3」"


def test_型号这边没有_跳过并说出来_不猜(monkeypatch, tmp_path):
    _, s, ks = _old_machine(monkeypatch, tmp_path)
    data = _pack(s, ks)
    # 包里把她加的那家和型号拿掉：新机器上 gemini-3 根本不会存在
    data["providers"] = [p for p in data["providers"] if p["id"] != "relay"]
    data["models"] = [m for m in data["models"] if m["provider_id"] != "relay"]
    _, s2, ks2 = _machine(monkeypatch, tmp_path / "b")

    pv = {c["what"]: c for c in migrate.preview(s2, data)["choices"]}
    assert pv["视频"]["ok"] is False and "跳过" in pv["视频"]["action"]
    assert pv["聊天模型"]["ok"] is False

    prefs = Prefs()
    result = migrate.apply_package(s2, ks2, data, prefs=prefs)
    assert set(result["skipped"]) == {"视频", "聊天模型"}
    assert slots.choice(s2, "video") is None and prefs.chat_model is None
    assert prefs.thinking is True and voice.chain(s2, "call") == ["doubao", "eleven-v4"], "对得上的照收"


def test_声音链里这边没接的那家去掉_全没接就跳过(monkeypatch, tmp_path):
    _, s, ks = _machine(monkeypatch, tmp_path / "b")
    data = {"choices": {"voice": {"call": ["future-tts", "doubao"], "live": ["future-tts"]}}}
    pv = {c["key"]: c for c in migrate.plan_choices(s, data)}
    assert pv["call"]["ok"] and pv["call"]["engines"] == ["doubao"] and "future-tts" in pv["call"]["action"]
    assert pv["live"]["ok"] is False


def test_旧包没有这一栏_照样能导_预览说清楚(monkeypatch, tmp_path):
    _, s, ks = _old_machine(monkeypatch, tmp_path)
    data = _pack(s, ks)
    del data["choices"]
    _, s2, ks2 = _machine(monkeypatch, tmp_path / "b")
    pv = migrate.preview(s2, data)
    assert pv["choices"] == [] and "旧版" in pv["slots"]["note"]
    result = migrate.apply_package(s2, ks2, data, prefs=Prefs())
    assert result["providers"] == 1 and result["choices"] == 0


def test_存不上的选择算跳过_不假装导好了(monkeypatch, tmp_path):
    _, s, ks = _old_machine(monkeypatch, tmp_path)
    data = _pack(s, ks)
    _, s2, ks2 = _machine(monkeypatch, tmp_path / "b")
    result = migrate.apply_package(s2, ks2, data, prefs=Prefs(ok=False))
    assert set(result["skipped"]) == {"聊天模型", "思考"} and result["choices"] == 2


def test_接口_导出带上她在_attention_库里的两样_导入写回_core(monkeypatch, tmp_path):
    """从使用方断言（verify-from-the-consumer-side）：包里真有、导入时 core 真被叫到。"""
    from tests.test_config_keys_migrate import _client

    c, nox = _client(monkeypatch, tmp_path)
    store = SettingsStore(tmp_path / "settings.db")
    short = next(m["short_name"] for m in store.models() if m["short_name"])
    calls = []
    nox.chat_model_key = lambda: short
    nox.thinking_on = lambda: True
    nox.set_chat_model = lambda k: calls.append(("chat", k)) or True
    nox.set_thinking = lambda on: calls.append(("think", on)) or True

    b64 = c.post("/api/nox/config/export", json={"passphrase": PASS, "passphrase2": PASS}).json()["file_b64"]
    import base64
    data = migrate.open_package(base64.b64decode(b64), PASS)
    assert data["choices"]["thinking"] is True and data["choices"]["chat_model"]["key"] == short

    pv = c.post("/api/nox/config/import/preview", json={"file_b64": b64, "passphrase": PASS}).json()
    assert {x["what"] for x in pv["choices"]} == {"聊天模型", "思考"}
    imp = c.post("/api/nox/config/import", json={"file_b64": b64, "passphrase": PASS}).json()
    assert imp["choices"] == 2 and imp["skipped"] == []
    assert ("chat", short) in calls and ("think", True) in calls
