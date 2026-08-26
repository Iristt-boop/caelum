"""World Model 的落盘层（SQLite）。

结构和 `data/store.py`、`attention/store.py` 一致：一把锁 + WAL + 全同步
（`context/base.py:25` 的硬约束：不许混 async）。

## 两张表，职责不同

    observations   原始事实。**只追加，永不改写、永不删除**
    states         当前状态。派生的，随时可以从 observations 重算

⚠️ **states 表是缓存性质的。** 哪天判断规则变了，删掉整张表、
从 observations 重跑一遍就行 —— 这正是「派生可重算」的意思。
observations 丢了才是真的丢了。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from world_model.types import Observation, State

logger = logging.getLogger(__name__)

_SCHEMA = """
-- 原始事实。只追加。
CREATE TABLE IF NOT EXISTS observations (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    type         TEXT NOT NULL,
    observed     TEXT NOT NULL,      -- JSON
    observed_at  TEXT NOT NULL,
    ingested_at  TEXT NOT NULL,
    confidence   REAL NOT NULL DEFAULT 1.0,
    -- 同一件事的幂等键（如某天的睡眠）。UNIQUE 让重复写入直接被库挡掉，
    -- 不用在调用方写「先查再写」那种有竞态的逻辑
    dedup_key    TEXT UNIQUE
);

-- 按类型+时间查是最主要的用法（趋势反查），这个索引是必须的
CREATE INDEX IF NOT EXISTS idx_obs_type_time
    ON observations(type, observed_at DESC);

-- 当前状态。派生数据，可从 observations 重算
CREATE TABLE IF NOT EXISTS states (
    type         TEXT PRIMARY KEY,
    value        TEXT NOT NULL,      -- JSON
    observed_at  TEXT NOT NULL,
    source       TEXT NOT NULL,
    based_on     TEXT NOT NULL,      -- JSON 数组，追溯用
    confidence   REAL NOT NULL DEFAULT 1.0
);
"""


class WorldStore:
    """observations / states 的落盘。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()
        logger.info("World Model 库就绪：%s", self.path)

    # ------------------------------------------------------------ 写

    def add_observation(self, obs: Observation) -> bool:
        """存一条事实。返回 False 表示 dedup_key 撞了（已经存过，不是错误）。

        ⚠️ 用 `INSERT OR IGNORE` 而不是「先查再插」—— 后者在
        Attention 心跳和手动 tick 撞车时会重复写。
        """
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO observations"
                "(id, source, type, observed, observed_at, ingested_at, confidence, dedup_key)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (obs.id, obs.source, obs.type,
                 json.dumps(obs.observed, ensure_ascii=False),
                 obs.observed_at.isoformat(), obs.ingested_at.isoformat(),
                 obs.confidence, obs.dedup_key),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def put_state(self, st: State) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO states"
                "(type, value, observed_at, source, based_on, confidence)"
                " VALUES(?,?,?,?,?,?)",
                (st.type, json.dumps(st.value, ensure_ascii=False),
                 st.observed_at.isoformat(), st.source,
                 json.dumps(st.based_on), st.confidence),
            )
            self._conn.commit()

    # ------------------------------------------------------------ 读

    def get_state_row(self, type_: str) -> State | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM states WHERE type = ?", (type_,)
            ).fetchone()
        if row is None:
            return None
        return State(
            type=row["type"],
            value=json.loads(row["value"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            source=row["source"],
            based_on=json.loads(row["based_on"]),
            confidence=row["confidence"],
        )

    def recent(self, type_: str, limit: int = 30,
               since: datetime | None = None) -> list[Observation]:
        """某类事实的历史，**新的在前**。趋势反查用的就是它。"""
        sql = "SELECT * FROM observations WHERE type = ?"
        args: list = [type_]
        if since is not None:
            sql += " AND observed_at >= ?"
            args.append(since.isoformat())
        sql += " ORDER BY observed_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            try:
                out.append(Observation(
                    id=r["id"], source=r["source"], type=r["type"],
                    observed=json.loads(r["observed"]),
                    observed_at=datetime.fromisoformat(r["observed_at"]),
                    ingested_at=datetime.fromisoformat(r["ingested_at"]),
                    confidence=r["confidence"], dedup_key=r["dedup_key"],
                ))
            except Exception:  # noqa: BLE001
                logger.exception("observation 行损坏，跳过：%s", r["id"])
        return out

    def stats(self) -> dict:
        with self._lock:
            n_obs = self._conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"]
            n_st = self._conn.execute("SELECT COUNT(*) c FROM states").fetchone()["c"]
            types = [r["type"] for r in self._conn.execute(
                "SELECT DISTINCT type FROM observations").fetchall()]
        return {"observations": n_obs, "states": n_st, "types": types}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
