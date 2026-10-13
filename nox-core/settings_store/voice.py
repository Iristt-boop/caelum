"""声音链（配置层 P4，2026-10-13）：Call / Live 两条链用哪几家、什么顺序，她在 Models 页改。

糖糖 10-13 定：
  · **按场景分两条，不分手机电脑**：Call = 打电话（ElevenLabs 打头），Live = OS 实时语音（豆包打头）
  · 只管「用哪几家、什么顺序」，音色不动（音色不怎么换）

规矩（`bridge/lib/tts-chain.js` 那段话是这一节的法律）：**决定属于她的耳朵**。
页面上不在链上的引擎要她**试听过**才能「加入」；跑通了 ≠ 该当默认。
「一段话中间不换声音」「降级时同一家网络断了整档跳过」这些**留在 bridge 代码里**，这里只决定链上有谁。

真源在这里（Core），bridge 每次合成前读（缓存 30 秒），读不到就按它代码里的默认走 —— 永远不会因为配置读不到而没声音。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from settings_store.store import SettingsStore, _now

logger = logging.getLogger("settings_store.voice")

#: bridge 里接好了的引擎（加一家新的要写代码，不能在页面上凭空加）
ENGINES = [
    {"id": "eleven-v4", "label": "ElevenLabs v4", "note": "情绪标签最全，英语陪练靠它"},
    {"id": "eleven-v3", "label": "ElevenLabs v3", "note": "v4 之前一直在用的那个"},
    {"id": "eleven-turbo", "label": "ElevenLabs Turbo", "note": "快，但不认情绪标签"},
    {"id": "doubao", "label": "豆包（复刻声音）", "note": "OS 实时语音一直在听的那个；在 Live 里打头时走流式，首句更快"},
    {"id": "edge", "label": "Edge 朗读", "note": "免费兜底，陌生的声音"},
]
ENGINE_IDS = tuple(e["id"] for e in ENGINES)

#: 和 10-13 之前的行为一模一样（bridge/lib/tts-chain.js 的 CHAINS）
DEFAULTS = {
    "call": ["eleven-v4", "eleven-v3", "eleven-turbo", "doubao", "edge"],
    "live": ["doubao", "eleven-v4", "eleven-v3", "eleven-turbo", "edge"],
}
LABELS = {"call": "Call（打电话）", "live": "Live（OS 实时语音）"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_chains(
  name     TEXT PRIMARY KEY,       -- call | live
  engines  TEXT NOT NULL,          -- JSON 有序列表
  at       TEXT NOT NULL
);
"""


class VoiceError(ValueError):
    def __init__(self, msg: str, status: int = 400) -> None:
        super().__init__(msg)
        self.status = status


def _ensure(store: SettingsStore) -> None:
    with store._lock:
        store._conn.executescript(SCHEMA)


def _check_name(name: str) -> str:
    if name not in DEFAULTS:
        raise VoiceError(f"没有这条声音链：{name}", status=404)
    return name


def _saved(store: SettingsStore, name: str) -> list[str] | None:
    _ensure(store)
    with store._lock:
        r = store._conn.execute("SELECT engines FROM voice_chains WHERE name=?", (name,)).fetchone()
    return json.loads(r["engines"]) if r else None


def chain(store: SettingsStore | None, name: str) -> list[str]:
    """这条链现在是什么（她改过用她的，没改过用默认）。认不得的引擎名过滤掉（代码里删了某一家时不至于卡死）。"""
    _check_name(name)
    saved = _saved(store, name) if store is not None else None
    if not saved:
        return list(DEFAULTS[name])
    kept = [e for e in saved if e in ENGINE_IDS]
    return kept or list(DEFAULTS[name])


def _history(store: SettingsStore, name: str) -> list[dict[str, Any]]:
    with store._lock:
        rows = store._conn.execute(
            "SELECT id, snapshot_json FROM slot_history WHERE slot=? ORDER BY id DESC LIMIT 20",
            (f"voice.{name}",)).fetchall()
    return [{"id": r["id"], **json.loads(r["snapshot_json"])} for r in rows]


def view(store: SettingsStore) -> dict[str, Any]:
    _ensure(store)
    return {
        "engines": ENGINES,
        "chains": {n: {"label": LABELS[n], "engines": chain(store, n), "custom": _saved(store, n) is not None,
                       "default": DEFAULTS[n], "can_revert": bool(_history(store, n))} for n in DEFAULTS},
    }


def _write(store: SettingsStore, name: str, engines: list[str] | None, record: bool = True) -> None:
    prev = _saved(store, name)
    with store.transaction() as c:
        if record:
            c.execute("INSERT INTO slot_history(slot, snapshot_json, at, reason) VALUES(?,?,?,?)",
                      (f"voice.{name}", json.dumps({"engines": prev}), _now(), "她改的"))
        if engines is None:
            c.execute("DELETE FROM voice_chains WHERE name=?", (name,))
        else:
            c.execute("INSERT OR REPLACE INTO voice_chains(name, engines, at) VALUES(?,?,?)",
                      (name, json.dumps(engines), _now()))


def set_chain(store: SettingsStore, name: str, engines: list[str] | None) -> list[str]:
    """她改了顺序 / 加了 / 移出了。`None` = 回到默认。至少留一家，不许重复，只许接好了的那几家。"""
    _check_name(name)
    _ensure(store)
    if engines is not None:
        engines = [str(e) for e in engines]
        bad = [e for e in engines if e not in ENGINE_IDS]
        if bad:
            raise VoiceError(f"这几家没接好：{'、'.join(bad)}（加新的一家要写代码）")
        if len(set(engines)) != len(engines):
            raise VoiceError("同一家不能在链上出现两次")
        if not engines:
            raise VoiceError("至少留一家 —— 不然这条路上我就哑了")
        if engines == DEFAULTS[name]:
            engines = None                      # 和默认一样就当没改（默认以后随代码更新时她也跟着）
    if engines == _saved(store, name):
        return chain(store, name)
    _write(store, name, engines)
    logger.info("配置审计：她把声音链 %s 改成 %s", name, " → ".join(engines or DEFAULTS[name]))
    return chain(store, name)


def revert(store: SettingsStore, name: str) -> list[str]:
    _check_name(name)
    _ensure(store)
    hist = _history(store, name)
    if not hist:
        raise VoiceError("没有上一个可以回了")
    _write(store, name, hist[0].get("engines"), record=False)
    with store.transaction() as c:
        c.execute("DELETE FROM slot_history WHERE id=?", (hist[0]["id"],))
    logger.info("配置审计：她把声音链 %s 换回了上一个", name)
    return chain(store, name)
