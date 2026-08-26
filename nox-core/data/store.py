"""会话持久化（SQLite）。

原来 history 存在内存里，进程一关全丢 —— 糖糖聊完关掉窗口，下次打开
Nox 就不记得刚才说过什么。这一层就是为了解决这件事。

三个设计取舍，都写在这儿免得以后忘：

1. **消息按行存，不整包 JSON。**
   一个会话一行、把 history 序列化成一坨 JSON 更省事，但那样每次追加
   一句话都要重写整段，而且没法按轮数分页取。按行存，加一句就 INSERT 一行。

2. **只存对话内容，不存工具调用的中间过程。**
   tool_calls / tool_results 那些是给模型看的中间态，重启后重放没有意义
   （工具结果早就过期了 —— 三小时前查的灯的状态，现在不作数）。
   恢复会话时只带 user / assistant 的正文，让他知道"聊过什么"，
   而不是假装那些工具调用刚刚发生过。

3. **不做异步。**
   SQLite 的写入是毫秒级，包一层 async 只会引入复杂度。FastAPI 的同步
   路由本来就在线程池里跑，不会阻塞事件循环。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent.llm import Message
from context.timeline import with_dates
from context.token_budget import tail_within_budget

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    title       TEXT,
    summary     TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_sessions_updated
    ON sessions(updated_at DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionInfo:
    id: str
    created_at: str
    updated_at: str
    title: str | None
    message_count: int


class Store:
    """会话与消息的落盘存储。

    线程安全：用一把锁串行化所有写操作。SQLite 本身支持并发读，
    但 Python 的连接对象不是线程安全的，而 FastAPI 的同步路由跑在
    线程池里 —— 不加锁会碰到 "SQLite objects created in a thread
    can only be used in that same thread"。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False + 自己加锁，比每次新建连接快得多
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # 老库没有 summary 列，幂等补上（CREATE TABLE IF NOT EXISTS 不会加列）
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(sessions)")}
            if "summary" not in cols:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN summary TEXT")
            # WAL 让读写不互相阻塞；对单机小服务是纯赚
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.commit()
        logger.info("会话库就绪：%s", self.path)

    # ------------------------------------------------------------ 写

    def append(self, session_id: str, messages: list[Message]) -> int:
        """把一轮新增的消息追加进去，返回实际写入条数。

        只写有正文的 user / assistant 消息（理由见文件开头第 2 条）。
        首次写入时自动从第一条 user 消息提取标题。
        """
        rows = [
            (m.role, m.text)
            for m in messages
            if m.role in ("user", "assistant") and m.text and m.text.strip()
        ]
        if not rows:
            return 0

        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) AS s FROM messages WHERE session_id = ?",
                (session_id,),
            )
            seq = int(cur.fetchone()["s"]) + 1

            self._conn.execute(
                "INSERT INTO sessions(id, created_at, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at",
                (session_id, now, now),
            )
            self._conn.executemany(
                "INSERT INTO messages(session_id, seq, role, text, created_at) "
                "VALUES(?, ?, ?, ?, ?)",
                [(session_id, seq + i, role, text, now) for i, (role, text) in enumerate(rows)],
            )
            self._conn.commit()
        # 新会话自动补标题（在锁外调，_ensure_title 自己拿锁）
        self._ensure_title(session_id)
        return len(rows)

    def sync(self, session_id: str, history: list[Message]) -> int:
        """用完整 history 覆盖式同步 —— 只补写数据库里还没有的尾部。

        调用方拿到的 history 是**累积**的（loop 每轮返回全量），
        直接 append 会重复写。

        ⚠️ 2026-08-14 修复：**不能用条数比对**。原实现是
        `existing = count(sid)` 然后 `len(pending) <= existing 就 return 0`，
        但 `load()` 只取最近 limit 条（默认 40），会话一旦超过 40 条，
        history 永远是截断的 40 条 + 新增，`len(pending)` 永远 ≤ 库里总数，
        **新消息从此静默不再落库** —— 用户对话、Care、晨报、Attention
        全中招，Nox 停在 40 条前的旧世界（8-14 实测主会话 63 条时断更）。
        改成**内容锚点**：拿库里最后一条已存消息，在 history 里找它的位置，
        它之后的部分才是新增。这样 history 截断与否都不影响正确性。
        """
        pending = [
            m for m in history
            if m.role in ("user", "assistant") and m.text and m.text.strip()
        ]
        if not pending:
            return 0

        # 库里最后一条已存消息（role, text）。新会话没有，全量写。
        last = self._last_text(session_id)
        if last is None:
            n = self.append(session_id, pending)
            if n > 0:
                self._ensure_title(session_id)
            return n

        last_role, last_text = last
        # 从尾部往前找锚点。history 里最后一条消息往往就是库里最后一条
        # （除非这轮有新增），找到它之后的部分就是新增。
        # ⚠️ 不能只比 text：不同轮可能说出相同的话（她连续两次问「在吗」），
        # 必须**从尾部往前找第一个匹配**，且 role 也要对上。
        idx = -1
        for i in range(len(pending) - 1, -1, -1):
            m = pending[i]
            if m.role == last_role and (m.text or "").strip() == last_text.strip():
                idx = i
                break
        if idx < 0:
            # 理论上不该发生（history 来自 load，最后一条必然在里面）。
            # 真发生了就全量追加 —— 宁可重复写也不要丢消息。
            # 重复的代价是历史里多一条一样的，丢的代价是他忘记一切。
            logger.warning(
                "sync: 会话 %s 找不到锚点（%s），按全量追加处理", session_id[:8], last_text[:20])
            n = self.append(session_id, pending)
            if n > 0:
                self._ensure_title(session_id)
            return n

        fresh = pending[idx + 1:]
        if not fresh:
            return 0
        n = self.append(session_id, fresh)
        if n > 0:
            self._ensure_title(session_id)
        return n

    def _last_text(self, session_id: str) -> tuple[str, str] | None:
        """库里该会话最后一条已存消息的 (role, text)。没有返回 None。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT role, text FROM messages WHERE session_id = ? "
                "AND role IN ('user', 'assistant') "
                "ORDER BY seq DESC LIMIT 1",
                (session_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return (row["role"], row["text"])

    # ------------------------------------------------------- 摘要（压缩用）

    def get_summary(self, session_id: str) -> str | None:
        """该会话已压缩的摘要。没压过返回 None。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT summary FROM sessions WHERE id = ?", (session_id,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        return row["summary"] or None

    def set_summary(self, session_id: str, text: str) -> None:
        """写入/更新该会话的摘要。幂等：重复写同一份覆盖即可。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id, created_at, updated_at, summary) "
                "VALUES(?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET summary = excluded.summary, "
                "updated_at = excluded.updated_at",
                (session_id, _now(), _now(), text),
            )
            self._conn.commit()

    def set_title(self, session_id: str, title: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title[:120], _now(), session_id),
            )
            self._conn.commit()

    def _ensure_title(self, session_id: str) -> None:
        """会话还没有标题时，用第一条 user 消息自动补上。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT title FROM sessions WHERE id = ?", (session_id,)
            )
            row = cur.fetchone()
            if row is None or row["title"]:
                return  # 不存在或已有标题，不覆盖
            cur = self._conn.execute(
                "SELECT text FROM messages WHERE session_id = ? AND role = 'user' "
                "ORDER BY seq ASC LIMIT 1",
                (session_id,),
            )
            first = cur.fetchone()
            if first and first["text"]:
                title = first["text"].strip()[:40]
                self._conn.execute(
                    "UPDATE sessions SET title = ? WHERE id = ? AND title IS NULL",
                    (title, session_id),
                )

    def drop(self, session_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------ 读

    def load(
        self,
        session_id: str,
        limit: int = 40,
        recent_window_tokens: int | None = None,
    ) -> list[Message]:
        """取历史消息，按时间正序返回，**带日期分隔线**，前面拼上摘要。

        两种取法：
        - `limit`（默认 40 条）：兼容旧行为，按条数截最近窗口。
        - `recent_window_tokens`：token 预算驱动 —— 从尾部往前凑到 ≤ 预算。
          压缩落地后用这个，消息长短不再影响窗口大小。

        有摘要时，第一条是 `system` 角色的摘要消息（`context/compactor.py`
        的 `SUMMARY_HEADER` 文案），让 Nox 知道这段对话早前发生过什么。

        ⚠️ `created_at` 必须一起读出来。这里原本只 `SELECT role, text`，
        于是进他上下文的历史**一点时间信息都没有** —— 四天前那句
        「我今天去做美甲了」在他看来就是今天说的（糖糖 2026-08-11 报的）。
        换算和标注在 `context/timeline.py`，那边还写着为什么只能用绝对日期。
        """
        with self._lock:
            if recent_window_tokens is not None:
                cur = self._conn.execute(
                    "SELECT role, text, created_at FROM messages WHERE session_id = ? "
                    "ORDER BY seq DESC",
                    (session_id,),
                )
                rows = cur.fetchall()
            else:
                cur = self._conn.execute(
                    "SELECT role, text, created_at FROM messages WHERE session_id = ? "
                    "ORDER BY seq DESC LIMIT ?",
                    (session_id, limit),
                )
                rows = cur.fetchall()
            summary = self._conn.execute(
                "SELECT summary FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        summary_text = summary["summary"] if summary else None

        # 按 token 预算切最近窗口（复用 tail_within_budget，先转 Message）
        if recent_window_tokens is not None:
            # rows 是 seq DESC（最新在前）；转成时间正序（最老在前），
            # tail_within_budget 从尾部（最新）往前取 keep 条
            all_msgs = [
                Message(role=r["role"], text=r["text"]) for r in reversed(rows)
            ]
            recent = tail_within_budget(all_msgs, recent_window_tokens)
            keep = len(recent)
            # 时间正序的最新 keep 条 = 从正序尾部取，with_dates 顺序不变
            ordered = list(reversed(rows))
            dated = with_dates(
                (r["role"], r["text"], r["created_at"]) for r in ordered[-keep:]
            )
        else:
            dated = with_dates(
                (r["role"], r["text"], r["created_at"]) for r in reversed(rows)
            )

        if summary_text:
            from context.compactor import SUMMARY_HEADER
            return [
                Message(role="system", text=SUMMARY_HEADER + summary_text),
                *dated,
            ]
        return dated

    def load_full(self, session_id: str) -> list[Message]:
        """取该会话**全部** user/assistant 消息（不截断），供压缩用。

        压缩需要看完整历史才能决定哪些该压；`load()` 是给模型看的（截断的）。
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT role, text, created_at FROM messages WHERE session_id = ? "
                "ORDER BY seq",
                (session_id,),
            )
            rows = cur.fetchall()
        return with_dates(
            (r["role"], r["text"], r["created_at"]) for r in rows
        )

    def count(self, session_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (session_id,)
            )
            return int(cur.fetchone()["n"])

    def messages_between(self, start_iso: str, end_iso: str,
                         clean_only: bool = True) -> list[dict]:
        """某个时间段里的所有消息，按时间正序。

        「Nox 的一天」的聚合器用它把对话切成时间线上的块
        （`day/aggregator.py`）。**只读，不做任何加工** ——
        归一和意义化是聚合器的事。

        `created_at` 存的是 UTC ISO（见 `_now()`），所以传进来的边界
        也必须是 UTC ISO，字符串比较才成立。
        """
        with self._lock:
            sql = (
                "SELECT m.session_id, m.role, m.text, m.created_at "
                "FROM messages m "
                "WHERE m.created_at >= ? AND m.created_at < ? "
            )
            if clean_only:
                # 和 recent(clean_only) 同一条白名单：手写的测试 id 不进时间线
                sql += "AND length(m.session_id) = 32 AND m.session_id NOT GLOB '*[^0-9a-f]*' "
            sql += "ORDER BY m.created_at ASC"
            cur = self._conn.execute(sql, (start_iso, end_iso))
            return [dict(r) for r in cur.fetchall()]

    def recent(self, limit: int = 20, clean_only: bool = False) -> list[SessionInfo]:
        with self._lock:
            if clean_only:
                # 只列前端生成的 32 位纯 hex id（和 bridge 的 conv-sessions
                # 白名单一致）。手写的测试 id 不可能长这样。
                cur = self._conn.execute(
                    "SELECT s.id, s.created_at, s.updated_at, s.title, "
                    "       (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS n "
                    "FROM sessions s "
                    "WHERE length(s.id) = 32 AND s.id NOT GLOB '*[^0-9a-f]*' "
                    "ORDER BY s.updated_at DESC LIMIT ?",
                    (limit,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT s.id, s.created_at, s.updated_at, s.title, "
                    "       (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS n "
                    "FROM sessions s ORDER BY s.updated_at DESC LIMIT ?",
                    (limit,),
                )
            return [
                SessionInfo(
                    id=r["id"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    title=r["title"],
                    message_count=int(r["n"]),
                )
                for r in cur.fetchall()
            ]

    def started_at(self, session_id: str) -> datetime | None:
        """这个会话是什么时候开始的。给「距对话开始已 X 天」用。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT created_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None or not row["created_at"]:
            return None
        try:
            return datetime.fromisoformat(row["created_at"])
        except ValueError:
            return None

    def last_user_at(self, session_id: str) -> datetime | None:
        """她在这个会话里最后一次说话是什么时候。没说过就是 None。

        唤醒链靠这个判断「她回了没有」—— 醒来之前先看一眼，
        她要是已经回话了，这条链就该直接结束，别再追问。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT created_at FROM messages WHERE session_id = ? AND role = 'user' "
                "ORDER BY seq DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        if row is None or not row["created_at"]:
            return None
        try:
            return datetime.fromisoformat(row["created_at"])
        except ValueError:
            return None

    def stats(self) -> dict:
        with self._lock:
            s = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"]
            m = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
        size = self.path.stat().st_size if self.path.exists() else 0
        return {"sessions": int(s), "messages": int(m), "db_bytes": size}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
