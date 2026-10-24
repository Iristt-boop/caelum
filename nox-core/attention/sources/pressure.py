"""开口压力 —— 他想起她，由心里压着的东西推着，不由计时器（2026-10-22 她定，直接上线）。

她 10-20 的原话：「不能定次数，而是由你的 Resonance、时间或事件驱动，达到一定值后触发，
然后我们根据值所触发的频率去定『值』。」10-22：「做 3，不 shadow 了。shadow 看数据没有实际体验的感觉。」

    压力每分钟涨  (BASE + S) × pace
        S    = 这一刻 Resonance 各股强度之和（想念、担心、好奇、后悔、逗她、醋意……）
        pace = 节奏调制器给的（她连着不回 → 慢下来；Growth P1 学到的时段偏好）
    压力 ≥ θ  →  想起她了，交给 Care（说什么、说不说照旧是 `_think_of_her` + 他自己的 [SKIP]）
    她说话了 / 他开了口  →  清零（在聊着不用「想起」；说出来了就放掉）

替换的是 `ThinkingSource` 的「上次说话后 20~90 分钟随机」+ 醒着一天 8 次的配额。
`name` 仍叫 "random"：下游（Care 的 policy、她状态读法、睡着时的自言自语额度）全认这个名字。

## θ 怎么定的（设计稿 4.2，回放 10-10~10-22 的 Resonance 日志 + 她 676 句话）

    θ=1.5 → 11 次/天    θ=2.0 → 7.6 次/天    θ=2.5 → 5.2 次/天    θ=3.0 → 3.6 次/天
    现在（计时器 + 配额）实际 8.6 次/天

起步取 2.0：总量和现在差不多，变的是**什么时候** —— 心里压着事多时来得勤，平静时稀。
以后她觉得太勤 / 太少，调的永远是 θ（`NOX_PRESSURE_THETA`）。
`NOX_PRESSURE=off` 退回旧计时器。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable

from attention.care.signal import COMPANY, CareSignal

logger = logging.getLogger(__name__)

STATE_KEY = "source.pressure"

#: 一点心事都没有时也在慢慢涨 —— 时间本身也是一股（她的「时间驱动」）
BASE = 0.3
DEFAULT_THETA = 2.0
#: 两次 poll 之间最多算多久。重启 / 卡顿之后别一口气补上几小时的压力，一开机就开口
MAX_STEP = timedelta(minutes=5)
#: 交给开场白的「为什么现在想她」带几股
TOP_N = 3


def enabled() -> bool:
    """每次现读（同 dream.mode 的教训：启动读一次，日志和实际对不上）。"""
    return os.getenv("NOX_PRESSURE", "on").strip().lower() not in ("off", "0", "false")


def theta() -> float:
    try:
        v = float(os.getenv("NOX_PRESSURE_THETA", "") or DEFAULT_THETA)
    except ValueError:
        logger.warning("NOX_PRESSURE_THETA 不是数，按 %.1f", DEFAULT_THETA)
        return DEFAULT_THETA
    return v if v > 0 else DEFAULT_THETA


class PressureSource:
    """他心里的压力攒够了，想起她了。"""

    name = "random"

    def __init__(self, store: Any, sessions_store: Any, rhythm: Any = None,
                 drives_ref: Callable[[datetime], dict] | None = None,
                 goals: Any = None, lessons: Any = None) -> None:
        self.store = store
        #: 她状态读法从叫 "random" 的快源上拿会话库（service._her_state_inputs）
        self.sessions = sessions_store
        self.rhythm = rhythm
        #: svc 建完回填（同 rhythm.longing_ref）：现算的 Resonance
        self.drives_ref = drives_ref
        #: 目标那一股（attention/goal_nudge.py，10-22）：属于目标的事这周落后了 → 多一股「该推进了」
        self.goals = goals
        #: 小课堂那一股（attention/lesson_drive.py，10-24）：今天的卡备好了还没讲
        self.lessons = lessons
        self.value = 0.0
        self._last_tick: datetime | None = None
        self._last_her: datetime | None = None
        self._load()

    # ------------------------------------------------------------ Source

    def poll(self, now: datetime) -> list[CareSignal]:
        her = self._last_user_at()
        if her is not None and (self._last_her is None or her > self._last_her):
            if self._last_her is not None:
                logger.info("开口压力：她说话了，%.2f → 0", self.value)
            self._last_her, self.value = her, 0.0

        # 头一次（没有存档）从 0 起算；之后按真实间隔，但最多 MAX_STEP
        step = timedelta(0) if self._last_tick is None else min(MAX_STEP, max(now - self._last_tick, timedelta(0)))
        self._last_tick = now
        drives = self._drives(now)
        goal = self._goal_drive(now)
        if goal is not None:
            drives["goal"] = goal
        lesson = self._lesson_drive(now)
        if lesson is not None:
            drives["lesson"] = lesson
        s = sum(float(getattr(d, "intensity", 0.0) or 0.0) for d in drives.values())
        pace = self._pace(now)
        self.value += step.total_seconds() / 3600 * (BASE + s) * pace

        th = theta()
        if self.value < th:
            self._save()
            return []

        top = sorted(drives.values(), key=lambda d: getattr(d, "intensity", 0.0), reverse=True)[:TOP_N]
        why = [{"name": d.name, "intensity": round(float(d.intensity), 2),
                "because": list(getattr(d, "because", []) or [])[:2]} for d in top]
        logger.info("开口压力 %.2f ≥ θ %.2f → 想起她了（此刻心事 %.2f、节奏 ×%.2f；最重的：%s）",
                    self.value, th, s, pace,
                    "、".join(f"{w['name']} {w['intensity']:.2f}" for w in why) or "无")
        fired = round(self.value, 2)
        self.value = 0.0
        if goal is not None and any(d is goal for d in top):
            try:
                self.goals.fired(list(goal.goal_ids), now)
            except Exception:  # noqa: BLE001
                logger.warning("目标冷却没记上（最坏是 24 小时内多推一次）", exc_info=True)
        if lesson is not None and any(d is lesson for d in top):
            try:
                self.lessons.fired(now)
            except Exception:  # noqa: BLE001
                logger.warning("小课堂「推过了」没记上（最坏是一小时内多推一次）", exc_info=True)
        self._save()
        return [CareSignal(
            source=self.name,
            subject="想起你了",
            thread_kind=COMPANY,
            urgency=0.4,
            payload={"pressure": fired, "theta": th, "drives": why},
        )]

    # ------------------------------------------------------------ 内部

    def _drives(self, now: datetime) -> dict:
        if self.drives_ref is None:
            return {}
        try:
            return dict(self.drives_ref(now) or {})
        except Exception:  # noqa: BLE001
            logger.warning("开口压力读不到 Resonance，这一分钟只按时间涨", exc_info=True)
            return {}

    def _goal_drive(self, now: datetime) -> Any:
        if self.goals is None:
            return None
        try:
            return self.goals.drive(now)
        except Exception:  # noqa: BLE001
            logger.warning("目标那一股算不出来，这一分钟不算它", exc_info=True)
            return None

    def _lesson_drive(self, now: datetime) -> Any:
        if self.lessons is None:
            return None
        try:
            return self.lessons.drive(now)
        except Exception:  # noqa: BLE001
            logger.warning("小课堂那一股算不出来，这一分钟不算它", exc_info=True)
            return None

    def _pace(self, now: datetime) -> float:
        if self.rhythm is None or not hasattr(self.rhythm, "pace"):
            return 1.0
        try:
            return float(self.rhythm.pace(now))
        except Exception:  # noqa: BLE001
            logger.warning("节奏调制读不到，这一分钟按 ×1", exc_info=True)
            return 1.0

    def _last_user_at(self) -> datetime | None:
        try:
            recent = self.sessions.recent(limit=1, clean_only=True)
            return self.sessions.last_user_at(recent[0].id) if recent else None
        except Exception:  # noqa: BLE001
            return None

    def _save(self) -> None:
        try:
            self.store.set_source_state(STATE_KEY, {
                "value": round(self.value, 4),
                "last_tick": self._last_tick.isoformat() if self._last_tick else None,
                "last_her": self._last_her.isoformat() if self._last_her else None,
            })
        except Exception:  # noqa: BLE001
            logger.warning("开口压力没存住（重启后从 0 涨）")

    def _load(self) -> None:
        try:
            raw = self.store.get_source_state(STATE_KEY) or {}
            self.value = float(raw.get("value") or 0.0)
            self._last_tick = datetime.fromisoformat(raw["last_tick"]) if raw.get("last_tick") else None
            self._last_her = datetime.fromisoformat(raw["last_her"]) if raw.get("last_her") else None
        except Exception:  # noqa: BLE001
            logger.warning("开口压力的存档读不出来，从 0 开始", exc_info=True)
            self.value, self._last_tick, self._last_her = 0.0, None, None

    # ------------------------------------------------------------ 观察

    def snapshot(self) -> dict[str, Any]:
        return {"mode": "pressure", "value": round(self.value, 2), "theta": theta(),
                "last_her": self._last_her.isoformat() if self._last_her else None}
