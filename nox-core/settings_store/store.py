"""配置层的存储类。线程安全同 `orders/store.py`：一把锁串行化，`check_same_thread=False`。

只读写自己这一个库（R3：nox-core 不直读别的服务的库；这是 Core 自己的）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from settings_store.schema import SCHEMA, SCHEMA_VERSION

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SettingsStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(k, v) VALUES('schema_version', ?)", (SCHEMA_VERSION,))
            self._conn.commit()

    # ------------------------------------------------------------ 事务

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """一段要么全成、要么全不成的写。种子刷新用它：别让库停在「删了一半」。"""
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------ 读

    def providers(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM providers ORDER BY id").fetchall()
        return [self._provider(r) for r in rows]

    def provider(self, pid: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
        return self._provider(r) if r else None

    def models(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM models ORDER BY id").fetchall()
        return [self._model(r) for r in rows]

    def model(self, model_id: int) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
        return self._model(r) if r else None

    def slot(self, slot: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute("SELECT * FROM slots WHERE slot=?", (slot,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["targets"] = json.loads(d["targets"])
        return d

    def slots(self) -> list[dict[str, Any]]:
        with self._lock:
            names = [r["slot"] for r in self._conn.execute("SELECT slot FROM slots ORDER BY slot")]
        return [s for s in (self.slot(n) for n in names) if s]

    def integrity_ok(self) -> bool:
        with self._lock:
            row = self._conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"

    # ------------------------------------------------------------ 影子账本

    def log_shadow(self, *, ok: bool, n_checked: int, n_diff: int,
                   seed_added: int, detail: list[dict[str, Any]]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO shadow_log(at, ok, n_checked, n_diff, seed_added, detail)"
                " VALUES(?,?,?,?,?,?)",
                (_now(), 1 if ok else 0, n_checked, n_diff, seed_added,
                 json.dumps(detail, ensure_ascii=False)))
            self._conn.commit()

    def shadow_summary(self, days: int = 7) -> dict[str, Any]:
        """一周观察期的验收口径：跑了几次、几次不一致。"""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS runs, COALESCE(SUM(1-ok),0) AS bad,"
                " COALESCE(SUM(n_diff),0) AS diffs, MIN(at) AS first, MAX(at) AS last"
                " FROM shadow_log WHERE at>=?", (since,)).fetchone()
        return {"runs": row["runs"], "bad_runs": int(row["bad"]), "diffs": int(row["diffs"]),
                "first": row["first"], "last": row["last"]}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ 行 → dict

    @staticmethod
    def _provider(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["key_envs"] = json.loads(d["key_envs"])
        return d

    @staticmethod
    def _model(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["capabilities"] = json.loads(d["capabilities"])
        d["price"] = json.loads(d["price_json"]) if d.get("price_json") else None
        return d
