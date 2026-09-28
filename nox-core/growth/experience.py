"""经历账本的第一种经历：他主动找她，她回没回（Growth Loop 第 0 期，2026-09-28）。

设计稿：`CAELUM-GROWTH-LOOP-设计.md` 第四节 ② 与第四·五节。

## 🔴 判定只有一个来源：rhythm

「理了 / 没理」早就由 `attention/rhythm.py` 在判（她说话 → 理了；满 4 小时 → 没理；
判定时刻她在睡 → 继续等）。这里**不另判**，只在 rhythm 判定落定的那一刻抄一行进账 ——
两套判定迟早对不上，而且同一次没回会被罚两遍（设计稿 G10）。

## 🔴 情境在开口那一刻取，不在判定那一刻取

`posture` / `silent_min` 描述的是「他开口时她在干嘛」：正在聊（沉默 2 分钟）的那句
当然会被接住，那不叫她愿意被找。等到 4 小时后判定时再取，她早就换了状态 ——
所以 service 在 `on_spoke` 时就把它们算好存进 rhythm 的 pending，这里原样抄。

第 0 期**先全记、不过滤**：过滤阈值要拿这本账回放出来定（设计稿四·五节第 3 条）。

## 不进账的

她睡着时的自言自语 —— service 在调 `on_spoke` 之前就跳过了（09-23 起），
所以它们根本到不了 rhythm，也就到不了这里（设计稿 G11）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from temporal import to_local

logger = logging.getLogger(__name__)

KIND_PROACTIVE_REPLY = "proactive_reply"

#: 时段桶（设计稿 P1 的建议分档）。1-9 点醒着找她很少见，单独一桶不和别的混
_BUCKETS = ((9, 12, "9-12"), (12, 14, "12-14"), (14, 18, "14-18"), (18, 21, "18-21"))


def bucket(dt: datetime) -> str:
    """这一刻落在哪个时段桶（按她那边的钟）。"""
    h = to_local(dt).hour
    for lo, hi, name in _BUCKETS:
        if lo <= h < hi:
            return name
    return "21-1" if (h >= 21 or h < 1) else "1-9"


def opening_context(now: datetime, *, source: str, posture: str | None,
                    silent: timedelta | None) -> dict[str, Any]:
    """他开口这一刻的情境。**读不到的留 None**，不猜（同 her_state.read）。"""
    return {
        "bucket": bucket(now),
        "source": source,
        "posture": posture or None,
        "silent_min": None if silent is None else round(silent.total_seconds() / 60),
    }


def record_reply(store: Any, entry: dict[str, Any]) -> int:
    """rhythm 判定落定时调一次。`entry` 是 rhythm pending 里的那一条。"""
    ctx = dict(entry.get("ctx") or {})
    spoke_at = datetime.fromisoformat(entry["at"])
    if entry.get("reply_at"):
        ctx["reply_min"] = round(
            (datetime.fromisoformat(entry["reply_at"]) - spoke_at).total_seconds() / 60)
    replied = bool(entry["replied"])
    row_id = store.append_experience(
        at=entry["at"], kind=KIND_PROACTIVE_REPLY, context=ctx,
        outcome=1.0 if replied else 0.0,
        #: 这条经历的结果是她给的（和 OB extract_memory 同一套归属）
        attributed_to="user",
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )
    logger.info("成长账本：%s %s 的开口 → %s（开口时她 %s、已沉默 %s 分钟）",
                ctx.get("bucket", "?"), ctx.get("source", "?"),
                "她回了" if replied else "她没回", ctx.get("posture") or "?",
                "?" if ctx.get("silent_min") is None else ctx["silent_min"])
    return row_id
