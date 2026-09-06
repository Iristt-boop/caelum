"""订单存储 —— 待确认的单子活在这里。

## 为什么单独一个库

`orders.db` 是第五个库（前四个：sessions / attention / world / topics）。
两个都不合适：

- **world.db 不行**：World Model 的契约是「Observation 冻结只追加、永不改写」
  （见 `world_model/`）。订单状态机天生要改（pending → confirmed → paid），
  塞进去等于当场破掉那条契约
- **sessions.db 不行**：它是会话数据。订单是她和商家之间的事实，
  跟哪一轮对话说的没关系 —— 会话删了订单也还在

## 🔴 付款链接为什么单独一张表

`Caelum-AI支付-可行性调研.md` 第六节倒数第二条：
**收银台 URL 不进日志、不进聊天历史、不进数据库明文。**

`snapshot_json` 会被塞进 attachment 发给前端、会进日志、可能被人复制粘贴。
付款链接混在里面就等于到处都是。所以它单独存、带 TTL，
只在出卡片二那一帧取一次。

## 线程安全

同 `data/store.py`：一把锁串行化写，`check_same_thread=False`。
FastAPI 的同步路由跑在线程池里，不加锁会撞
「SQLite objects created in a thread can only be used in that same thread」。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 待确认单活多久。
#:
#: 15 分钟：比价格和券的波动快，比她去洗个澡回来慢。
#: 过期的单子**不许直接下**——价格可能已经变了，必须重新出卡。
TTL = timedelta(minutes=15)

#: 状态机（设计文档第四节）。8 个态，比支付调研列的 12 个少 4 个——
#: 少掉的是支付宝那套预算预占/对账/claim-lease，这次不碰钱所以不需要。
PENDING = "pending_confirm"
CONFIRMED = "confirmed"
PENDING_PAYMENT = "pending_payment"
PAID = "paid"
EXPIRED = "expired"
CANCELLED = "cancelled"
ORDER_FAILED = "order_failed"
PAYMENT_UNKNOWN = "payment_unknown"

#: 终态：到了就不再变，confirm/cancel 一律拒绝
FINAL = frozenset({PAID, EXPIRED, CANCELLED, ORDER_FAILED, PAYMENT_UNKNOWN})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id                TEXT PRIMARY KEY,
  session_id        TEXT NOT NULL,
  merchant          TEXT NOT NULL,
  state             TEXT NOT NULL,
  snapshot_json     TEXT NOT NULL,
  fingerprint       TEXT NOT NULL,
  merchant_order_id TEXT,
  detail_json       TEXT,
  created_at        TEXT NOT NULL,
  expires_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_state ON orders(state);
CREATE TABLE IF NOT EXISTS pay_links (
  order_id   TEXT PRIMARY KEY,
  url        TEXT NOT NULL,
  qr_url     TEXT,
  expires_at TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OrderStore:
    """待确认单的落盘存储。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            # 老库没有 qr_url 列，幂等补上（CREATE TABLE IF NOT EXISTS 不加列）
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(pay_links)")}
            if "qr_url" not in cols:
                self._conn.execute("ALTER TABLE pay_links ADD COLUMN qr_url TEXT")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    # ------------------------------------------------------------ 写

    def create(
        self, *, session_id: str, merchant: str,
        snapshot: dict[str, Any], fingerprint: str, now: datetime | None = None,
    ) -> str:
        """建一张待确认单，返回 order_id。"""
        now = now or _now()
        oid = f"ord-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO orders(id, session_id, merchant, state, snapshot_json,"
                " fingerprint, created_at, expires_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (oid, session_id, merchant, PENDING,
                 json.dumps(snapshot, ensure_ascii=False), fingerprint,
                 now.isoformat(), (now + TTL).isoformat(), now.isoformat()),
            )
            self._conn.commit()
        logger.info("待确认单已建：%s（%s，%s）", oid, merchant, session_id)
        return oid

    def set_state(
        self, oid: str, state: str, *,
        merchant_order_id: str | None = None,
        detail: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or _now()
        with self._lock:
            self._conn.execute(
                "UPDATE orders SET state=?, updated_at=?,"
                " merchant_order_id=COALESCE(?, merchant_order_id),"
                " detail_json=COALESCE(?, detail_json) WHERE id=?",
                (state, now.isoformat(), merchant_order_id,
                 json.dumps(detail, ensure_ascii=False) if detail else None, oid),
            )
            self._conn.commit()
        logger.info("订单 %s → %s", oid, state)

    def claim(self, oid: str, now: datetime | None = None) -> dict[str, Any] | None:
        """🔴 **幂等闸门**：把一张 pending 单原子地翻成 confirmed。

        返回那张单（可以往下走），或 None（不能走：不存在/已过期/已经被点过了）。

        为什么要原子：她连点两次「确认下单」会打进来两个请求。
        先查后改的话两个都会看到 pending，于是**下两单**。
        这里用 `UPDATE ... WHERE state=?` 让 SQLite 保证只有一个能成。
        """
        now = now or _now()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
            if row is None:
                return None
            if row["state"] != PENDING:
                logger.info("订单 %s 状态是 %s，不能确认", oid, row["state"])
                return None
            if datetime.fromisoformat(row["expires_at"]) <= now:
                self._conn.execute(
                    "UPDATE orders SET state=?, updated_at=? WHERE id=?",
                    (EXPIRED, now.isoformat(), oid))
                self._conn.commit()
                logger.info("订单 %s 已过期（%s），拒绝确认", oid, row["expires_at"])
                return None
            cur = self._conn.execute(
                "UPDATE orders SET state=?, updated_at=? WHERE id=? AND state=?",
                (CONFIRMED, now.isoformat(), oid, PENDING))
            self._conn.commit()
            if cur.rowcount != 1:
                # 另一个请求抢先了。**这不是错误**，是幂等生效了
                logger.info("订单 %s 已被另一次请求确认，这次跳过", oid)
                return None
        return self._row(row)

    def set_pay_link(self, oid: str, url: str, qr_url: str = "",
                     now: datetime | None = None) -> None:
        """付款链接单独存。**绝不进 snapshot_json**（见模块头）。

        `qr_url` 是二维码图。瑞幸给的是 `weixin://wxpay/bizpayurl?pr=…`——
        微信支付的 NATIVE（扫码）链接，点了不弹支付，只能扫或复制进微信。
        """
        now = now or _now()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO pay_links(order_id, url, qr_url, expires_at)"
                " VALUES(?,?,?,?)",
                (oid, url, qr_url or None, (now + timedelta(hours=2)).isoformat()))
            self._conn.commit()

    # ------------------------------------------------------------ 读

    def get(self, oid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        return self._row(row) if row else None

    def pay_link(self, oid: str, now: datetime | None = None) -> str | None:
        """取付款链接。过期就当没有 —— 过期的收银台链接点了也是报错页。"""
        row = self._pay_row(oid, now)
        return row["url"] if row else None

    def pay_qr(self, oid: str, now: datetime | None = None) -> str | None:
        """取二维码图的地址。没有就是没有。"""
        row = self._pay_row(oid, now)
        return (row["qr_url"] or None) if row else None

    def _pay_row(self, oid: str, now: datetime | None = None):
        now = now or _now()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pay_links WHERE order_id=?", (oid,)).fetchone()
        if row is None or datetime.fromisoformat(row["expires_at"]) <= now:
            return None
        return row

    def expire_stale(self, now: datetime | None = None) -> int:
        """把过了期还挂着的单子收掉。返回收了几张。"""
        now = now or _now()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE orders SET state=?, updated_at=? WHERE state=? AND expires_at<=?",
                (EXPIRED, now.isoformat(), PENDING, now.isoformat()))
            self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["snapshot"] = json.loads(d.pop("snapshot_json") or "{}")
        detail = d.pop("detail_json", None)
        d["detail"] = json.loads(detail) if detail else None
        return d
