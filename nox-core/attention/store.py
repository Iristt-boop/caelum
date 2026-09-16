"""Attention 落盘（SQLite）。

## 这个模块存在的唯一理由：重启不能丢

`Concern("糖糖的睡眠") strength 0.72 since 08-04` —— 这是跨了四天的状态。
而 `nox-core` 会重启（PROJECT.md 里专门配了 `nox-core.service.d/deps.conf`
处理重启依赖）。纯内存的 Registry 一重启，Nox 关心了四天的事情全忘光。

架构设计 v1.2 的 Phase 1-5 完全没提存储，v1.3 第 12.6 节补上，就是这一块。

## 为什么是 SQLite 不是 Ombre Brain

Ombre Brain 是**长期记忆**：语义检索、模糊召回、越久越有味道。
Attention 是**工作状态**：要频繁读写、要精确覆盖、要按 subject 精确命中。
两者性质不同，混在一起会让记忆库被高频状态写噪音淹掉。

等 Attention 需要「回忆去年这时候她也睡不好」的时候，再接 Ombre Brain ——
那时候是**读**它，不是拿它当存储。

## 为什么是全量覆盖不是增量

Attention 的数量是个位数到几十条，全量重写一次是毫秒级。
增量更新要处理「registry 里删掉的那条怎么从库里消失」，
多一类能悄悄出错的逻辑，换不到任何看得见的好处。

## 线程安全 / 同步

跟 `data/store.py` 一样：一把锁串行化写，`check_same_thread=False`，WAL。
FastAPI 的同步路由跑在线程池里，不加锁会碰到
"SQLite objects created in a thread can only be used in that same thread"。

不做 async（`context/base.py:25` 第二条硬约束）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from attention.intent import IntentEngine
from attention.registry import AttentionRegistry
from attention.wakeup import WakeBook

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attentions (
    subject      TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    strength     REAL NOT NULL,
    since        TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    decay        TEXT NOT NULL,
    evidence     TEXT NOT NULL
);

-- Temporal Filter 的记忆：「上次看到的是什么状态」。
-- 没有它就没法判断「变化」，每次 poll 都会重新产生一遍事件。
CREATE TABLE IF NOT EXISTS source_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Intent 整包存 JSON，不像 attentions 那样逐列拆。
-- 理由：attentions 要按 subject 查、按 strength 排，拆列才好使；
-- intents 只按 id 存取，而且字段还在演进（M5 要往 action_history 里加东西），
-- 拆列的话每次加字段都要迁移表。
CREATE TABLE IF NOT EXISTS intents (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL
);

-- 他给自己留的纸条（唤醒链）。整包存 JSON，理由同 intents。
-- ⚠️ 这张表必须落盘：纸条丢了是**静默失败** —— 不报错，
-- 只是他到点没醒，而她永远不会知道他本来打算问一句。
CREATE TABLE IF NOT EXISTS wakeups (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""


class AttentionStore:
    """Attention Registry 的落盘存储。

        store = AttentionStore(path)
        registry = store.load()      # 启动时
        ...
        store.save(registry)         # 每次变更之后
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            # 拿不到锁先等 5 秒，别当场抛 `database is locked`。
            #
            # ⚠️ **这一行本身不修任何东西** —— Python 的
            # `sqlite3.connect()` 默认 `timeout=5.0`，已经等价于 5000
            # （2026-09-13 实测：默认 connect 回来就是 5000）。
            # 写在这里只为两件事：
            #   ① 把"这个库要等锁，不许秒失败"变成看得见的意图，
            #      而不是藏在一个库默认值里
            #   ② 万一以后有人给 connect 加了 `timeout=0`，这一行会盖回来
            #
            # 为什么在意：这个库存着「他今天开过几次口」。写失败又恰好重启，
            # 就是她看到的"他翻来覆去说同一件事"（见 service.py 的 `_persist`）。
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.commit()
        logger.info("Attention 库就绪：%s", self.path)

    # ------------------------------------------------------------ 读写

    def load(self) -> AttentionRegistry:
        """把库里的 Attention 全部读回来。

        库是空的就返回空 Registry —— 第一次启动是正常情况，不是错误。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT subject, kind, strength, since, last_updated, decay, evidence "
                "FROM attentions"
            ).fetchall()

        items = []
        for r in rows:
            try:
                items.append({
                    "subject": r["subject"],
                    "kind": r["kind"],
                    "strength": r["strength"],
                    "since": r["since"],
                    "last_updated": r["last_updated"],
                    "decay": r["decay"],
                    "evidence": json.loads(r["evidence"]),
                })
            except Exception:  # noqa: BLE001
                # 一条坏了不该让整个 Registry 起不来 —— 那等于一次
                # 写坏就永久失忆。跳过它，但要吼出来
                logger.exception("Attention 行损坏，跳过：subject=%s", r["subject"])

        reg = AttentionRegistry.from_list(items)
        logger.info("载入 %d 条 Attention", len(reg))
        return reg

    def save(self, registry: AttentionRegistry) -> int:
        """全量覆盖写回。返回写入条数。

        整个操作在一个事务里 —— 中途崩了也不会留下「删了一半」的库。
        """
        rows = [
            (
                a["subject"], a["kind"], a["strength"],
                a["since"], a["last_updated"], a["decay"],
                json.dumps(a["evidence"], ensure_ascii=False),
            )
            for a in registry.to_list()
        ]
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM attentions")
                self._conn.executemany(
                    "INSERT INTO attentions"
                    "(subject, kind, strength, since, last_updated, decay, evidence) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return len(rows)

    # ------------------------------------------------------------ Intent

    def load_intents(self) -> IntentEngine:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM intents").fetchall()
        items = []
        for r in rows:
            try:
                items.append(json.loads(r["data"]))
            except Exception:  # noqa: BLE001
                logger.exception("Intent 行损坏，跳过")
        engine = IntentEngine.from_list(items)
        logger.info("载入 %d 条 Intent", len(engine))
        return engine

    def save_intents(self, engine: IntentEngine) -> int:
        """全量覆盖，理由同 `save()`。"""
        rows = [(i["id"], json.dumps(i, ensure_ascii=False)) for i in engine.to_list()]
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM intents")
                self._conn.executemany(
                    "INSERT INTO intents(id, data) VALUES(?, ?)", rows
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return len(rows)

    # ------------------------------------------------------------ 唤醒纸条

    def load_wakeups(self) -> WakeBook:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM wakeups").fetchall()
        items = []
        for r in rows:
            try:
                items.append(json.loads(r["data"]))
            except Exception:  # noqa: BLE001
                logger.exception("纸条行损坏，跳过")
        book = WakeBook.from_list(items)
        logger.info("载入 %d 张纸条", len(book))
        return book

    def save_wakeups(self, book: WakeBook) -> int:
        """全量覆盖，理由同 `save()`。"""
        rows = [(w["id"], json.dumps(w, ensure_ascii=False)) for w in book.to_list()]
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM wakeups")
                self._conn.executemany(
                    "INSERT INTO wakeups(id, data) VALUES(?, ?)", rows
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return len(rows)

    # ------------------------------------------------------------ Source 状态

    def get_source_state(self, key: str) -> dict[str, Any] | None:
        """取某个 Source 的 Temporal Filter 状态。没有就返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM source_state WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except Exception:  # noqa: BLE001
            # 状态坏了最坏的后果是重放一次事件，比起因此起不来要轻得多
            logger.exception("Source 状态损坏，当作没有：key=%s", key)
            return None

    def set_source_state(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO source_state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
