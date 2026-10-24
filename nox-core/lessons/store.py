"""课程线 + 讲过的卡（lessons.db）。独立的库，和 attention / world 平级。

一张卡的一生：

    pending   早上生成好了，等合适的时候讲
    told      讲了 —— 他回的话里出现了这张卡的要点（`key`），见 attention/lesson_drive.py
    expired   到 21 点还没讲，今天不讲了（不补）

「她回没回」：讲完 30 分钟内她的第一句话记进 reply；30 分钟过了没回就记空串、不再看
（reply_checked=1）。回的是别的事也照记，下次生成让模型自己判断她接没接。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PENDING, TOLD, EXPIRED = "pending", "told", "expired"

#: 节奏（她 10-24：「看重要程度」）。第一步只做 daily；mention（聊到才讲）是第二步
CADENCES = ("daily",)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lines (
    goal_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    cadence     TEXT NOT NULL DEFAULT 'daily',
    level       TEXT DEFAULT '',
    next        TEXT DEFAULT '[]',
    active      INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cards (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id       TEXT NOT NULL,
    date          TEXT NOT NULL,
    title         TEXT NOT NULL,
    key           TEXT NOT NULL,
    body          TEXT NOT NULL,
    hook          TEXT DEFAULT '',
    level_after   TEXT DEFAULT '',
    next_after    TEXT DEFAULT '[]',
    status        TEXT NOT NULL DEFAULT 'pending',
    generated_at  TEXT NOT NULL,
    told_at       TEXT,
    reply         TEXT,
    reply_checked INTEGER DEFAULT 0,
    UNIQUE (goal_id, date)
);
CREATE INDEX IF NOT EXISTS idx_cards_goal ON cards(goal_id, date DESC);
"""


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


class LessonStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------ 课程线

    def upsert_line(self, goal_id: str, title: str, *, cadence: str = "daily",
                    level: str | None = None, next_points: list[str] | None = None,
                    active: bool = True) -> None:
        if cadence not in CADENCES:
            raise ValueError(f"节奏 {cadence!r} 还不支持（现在只有 {', '.join(CADENCES)}）")
        now = _iso()
        with self._lock:
            cur = self._conn.execute("SELECT level, next FROM lines WHERE goal_id = ?", (goal_id,)).fetchone()
            lv = level if level is not None else (cur["level"] if cur else "")
            nx = json.dumps(next_points, ensure_ascii=False) if next_points is not None else (cur["next"] if cur else "[]")
            self._conn.execute(
                "INSERT INTO lines(goal_id, title, cadence, level, next, active, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(goal_id) DO UPDATE SET "
                "title=excluded.title, cadence=excluded.cadence, level=excluded.level, "
                "next=excluded.next, active=excluded.active, updated_at=excluded.updated_at",
                (goal_id, title, cadence, lv, nx, 1 if active else 0, now, now))
            self._conn.commit()

    def get_line(self, goal_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM lines WHERE goal_id = ?", (goal_id,)).fetchone()
        return self._line(row) if row else None

    def lines(self, cadence: str | None = None) -> list[dict]:
        sql = "SELECT * FROM lines WHERE active = 1"
        args: tuple = ()
        if cadence:
            sql += " AND cadence = ?"
            args = (cadence,)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY created_at", args).fetchall()
        return [self._line(r) for r in rows]

    @staticmethod
    def _line(row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["next"] = json.loads(d.get("next") or "[]")
        except ValueError:
            d["next"] = []
        return d

    # ------------------------------------------------------------ 卡

    def card_on(self, goal_id: str, date: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM cards WHERE goal_id = ? AND date = ?",
                                     (goal_id, date)).fetchone()
        return dict(row) if row else None

    def add_card(self, goal_id: str, date: str, card: dict[str, Any],
                 now: datetime | None = None) -> dict | None:
        """今天这张。同一个目标一天只有一张（UNIQUE）—— 撞了返回已有那张。"""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO cards(goal_id, date, title, key, body, hook, level_after, next_after, "
                    "status, generated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (goal_id, date, card["title"], card["key"], card["body"], card.get("hook", ""),
                     card.get("level", ""), json.dumps(card.get("next") or [], ensure_ascii=False),
                     PENDING, _iso(now)))
                self._conn.commit()
            except sqlite3.IntegrityError:
                logger.info("小课堂：%s 今天已经有一张了，不重复写", date)
        return self.card_on(goal_id, date)

    def pending(self, date: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM cards WHERE date = ? AND status = ?",
                                      (date, PENDING)).fetchall()
        return [dict(r) for r in rows]

    def mark_told(self, card_id: int, at: datetime) -> None:
        """讲了 → 卡记 told，课程线按这张卡回的「讲完之后她在哪 / 接下来讲什么」往前挪。"""
        with self._lock:
            row = self._conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
            if row is None or row["status"] != PENDING:
                return
            self._conn.execute("UPDATE cards SET status = ?, told_at = ? WHERE id = ?",
                               (TOLD, _iso(at), card_id))
            sets, vals = [], []
            if row["level_after"]:
                sets.append("level = ?")
                vals.append(row["level_after"])
            if row["next_after"] and row["next_after"] != "[]":
                sets.append("next = ?")
                vals.append(row["next_after"])
            if sets:
                self._conn.execute(f"UPDATE lines SET {', '.join(sets)}, updated_at = ? WHERE goal_id = ?",
                                   (*vals, _iso(), row["goal_id"]))
            self._conn.commit()

    def expire_through(self, date: str) -> int:
        """这一天（含）之前还没讲的 → expired，不补。返回改了几张。"""
        with self._lock:
            cur = self._conn.execute("UPDATE cards SET status = ? WHERE status = ? AND date <= ?",
                                     (EXPIRED, PENDING, date))
            self._conn.commit()
            return cur.rowcount

    def awaiting_reply(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM cards WHERE status = ? AND reply_checked = 0",
                                      (TOLD,)).fetchall()
        return [dict(r) for r in rows]

    def set_reply(self, card_id: int, reply: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE cards SET reply = ?, reply_checked = 1 WHERE id = ?",
                               (reply, card_id))
            self._conn.commit()

    def recent(self, goal_id: str, n: int = 15) -> list[dict]:
        """讲过的（told），新的在前。生成时拿来「这些讲过了，别重复」。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM cards WHERE goal_id = ? AND status = ? ORDER BY date DESC LIMIT ?",
                (goal_id, TOLD, n)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
