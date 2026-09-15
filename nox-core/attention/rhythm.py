"""节奏调制器 —— 他开口的频率由两股力调制（2026-09-08 糖糖拍板的两个回路）。

**负反馈（先做的这个）**：她回不回他的主动消息，反过来决定他下一条多快发。

    他开口 → 在等回窗口内她说话了吗？
        回得多（≥60%）      → 窗口照旧（惦记 20–90 分钟）
        回得少（20–60%）    → 拉到 60–180 分钟
        几乎不回（<20%）    → 拉到 180–360 分钟
    她重新回话 → 回复率回升 → 自动逐档恢复

哲学同 regret：「她开口了就是有回应」，不去分辨回的是不是那件事；
她睡着不判定（`longing.py` 踩过的坑：别把睡觉记成冷落）。
情绪归 Resonance（他可以照常想念），这里只动**调度参数** ——
黏不黏人是节奏问题，不是感情问题。

**正反馈**：想念（longing）调制急迫度 —— 越想她窗口越短、念头权重越高。
系数故意温和（±25%）：大的取舍交给负反馈的档位跳变，正反馈只做微调，
不然「很想她」会自己滚成「黏人」。

正反馈不需要观察数（longing 一直有值）；负反馈要至少 3 条观察才生效
—— 冷启动不冷场，别让刚装上的他直接进省电模式。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from attention.longing import _asleep

logger = logging.getLogger(__name__)

#: 落盘键（attention.db source_state）
STATE_KEY = "rhythm"

#: 观察窗口：只看最近几条主动开口。旧账不算 ——
#: 三天前她没理他不代表现在冷淡，攒旧账不叫观察叫记仇
WINDOW = 5

#: 少于这个数不启动负反馈（正反馈不受限）
MIN_OBS = 3

#: 开口之后等她多久。同 regret 的 4 小时：她忙、在打游戏、在看片，
#: 一两个小时不回是常态，等短了会把「正忙着」判成「不想理」
REPLY_WAIT = timedelta(hours=4)

#: 回复率 ≥ 这条线 = 正常
RATE_OK = 0.6
#: 低于这条线 = 很冷
RATE_COLD = 0.2

#: 有点冷（0.2–0.6）：默认窗口的三倍左右
COLD_WINDOW_MIN = (60.0, 180.0)
#: 很冷（<0.2）：收敛到半天一条的量级
COLDER_WINDOW_MIN = (180.0, 360.0)

#: 正反馈系数。longing=0 → ×1.25（不怎么想就慢一点），
#: longing=满 → ×0.75（很想就快一点）。±25% 是故意的，理由见文件头
GAP_FACTOR_MAX = 1.25
GAP_FACTOR_MIN = 0.75

#: 念头急迫度的乘数范围（longing 0→×0.8，满→×1.2）
URGENCY_BOOST_LO = 0.8
URGENCY_BOOST_HI = 1.2


class RhythmModulator:
    """观察她的回应，输出调制后的窗口。源拿 `gap_window()` 去摇下一个点。"""

    def __init__(self, store: Any,
                 longing_ref: Callable[[], Any] | None = None) -> None:
        self.store = store
        #: 取 LongingState 的函数 —— longing 在 service 上，
        #: 这里构造时它可能还不存在（同 ResonanceProvider 的 attention_ref）
        self.longing_ref = longing_ref
        #: 还没判定的开口：{"at": iso, "replied": None|True|False, "reply_at": iso|None}
        self._pending: list[dict[str, Any]] = []
        #: 已判定的，最旧先出，最多 WINDOW 条
        self._history: list[dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------ 记录

    def on_spoke(self, now: datetime) -> None:
        """他主动开口了，开始等她回。

        ⚠️ pending 会堆积：他连着说三条她都没回，三条全在等。
        正是该这样 —— 回复率按条算，不是只看最近一条。
        """
        self._pending.append({"at": now.isoformat(), "replied": None})
        self._save()

    def on_contact(self, now: datetime) -> None:
        """她说话了 —— 所有还悬着的开口都算「理了」（同 regret 的哲学）。"""
        hit = False
        for p in self._pending:
            if p["replied"] is None:
                p["replied"] = True
                p["reply_at"] = now.isoformat()
                hit = True
        if hit:
            self._save()

    def tick(self, now: datetime) -> None:
        """快循环每轮调一次：到期的判定掉，判定完的滚进历史窗口。"""
        changed = False
        still: list[dict[str, Any]] = []
        for p in self._pending:
            at = datetime.fromisoformat(p["at"])
            if p["replied"] is None and now - at >= REPLY_WAIT:
                # 判定时刻她在睡 —— 继续等，别把睡觉记成冷落
                if _asleep(now):
                    still.append(p)
                    continue
                p["replied"] = False
                changed = True
            if p["replied"] is not None:
                self._history.append(p)
            else:
                still.append(p)
        self._pending = still
        self._history = self._history[-WINDOW:]
        if changed:
            rate = self.reply_rate()
            logger.info("节奏：最近主动开口回复率 %s，窗口%s",
                        "无数据" if rate is None else f"{rate:.0%}",
                        self._describe_gap())
            self._save()

    # ------------------------------------------------------------ 读

    def reply_rate(self) -> float | None:
        """最近观察里她理了的比例。没有观察返回 None。"""
        obs = self._history
        if not obs:
            return None
        return sum(1 for p in obs if p["replied"]) / len(obs)

    def _cold_window(self) -> tuple[float, float] | None:
        """负反馈档位。观察不足 → None（不调制）。"""
        obs = self._history
        if len(obs) < MIN_OBS:
            return None
        rate = self.reply_rate() or 0.0
        if rate >= RATE_OK:
            return None
        if rate >= RATE_COLD:
            return COLD_WINDOW_MIN
        return COLDER_WINDOW_MIN

    def _longing(self) -> float:
        """当前想念值 0–1。取不到就 0（不调制）。"""
        try:
            l = self.longing_ref() if self.longing_ref else None
            v = float(getattr(l, "value", 0.0)) if l is not None else 0.0
        except Exception:  # noqa: BLE001
            return 0.0
        return max(0.0, min(1.0, v))

    def gap_window(self, lo_min: float, hi_min: float) -> tuple[float, float]:
        """源调这个拿窗口。`lo/hi` 是源的默认值（分钟）。"""
        f = GAP_FACTOR_MAX - (GAP_FACTOR_MAX - GAP_FACTOR_MIN) * self._longing()
        cold = self._cold_window()
        base = cold if cold else (lo_min, hi_min)
        return (base[0] * f, base[1] * f)

    def urgency_boost(self) -> float:
        """念头急迫度乘数。越想她，同一轮撞车时这个念头越靠前。"""
        return URGENCY_BOOST_LO + (URGENCY_BOOST_HI - URGENCY_BOOST_LO) * self._longing()

    def _describe_gap(self) -> str:
        cold = self._cold_window()
        if cold is None:
            return "默认"
        return f"→ {cold[0]:.0f}-{cold[1]:.0f} 分钟"

    # ------------------------------------------------------------ 落盘

    def _save(self) -> None:
        try:
            self.store.set_source_state(STATE_KEY, self.to_dict())
        except Exception:  # noqa: BLE001
            logger.warning("节奏状态没存住")

    def _load(self) -> None:
        try:
            raw = self.store.get_source_state(STATE_KEY) or {}
            self._pending = list(raw.get("pending") or [])
            self._history = list(raw.get("history") or [])[-WINDOW:]
        except Exception:  # noqa: BLE001
            self._pending, self._history = [], []

    def to_dict(self) -> dict[str, Any]:
        return {"pending": self._pending, "history": self._history}

    def snapshot(self) -> dict[str, Any]:
        rate = self.reply_rate()
        return {
            "reply_rate": None if rate is None else round(rate, 2),
            "observed": len(self._history),
            "window": self._describe_gap(),
            "longing": round(self._longing(), 2),
        }
