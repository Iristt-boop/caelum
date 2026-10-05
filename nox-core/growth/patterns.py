"""P1 时段回应度 `receptive` —— 第 1 期 shadow（2026-10-05，她点头）。

设计稿 `CAELUM-GROWTH-LOOP-设计.md` 四·六节。一句话：**哪个时段找她，她相对更容易很快接住**。

## 为什么不是「她回没回」

第 0 期跑了一周，79 条开口**全是「4 小时内回了」**（逐条对过会话库，是真的）。
拿它学，每个时段都会学成「她多半会回」→ 每个时段都更勤 —— 设计稿反模式 1「变成黏人」。
有区分度的是**回得多快**：深夜 21-1 点中位 23 分钟，白天各段 50~60 分钟。

## 为什么只学相对值

整体快慢（放假一直在外面、这周特别忙）是 rhythm 的事 —— 它看最近 5 条，变得快。
P1 只学「**哪个时段相对**更容易接住他」，变得慢。节后她整体回得慢了，rhythm 先反应；
P1 的形状（深夜比白天快）不该因此全体往下掉。

## 🔴 不存状态

P1 是经历账本的一个**读视角**，每次现算（同 `resonance.py`「Drive 没有自己的记忆」）。
「可重算」「可撤」于是白送：改参数、标掉某几天，下一次读就是新结果。
证据按 14 天半衰期变轻 —— 那就是设计稿 ⑤ 的「惰性回落」：没有新证据，各时段被拉回她的整体，bias → 1.0。

## 第 1 期只看不动

`shadow()` 只写日志「如果生效，惦记间隔本来会是多少」。rhythm 返回的窗口一个字都不变。
要不要进第 2 期，至少等 shadow 覆盖一个正常上班周（约 10-19），她看过再定。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from growth.experience import KIND_PROACTIVE_REPLY, bucket
from temporal import to_local

logger = logging.getLogger(__name__)

#: 多久之内回算「很快接住」（分钟）。第 0 期：她整体 38% 的开口在这条线内
FAST_MIN = 30
#: 开口时她至少沉默了多久才算证据（分钟）。更短的是「她正在聊」，接住了也不说明什么
MIN_SILENT_MIN = 20
#: 收缩强度：证据少时往她的整体快回率收。和 N_MIN 同量级 —— 攒到 8 条时各占一半
K = 8.0
#: 证据半衰期（天）。= 设计稿 ⑤ 的惰性回落；国庆那周到 10-19 已降到一半以下
HALF_LIFE_DAYS = 14.0
#: 生效条件（设计稿 ④）：条数够、而且跨天 —— 一天的坏心情不能变成习惯
N_MIN = 8
MIN_DAYS = 3
#: bias 上下限（设计稿 ⑥）：最多克制三成、最多积极三成
BIAS_LO, BIAS_HI = 0.7, 1.3

BUCKET_ORDER = ("9-12", "12-14", "14-18", "18-21", "21-1", "1-9")


@dataclass(frozen=True)
class BucketPattern:
    bucket: str
    #: 原始条数 / 跨了几天（生效条件看这两个，不看加权）
    n: int
    days: int
    #: 这个时段原始的快回比例（给人看的，不参与计算）
    fast_rate: float
    #: 相对她整体的快回率，1.0 = 和平时一样
    rel: float
    bias: float
    effective: bool

    def describe(self) -> str:
        state = "生效" if self.effective else f"候选（{self.n} 条 / {self.days} 天）"
        return (f"{self.bucket} {state}｜快回 {self.fast_rate:.0%}，"
                f"相对平时 ×{self.rel:.2f} → bias {self.bias:.2f}")

    def to_dict(self) -> dict[str, Any]:
        return {"bucket": self.bucket, "n": self.n, "days": self.days,
                "fast_rate": round(self.fast_rate, 3), "rel": round(self.rel, 3),
                "bias": round(self.bias, 3), "effective": self.effective}


def _evidence(rows: list[dict[str, Any]]) -> list[tuple[str, float, datetime]]:
    """账本 → (时段, 快回 1/0, 开口时刻)。过滤见四·六节第 3 条。"""
    out = []
    for r in rows:
        if r.get("kind", KIND_PROACTIVE_REPLY) != KIND_PROACTIVE_REPLY:
            continue
        c = r.get("context") or {}
        #: 没有 bucket 的是第 0 期上线前就在等判定的那条，没有情境
        if "bucket" not in c or c.get("posture") == "ignored":
            continue
        if (c.get("silent_min") or 0) < MIN_SILENT_MIN:
            continue
        fast = 1.0 if r.get("outcome") and (c.get("reply_min") is not None
                                             and c["reply_min"] <= FAST_MIN) else 0.0
        out.append((c["bucket"], fast, datetime.fromisoformat(r["at"])))
    return out


def receptive(rows: list[dict[str, Any]], now: datetime) -> dict[str, BucketPattern]:
    """各时段的 P1。没有证据的时段不出现。"""
    ev = _evidence(rows)
    if not ev:
        return {}
    w = [0.5 ** (max(0.0, (now - at).total_seconds()) / 86400 / HALF_LIFE_DAYS)
         for _, _, at in ev]
    total = sum(w)
    overall = sum(wi * f for wi, (_, f, _) in zip(w, ev)) / total
    out: dict[str, BucketPattern] = {}
    for b in BUCKET_ORDER:
        idx = [i for i, e in enumerate(ev) if e[0] == b]
        if not idx:
            continue
        wb = sum(w[i] for i in idx)
        fb = sum(w[i] * ev[i][1] for i in idx)
        if overall > 0:
            rel = ((fb + K * overall) / (wb + K)) / overall
        else:
            #: 她一次都没快回过 —— 没有「相对」可言，全体中性
            rel = 1.0
        value = min(1.0, max(0.0, 0.5 * rel))
        bias = BIAS_LO + (BIAS_HI - BIAS_LO) * value
        days = len({to_local(ev[i][2]).date() for i in idx})
        out[b] = BucketPattern(
            bucket=b, n=len(idx), days=days,
            fast_rate=sum(ev[i][1] for i in idx) / len(idx), rel=rel, bias=bias,
            effective=len(idx) >= N_MIN and days >= MIN_DAYS,
        )
    return out


def shadow(store: Any, *, why: str, at: datetime, window: tuple[float, float],
           cold: bool) -> None:
    """rhythm 每次真摇时间时调一次：写一行「如果 P1 生效，本来会是多少」。不改任何东西。"""
    b = bucket(at)
    lo, hi = window
    if cold:
        logger.info("P1 shadow：%s %s 时段，rhythm 冷档优先，P1 不参与（窗口 %.0f-%.0f 分钟）",
                    why, b, lo, hi)
        return
    p = receptive(store.list_experiences(KIND_PROACTIVE_REPLY),
                  datetime.now(timezone.utc)).get(b)
    if p is None or not p.effective:
        logger.info("P1 shadow：%s %s 时段还没学成（%s），本来也不会动：窗口 %.0f-%.0f 分钟",
                    why, b, "没有证据" if p is None else f"{p.n} 条 / {p.days} 天", lo, hi)
        return
    logger.info("P1 shadow：%s %s 时段 %s → 间隔本来会是 %.0f-%.0f 分钟（现在 %.0f-%.0f）",
                why, b, p.describe(), lo / p.bias, hi / p.bias, lo, hi)
