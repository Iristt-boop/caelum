"""话题池的存储 —— topics（池子）+ candidates（候选缓存）两张表。

独立的小 SQLite（`topics.db`），和 world.db 分库，理由同 world_model：
写入门槛不同。事实（world）永久保留，话题（这里）**天生短命**——
TTL 一到就 expired，谁也不该拿「留档」的名义把它救回来。

## 状态机（文档 §2.3）

    open → surfaced → followed / dismissed
       └──────────────→ expired（自然过期）

「推过了」（surfaced）和「真产生兴趣」（followed）是两件事，拆开记。
surfaced 由 UI/hook 写；followed / dismissed 由人点。这里只管存。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 各类话题的保质期（小时）。文档 §2.4：别全用统一的 24~48h ——
#: 24 小时后的 HN 讨论可能已经没意思，一周后的 arXiv 依然可看
TTL_HOURS: dict[str, int] = {
    "ai": 36,
    "weird": 36,
    "film": 48,
    "music": 48,
    "opensource": 72,
    "science": 24 * 7,
    "art": 24 * 7,
    "design": 24 * 7,
    "books": 24 * 7,
}
DEFAULT_TTL_HOURS = 48
#: shared 枝条（一起看完一场、读完一本）—— 过两天就不好再开口了
SHARED_TTL_HOURS = 48

#: 人工决策只允许这两个状态。surfaced 是 UI/hook 写的，不从这条口进
HUMAN_STATUSES = ("followed", "dismissed")


def ttl_for(category: str) -> timedelta:
    return timedelta(hours=TTL_HOURS.get(category, DEFAULT_TTL_HOURS))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class Topic:
    """池子里的一条话题。字段就是文档 §2 那张表。"""

    hook: str                       # 材料事实 + 一个值得继续看的点
    source_title: str = ""
    source_url: str = ""
    category: str = ""
    origin: str = "external"        # external / shared / conversation / world / memory-triggered
    why_this: list[str] = field(default_factory=list)
    relevance: float = 0.5
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    status: str = "open"
    id: str = field(default_factory=lambda: f"topic_{uuid.uuid4().hex[:12]}")

    def to_dict(self, now: datetime) -> dict:
        return {
            "id": self.id,
            "hook": self.hook,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "category": self.category,
            "origin": self.origin,
            "why_this": self.why_this,
            "relevance": self.relevance,
            "observed_at": _iso(self.observed_at),
            "expires_at": _iso(self.expires_at) if self.expires_at else None,
            "status": self.status,
            "fresh": self.expires_at is None or self.expires_at > now,
        }


@dataclass
class Candidate:
    """Scout 抓回来的原始候选。进 Filter 前先落一份短命缓存。"""

    source_id: str                  # hn:38123 / gh:owner/repo / arxiv:2401.12345 / gnews:<hash>
    title: str
    url: str
    source: str                     # hackernews / github / arxiv / googlenews
    category: str                   # 抓取方向（粗的，Filter 会重判）
    summary: str = ""
    published_at: datetime | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    hook TEXT NOT NULL,
    source_title TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    category TEXT DEFAULT '',
    origin TEXT DEFAULT 'external',
    why_this TEXT DEFAULT '[]',
    relevance REAL DEFAULT 0.5,
    observed_at TEXT NOT NULL,
    expires_at TEXT,
    status TEXT DEFAULT 'open',
    dedup_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_topics_status ON topics(status, expires_at);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT UNIQUE,
    title TEXT DEFAULT '',
    url TEXT DEFAULT '',
    source TEXT DEFAULT '',
    category TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    published_at TEXT,
    fetched_at TEXT NOT NULL
);
"""


