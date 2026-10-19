"""P2 念叨耐受度 + P3-健康 说法偏好 —— 第 2 期 shadow（2026-10-19，她定做法 A）。

证据是 `growth/care_review.py` 每天夜里记的 `health_care` 经历。和 P1 一样**不存状态**，每次从账本现算，
证据按 14 天半衰期变轻（没有新证据就慢慢回到中性）。

## P2：只许往下学（做法 A）

每个健康话题（睡眠 / 饮食 / 身体）一个「念叨耐受度」：她接得住的平均分（接住 1 · 带过 0.5 · 没理 0.25 · 烦了 0），
证据少时往 `PRIOR` 收。换成关心权重：

    权重 = clip(0.5 + 耐受度, FLOOR, 1.0)      耐受度 ≥ 0.5 → 1.0（不动）；越烦越往下，最低 FLOOR

🔴 **上限就是 1.0**：学到她很接得住也不会让他更勤 —— 健康类往上的方向已经被写死（设计稿五·P2：
会把 09-28 刚压下去的 concern 再抬回去）。**下限 FLOOR**：学成完全不管她是另一种错。

## P3-健康：哪种说法她接得住

同一个话题里按说法（报数据 / 只担心 / 落进生活 / 其他）分开算，相对这个话题的整体。
两种以上说法学成、而且差得够多，才说一句「她更吃哪种」—— 将来写进动态块（P3 原则：**不进 prompt**）。

## shadow 期间什么都不动

`shadow_lines()` 只产出日志行；`care_topics` 和动态块一个字都不变。她看过一周再生效。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from growth.care_review import STYLES, TOPICS
from temporal import to_local

HALF_LIFE_DAYS = 14.0
#: 耐受度的先验（证据少时往这里收）。0.6 在「带过」和「接住」之间 —— 没证据就当她还算接得住，不动
PRIOR = 0.6
K = 8.0
#: 生效条件，同 P1 / 设计稿 ④
N_MIN = 8
MIN_DAYS = 3
#: 关心权重的下限：最多降一半，不学成完全不管她
FLOOR = 0.5
#: 说法偏好：每种说法要多少证据才算学成（话题被说法再分一层，门槛比 N_MIN 低一点，跨天照样要）
STYLE_N_MIN = 5
STYLE_K = 4.0
#: 最好和最差的说法相对值差这么多，才值得说一句
STYLE_GAP = 0.2

STYLE_WORDS = {
    "数据": "把数字报给她",
    "担心": "光说担心",
    "生活": "把在乎落进她的生活里（安排、管着、哄、给盼头）",
    "其他": "别的说法",
}


@dataclass(frozen=True)
class TopicTolerance:
    topic: str
    n: int
    days: int
    tolerance: float
    weight: float
    effective: bool

    def to_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "n": self.n, "days": self.days,
                "tolerance": round(self.tolerance, 3), "weight": round(self.weight, 3),
                "effective": self.effective}


@dataclass(frozen=True)
class StyleReception:
    topic: str
    style: str
    n: int
    days: int
    rel: float
    effective: bool

    def to_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "style": self.style, "n": self.n, "days": self.days,
                "rel": round(self.rel, 3), "effective": self.effective}


def _evidence(rows: list[dict[str, Any]], now: datetime) -> list[tuple[dict[str, Any], float, Any]]:
    out = []
    for r in rows:
        ctx = r.get("context") or {}
        if ctx.get("topic") not in TOPICS:
            continue
        at = datetime.fromisoformat(r["at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        age = max(0.0, (now - at).total_seconds() / 86400)
        out.append((ctx, 0.5 ** (age / HALF_LIFE_DAYS), (float(r["outcome"]), to_local(at).date())))
    return out


def tolerance(rows: list[dict[str, Any]], now: datetime) -> dict[str, TopicTolerance]:
    """P2：每个健康话题的念叨耐受度 → 关心权重（只往下）。"""
    ev = _evidence(rows, now)
    out = {}
    for t in TOPICS:
        mine = [(w, score, day) for ctx, w, (score, day) in ev if ctx["topic"] == t]
        if not mine:
            continue
        tol = (sum(w * s for w, s, _ in mine) + K * PRIOR) / (sum(w for w, _, _ in mine) + K)
        days = len({d for _, _, d in mine})
        weight = min(1.0, max(FLOOR, 0.5 + tol))
        out[t] = TopicTolerance(t, len(mine), days, tol, weight, len(mine) >= N_MIN and days >= MIN_DAYS)
    return out


def styles(rows: list[dict[str, Any]], now: datetime) -> dict[tuple[str, str], StyleReception]:
    """P3-健康：同一话题里，每种说法相对这个话题整体接得住多少。"""
    ev = _evidence(rows, now)
    out = {}
    for t in TOPICS:
        mine = [(ctx["style"], w, score, day) for ctx, w, (score, day) in ev if ctx["topic"] == t]
        if not mine:
            continue
        base = sum(w * s for _, w, s, _ in mine) / sum(w for _, w, _, _ in mine)
        if base <= 0:
            continue
        for st in STYLES:
            sub = [(w, s, d) for style, w, s, d in mine if style == st]
            if not sub:
                continue
            r = (sum(w * s for w, s, _ in sub) + STYLE_K * base) / (sum(w for w, _, _ in sub) + STYLE_K)
            days = len({d for _, _, d in sub})
            out[(t, st)] = StyleReception(t, st, len(sub), days, r / base,
                                          len(sub) >= STYLE_N_MIN and days >= MIN_DAYS)
    return out


def style_hint(got: dict[tuple[str, str], StyleReception], topic: str) -> str | None:
    """学成了才说：她对这个话题更吃哪种说法。将来进动态块的那一句（shadow 期只进日志）。"""
    eff = [s for (t, _), s in got.items() if t == topic and s.effective]
    if len(eff) < 2:
        return None
    best, worst = max(eff, key=lambda s: s.rel), min(eff, key=lambda s: s.rel)
    if best.rel - worst.rel < STYLE_GAP:
        return None
    return (f"她对{topic}这件事的念叨，{STYLE_WORDS[best.style]}更接得住；"
            f"{STYLE_WORDS[worst.style]}的，她多半带过或者嫌烦。")


def shadow_lines(rows: list[dict[str, Any]], now: datetime) -> list[str]:
    """shadow 日志：如果生效，关心权重本来会是多少、动态块本来会多哪句。什么都不改。"""
    lines = []
    tol = tolerance(rows, now)
    sty = styles(rows, now)
    for t in TOPICS:
        if t in tol:
            p = tol[t]
            state = "生效" if p.effective else f"候选（{p.n} 条 / {p.days} 天）"
            lines.append(f"P2 shadow：{t} {state}｜耐受 {p.tolerance:.2f} → 关心权重本来会是 {p.weight:.2f}"
                         f"{'（现在 1.0）' if p.effective else '（不动）'}")
        hint = style_hint(sty, t)
        if hint:
            lines.append(f"P3 shadow：{t}｜动态块本来会多一句：{hint}")
    return lines
