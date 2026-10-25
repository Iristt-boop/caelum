"""装着的 skill（skills.db）。独立的库，和 lessons / orders 平级。

    skills     每个 skill 一行：当前那一版的内容 + 开没开 + 从哪来
    versions   每装一次 / 改一次留一份原文，改坏了能退回（照 Creations）
    uses       命中、翻阅各记一笔 —— 页面上「最近 7 天用了几次」和上线后数漏判都靠它

来源 `origin`：her（她在页面上装的）/ nox（他提议、她点头的，P3）/ xiaoke（小克写进来的）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from skills.parse import Skill, parse

logger = logging.getLogger(__name__)

ORIGINS = ("her", "nox", "xiaoke")
USE_KINDS = ("hit", "read")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    name         TEXT PRIMARY KEY,
    description  TEXT NOT NULL,
    triggers     TEXT NOT NULL DEFAULT '[]',
    body         TEXT NOT NULL,
    raw          TEXT NOT NULL,
    origin       TEXT NOT NULL,
    source       TEXT DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    version   INTEGER NOT NULL,
    raw       TEXT NOT NULL,
    origin    TEXT NOT NULL,
    saved_at  TEXT NOT NULL,
    UNIQUE (name, version)
);
CREATE TABLE IF NOT EXISTS uses (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL,
    kind  TEXT NOT NULL,
    at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_uses_name ON uses(name, at);
"""


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["triggers"] = json.loads(d.get("triggers") or "[]")
    d["enabled"] = bool(d["enabled"])
    return d


class SkillStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------ 装 / 改

    def install(self, raw: str, *, origin: str, source: str = "") -> tuple[dict[str, Any], str]:
        """装一个，或者给同名的换一版。返回 (这一行, created / updated / unchanged)。

        不合格抛 `skills.parse.SkillError`。同名同内容不新增版本（她点两次「装上」不该多一版）。
        换版本不动开关：她关着的，改完还是关着。
        """
        if origin not in ORIGINS:
            raise ValueError(f"来源 {origin!r} 不认识（只有 {', '.join(ORIGINS)}）")
        sk: Skill = parse(raw)
        raw = raw.replace("\r\n", "\n").strip() + "\n"
        now = _iso()
        with self._lock:
            cur = self._conn.execute("SELECT raw, version FROM skills WHERE name = ?", (sk.name,)).fetchone()
            if cur is not None and cur["raw"] == raw:
                status, version = "unchanged", cur["version"]
            else:
                version = 1 if cur is None else cur["version"] + 1
                status = "created" if cur is None else "updated"
                self._conn.execute(
                    "INSERT INTO skills(name, description, triggers, body, raw, origin, source, enabled, version, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,1,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                    "description=excluded.description, triggers=excluded.triggers, body=excluded.body, raw=excluded.raw, "
                    "origin=excluded.origin, source=excluded.source, version=excluded.version, updated_at=excluded.updated_at",
                    (sk.name, sk.description, json.dumps(list(sk.when), ensure_ascii=False), sk.body, raw,
                     origin, source, version, now, now))
                self._conn.execute("INSERT INTO versions(name, version, raw, origin, saved_at) VALUES(?,?,?,?,?)",
                                   (sk.name, version, raw, origin, now))
                self._conn.commit()
        logger.info("skill %s name=%s v%d origin=%s", status, sk.name, version, origin)
        return self.get(sk.name), status

    def set_enabled(self, name: str, on: bool) -> bool:
        """开 / 关。没有这个名字返回 False。"""
        with self._lock:
            n = self._conn.execute("UPDATE skills SET enabled = ?, updated_at = ? WHERE name = ?",
                                   (1 if on else 0, _iso(), name)).rowcount
            self._conn.commit()
        if n:
            logger.info("skill %s name=%s", "enabled" if on else "disabled", name)
        return bool(n)

    # ------------------------------------------------------------ 读

    def get(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM skills WHERE name = ?", (name,)).fetchone()
        return _row(r) if r else None

    def all(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM skills" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY created_at, name"
        with self._lock:
            return [_row(r) for r in self._conn.execute(q).fetchall()]

    def history(self, name: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT version, raw, origin, saved_at FROM versions WHERE name = ? ORDER BY version DESC", (name,)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ 用量

    def record(self, name: str, kind: str) -> None:
        """记一笔命中 / 翻阅。记账失败不该挡住这一轮回话 —— 留痕后放过。"""
        if kind not in USE_KINDS:
            raise ValueError(f"用量类型 {kind!r} 不认识")
        try:
            with self._lock:
                self._conn.execute("INSERT INTO uses(name, kind, at) VALUES(?,?,?)", (name, kind, _iso()))
                self._conn.commit()
        except sqlite3.Error:
            logger.warning("skill 用量没记上 name=%s kind=%s", name, kind, exc_info=True)

    def uses_since(self, days: int = 7) -> dict[str, dict[str, int]]:
        since = _iso(datetime.now(timezone.utc) - timedelta(days=days))
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, kind, COUNT(*) AS n FROM uses WHERE at >= ? GROUP BY name, kind", (since,)).fetchall()
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["name"], {"hit": 0, "read": 0})[r["kind"]] = r["n"]
        return out