class TopicStore:
    """topics.db 的读写。错误一律抛给上层 —— 池子坏了不该静默。

    ⚠️ 连接跨线程用（Scout 循环在 `to_thread` 的工人线程里跑，API 路由
    在 FastAPI 的线程池里跑，同一个库）：照 `world_model/store.py` 的方子
    —— `check_same_thread=False` + 每个操作持锁 + WAL。少哪一样，
    要么直接 ProgrammingError（2026-08-31 上线头一轮就栽在这），
    要么两边同时写时静默坏账。
    """

    def __init__(self, path: str | Path) -> None:
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(_SCHEMA)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ 候选

    def add_candidate(self, c: Candidate) -> bool:
        """source_id 撞了就是抓过 —— 返回 False，不是错误。"""
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO candidates"
                " (source_id, title, url, source, category, summary, published_at, fetched_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (c.source_id, c.title, c.url, c.source, c.category, c.summary,
                 _iso(c.published_at) if c.published_at else None, _iso(c.fetched_at)),
            )
            self.conn.commit()
        return cur.rowcount > 0

    def recent_candidates(self, max_age: timedelta, limit: int = 200) -> list[Candidate]:
        since = _iso(datetime.now(timezone.utc) - max_age)
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM candidates WHERE fetched_at >= ?"
                " ORDER BY id DESC LIMIT ?", (since, limit),
            ).fetchall()
        return [self._candidate(r) for r in rows]

    def prune_candidates(self, max_age: timedelta) -> int:
        """候选缓存天生短命（文档 §3.3：只是重筛的保险，不是档案）。"""
        since = _iso(datetime.now(timezone.utc) - max_age)
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM candidates WHERE fetched_at < ?", (since,))
            self.conn.commit()
        return cur.rowcount

    @staticmethod
    def _candidate(row: sqlite3.Row) -> Candidate:
        return Candidate(
            source_id=row["source_id"], title=row["title"], url=row["url"],
            source=row["source"], category=row["category"],
            summary=row["summary"], published_at=_parse(row["published_at"]),
            fetched_at=_parse(row["fetched_at"]) or datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------ 话题

    def add_topic(self, topic: Topic, dedup_key: str) -> bool:
        """同一来源 / 同一场经历只进池子一次。撞了返回 False。"""
        expires = topic.expires_at or (
            topic.observed_at + ttl_for(topic.category))
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO topics"
                " (id, hook, source_title, source_url, category, origin, why_this,"
                "  relevance, observed_at, expires_at, status, dedup_key)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (topic.id, topic.hook, topic.source_title, topic.source_url,
                 topic.category, topic.origin, json.dumps(topic.why_this, ensure_ascii=False),
                 topic.relevance, _iso(topic.observed_at), _iso(expires),
                 topic.status, dedup_key),
            )
            self.conn.commit()
        return cur.rowcount > 0

    def open_topics(self, now: datetime, *, include_surfaced: bool = True,
                    limit: int = 50) -> list[Topic]:
        """池子里的活话题：没过期、没人理过。新的、相关的在前。"""
        statuses = "('open','surfaced')" if include_surfaced else "('open')"
        with self._lock:
            rows = self.conn.execute(
                f"SELECT * FROM topics WHERE status IN {statuses}"
                " AND expires_at > ? ORDER BY relevance DESC, observed_at DESC"
                " LIMIT ?", (_iso(now), limit),
            ).fetchall()
        return [self._topic(r) for r in rows]

    def mark(self, topic_id: str, status: str) -> bool:
        """人工决策：followed / dismissed。不存在或已是终态返回 False。"""
        if status not in HUMAN_STATUSES:
            raise ValueError(f"只能人工设 {HUMAN_STATUSES}，不是 {status!r}")
        with self._lock:
            cur = self.conn.execute(
                "UPDATE topics SET status=? WHERE id=? AND status IN ('open','surfaced')",
                (status, topic_id))
            self.conn.commit()
        return cur.rowcount > 0

    def mark_surfaced(self, topic_ids: list[str]) -> None:
        """hook / UI 展示过就记一笔 —— 默认不循环出现（文档 §4.1）。"""
        if not topic_ids:
            return
        marks = ",".join("?" * len(topic_ids))
        with self._lock:
            self.conn.execute(
                f"UPDATE topics SET status='surfaced'"
                f" WHERE id IN ({marks}) AND status='open'", topic_ids)
            self.conn.commit()

    def expire(self, now: datetime) -> int:
        """到点的 open/surfaced 转 expired。followed/dismissed 是人的决定，不动。"""
        with self._lock:
            cur = self.conn.execute(
                "UPDATE topics SET status='expired'"
                " WHERE status IN ('open','surfaced') AND expires_at <= ?", (_iso(now),))
            self.conn.commit()
        return cur.rowcount

    def stats(self) -> dict:
        with self._lock:
            rows = self.conn.execute(
                "SELECT status, COUNT(*) AS n FROM topics GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    @staticmethod
    def _topic(row: sqlite3.Row) -> Topic:
        try:
            why = json.loads(row["why_this"] or "[]")
        except (ValueError, TypeError):
            why = []
        return Topic(
            id=row["id"], hook=row["hook"],
            source_title=row["source_title"], source_url=row["source_url"],
            category=row["category"], origin=row["origin"], why_this=why,
            relevance=row["relevance"],
            observed_at=_parse(row["observed_at"]) or datetime.now(timezone.utc),
            expires_at=_parse(row["expires_at"]), status=row["status"],
        )
