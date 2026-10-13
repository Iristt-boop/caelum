"""配置层 P4：声音链 Call / Live（2026-10-13，她定：按场景分、不分手机电脑；只管用哪几家和顺序）。

bridge 那一侧（真按这条链合成、读不到走默认）在 bridge/test/voice-chains.test.js。
"""

from __future__ import annotations

import pytest

from settings_store import voice
from settings_store.store import SettingsStore


@pytest.fixture
def store(tmp_path):
    return SettingsStore(tmp_path / "settings.db")


def test_默认就是10_13之前的样子(store):
    assert voice.chain(store, "call") == ["eleven-v4", "eleven-v3", "eleven-turbo", "doubao", "edge"]
    assert voice.chain(store, "live")[0] == "doubao"
    v = voice.view(store)
    assert v["chains"]["call"]["custom"] is False and v["chains"]["call"]["can_revert"] is False
    assert {e["id"] for e in v["engines"]} == set(voice.ENGINE_IDS)


def test_改顺序_移出_换回去一步步退(store):
    voice.set_chain(store, "call", ["eleven-v3", "eleven-v4", "edge"])
    assert voice.chain(store, "call") == ["eleven-v3", "eleven-v4", "edge"]
    voice.set_chain(store, "call", ["edge"])
    assert voice.revert(store, "call") == ["eleven-v3", "eleven-v4", "edge"]
    assert voice.revert(store, "call") == voice.DEFAULTS["call"]
    with pytest.raises(voice.VoiceError, match="没有上一个"):
        voice.revert(store, "call")
    assert voice.chain(store, "live") == voice.DEFAULTS["live"], "改 Call 不许动 Live"


@pytest.mark.parametrize("engines,msg", [
    (["qwen", "edge"], "没接好"),
    (["edge", "edge"], "两次"),
    ([], "至少留一家"),
])
def test_不合规的拒收(store, engines, msg):
    with pytest.raises(voice.VoiceError, match=msg):
        voice.set_chain(store, "live", engines)


def test_改回和默认一样就当没改_以后默认随代码更新她也跟着(store):
    voice.set_chain(store, "live", ["edge"])
    voice.set_chain(store, "live", list(voice.DEFAULTS["live"]))
    assert voice.view(store)["chains"]["live"]["custom"] is False


def test_没有这条链(store):
    with pytest.raises(voice.VoiceError) as e:
        voice.set_chain(store, "phone", ["edge"])
    assert e.value.status == 404


def test_库里存着代码已经删掉的引擎_过滤掉_全没了用默认(store):
    with store.transaction() as c:
        c.execute("CREATE TABLE IF NOT EXISTS voice_chains(name TEXT PRIMARY KEY, engines TEXT NOT NULL, at TEXT NOT NULL)")
        c.execute("INSERT INTO voice_chains VALUES('call', '[\"qwen\", \"edge\"]', 't')")
    assert voice.chain(store, "call") == ["edge"]
    with store.transaction() as c:
        c.execute("UPDATE voice_chains SET engines='[\"qwen\"]' WHERE name='call'")
    assert voice.chain(store, "call") == voice.DEFAULTS["call"], "不许空链 —— 空链就是哑巴"


def test_接口_查_改_换回去(monkeypatch, tmp_path):
    from tests.test_settings_views import _client

    c, _ = _client(monkeypatch, tmp_path)
    assert c.get("/api/nox/config/voice").json()["chains"]["live"]["engines"][0] == "doubao"
    r = c.put("/api/nox/config/voice/live", json={"engines": ["eleven-v4", "doubao", "edge"]}).json()
    assert r["chains"]["live"]["engines"] == ["eleven-v4", "doubao", "edge"] and r["chains"]["live"]["can_revert"]
    assert c.put("/api/nox/config/voice/live", json={"engines": ["qwen"]}).status_code == 400
    r = c.post("/api/nox/config/voice/live/revert").json()
    assert r["chains"]["live"]["engines"] == voice.DEFAULTS["live"]
    assert c.put("/api/nox/config/voice/live", json={"engines": None}).status_code == 200
