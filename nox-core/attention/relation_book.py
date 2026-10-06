"""关系状态的账本 —— 我们之间的事（2026-10-06，设计稿《Caelum-关系状态-设计稿-2026-10-06.md》）。

她 10-06：「四类都要，聊天卡片和列表都要。当时的确认放聊天，后续我可以自己在列表查看。」

在这之前 `RelationshipState` 是一份写死的种子，`avoid_topics` 永远是空 set ——
架构审计 3.3：「她说"别老问我这个"这件事，系统无法记住」。

## 四类

    avoid  别问   她说过别老问的事 → 他不再主动问（她自己提照样接）
    care   上心   她要他盯着的事   → 更上心
    pact   约定   我们说好的事     → 每轮都带着
    vibe   气氛   刚闹别扭 / 和好了 / 最近很甜 → 他自己判断，**不用她点头**，VIBE_TTL 后淡掉

## 状态

    pending → active（她点 Keep）/ rejected（她点 Not quite）
    active  → removed（她在列表里删）       —— **不真删**，留着谁、什么时候、她原话
    vibe 直接 active，到 expires_at 就不算了

## 闸（宁可漏 —— 她还会再说）

- 同 kind 同 topic 已有待确认 / 生效的，不重复提
- 她点过「不对」的，REJECT_QUIET 内同 kind 同 topic 不再提
- 一天最多 DAILY_CARDS 张卡（vibe 不算卡）

存在 attention.db（`scripts/caelum-backup.sh` 已经在备份它）。只用 AttentionStore 的连接和锁，
不另开连接 —— 两个连接写同一个库就是 `database is locked`。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

KINDS = ("avoid", "care", "pact", "vibe")
#: 她看的叫法（列表 / 卡片）。UI 是英文，这几个词给他的上下文和日志用
KIND_WORDS = {"avoid": "别问", "care": "上心", "pact": "约定", "vibe": "气氛"}
VIBE_TTL = timedelta(days=3)
REJECT_QUIET = timedelta(days=30)
DAILY_CARDS = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS relation_items (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL,
    topic       TEXT NOT NULL DEFAULT '',
    quote       TEXT NOT NULL DEFAULT '',
    session_id  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    expires_at  TEXT
);
CREATE INDEX IF NOT EXISTS relation_items_kind ON relation_items(kind, status);
"""


@dataclass
class RelationItem:
    id: str
    kind: str
    text: str
    topic: str
    quote: str
    session_id: str
    status: str
    created_at: str
    decided_at: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _key(topic: str, text: str) -> str:
    """同一件事的判断依据：有话题用话题，没有就用他那句话本身。"""
    return (topic or "").strip() or (text or "").strip()


class RelationBook:
    def __init__(self, store: Any) -> None:
        self._conn, self._lock = store._conn, store._lock
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------ 读

    def _rows(self, where: str = "1=1", args: tuple = ()) -> list[RelationItem]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, kind, text, topic, quote, session_id, status, created_at, decided_at, expires_at "
                f"FROM relation_items WHERE {where} ORDER BY created_at DESC", args).fetchall()
        return [RelationItem(*tuple(r)) for r in rows]

    def get(self, item_id: str) -> RelationItem | None:
        rows = self._rows("id=?", (item_id,))
        return rows[0] if rows else None

    def _live(self, it: RelationItem, now: datetime) -> bool:
        return not (it.expires_at and datetime.fromisoformat(it.expires_at) <= now)

    def items(self, now: datetime, statuses: tuple[str, ...] = ("pending", "active")) -> list[RelationItem]:
        """列表页用：待确认 + 生效中（过期的气氛不算）。"""
        marks = ",".join("?" * len(statuses))
        return [it for it in self._rows(f"status IN ({marks})", statuses) if self._live(it, now)]

    def active(self, now: datetime, kind: str | None = None) -> list[RelationItem]:
        return [it for it in self.items(now, ("active",)) if kind is None or it.kind == kind]

    def avoid_topics(self, now: datetime) -> set[str]:
        return {it.topic for it in self.active(now, "avoid") if it.topic}

    def care_topics(self, now: datetime) -> set[str]:
        return {it.topic for it in self.active(now, "care") if it.topic}

    # ------------------------------------------------------------ 写

    def _blocked(self, kind: str, key: str, now: datetime) -> str | None:
        for it in self._rows("kind=?", (kind,)):
            if _key(it.topic, it.text) != key:
                continue
            if it.status in ("pending", "active") and self._live(it, now):
                return f"已经{'在等她确认' if it.status == 'pending' else '记着'}了"
            if it.status == "rejected" and it.decided_at and now - datetime.fromisoformat(it.decided_at) < REJECT_QUIET:
                return "她说过不对，30 天内不再提"
        return None

    def cards_today(self, now: datetime) -> int:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return sum(1 for it in self._rows("kind != 'vibe'")
                   if datetime.fromisoformat(it.created_at) >= start)

    def propose(self, *, kind: str, text: str, topic: str = "", quote: str = "",
                session_id: str = "", now: datetime) -> RelationItem | None:
        """他想记一条。过不了闸返回 None（并说为什么）。vibe 直接生效，其余等她点头。"""
        text = (text or "").strip()
        if kind not in KINDS or not text:
            logger.info("关系提议丢弃：kind=%r text=%r", kind, text[:30])
            return None
        if kind == "vibe":
            # 新气氛顶掉旧气氛 —— 「刚闹别扭」和「和好了」不该同时挂着
            with self._lock:
                self._conn.execute("UPDATE relation_items SET status='removed', decided_at=? "
                                   "WHERE kind='vibe' AND status='active'", (now.isoformat(),))
                self._conn.commit()
        else:
            why = self._blocked(kind, _key(topic, text), now)
            if why:
                logger.info("关系提议不重复提（%s）：%s · %s", why, KIND_WORDS[kind], text[:40])
                return None
            if self.cards_today(now) >= DAILY_CARDS:
                logger.info("关系提议今天的卡用完了（%d 张）：%s · %s", DAILY_CARDS, KIND_WORDS[kind], text[:40])
                return None
        it = RelationItem(
            id=uuid.uuid4().hex[:12], kind=kind, text=text[:120], topic=(topic or "").strip()[:20],
            quote=(quote or "").strip()[:200], session_id=session_id or "",
            status="active" if kind == "vibe" else "pending", created_at=now.isoformat(),
            decided_at=now.isoformat() if kind == "vibe" else None,
            expires_at=(now + VIBE_TTL).isoformat() if kind == "vibe" else None,
        )
        with self._lock:
            self._conn.execute("INSERT INTO relation_items VALUES (?,?,?,?,?,?,?,?,?,?)", (
                it.id, it.kind, it.text, it.topic, it.quote, it.session_id, it.status,
                it.created_at, it.decided_at, it.expires_at))
            self._conn.commit()
        logger.info("关系提议：%s · %s（%s）", KIND_WORDS[kind], it.text[:40], it.status)
        return it

    #: 她能做的三件事 → 允许从哪个状态到哪个状态
    _MOVES = {"confirm": ({"pending", "rejected"}, "active"),
              "reject": ({"pending"}, "rejected"),
              "remove": ({"pending", "active"}, "removed")}

    def decide(self, item_id: str, action: str, now: datetime) -> RelationItem | None:
        """她点了 Keep / Not quite / 在列表里删。走不通（没这条 / 状态不对）返回 None。"""
        move = self._MOVES.get(action)
        it = self.get(item_id)
        if move is None or it is None or it.status not in move[0]:
            return None
        with self._lock:
            self._conn.execute("UPDATE relation_items SET status=?, decided_at=? WHERE id=?",
                               (move[1], now.isoformat(), item_id))
            self._conn.commit()
        logger.info("关系状态：%s · %s → %s", KIND_WORDS.get(it.kind, it.kind), it.text[:40], move[1])
        return self.get(item_id)

    # ------------------------------------------------------------ 给他看

    def describe(self, now: datetime, per_kind: int = 5) -> str:
        """进他每轮动态块的那段。没有就空串（什么都不说）。"""
        lines = []
        heads = {"pact": "你们的约定", "avoid": "她说过别老问的（她自己提起照样接，别主动问）",
                 "care": "她要你上心的", "vibe": "你们这几天的气氛"}
        for kind in ("pact", "avoid", "care", "vibe"):
            got = self.active(now, kind)[:per_kind]
            if got:
                lines.append(f"{heads[kind]}：" + "；".join(it.text for it in got))
        return "\n".join(lines)
