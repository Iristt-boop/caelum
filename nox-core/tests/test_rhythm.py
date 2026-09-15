"""节奏调制的测试。

钉四样东西：

1. **负反馈要等观察** —— 少于 3 条不动默认窗口，冷启动不冷场。
2. **档位跳变** —— 回复率高照旧、有点冷三倍、很冷收敛到半天量级；
   她重新回话自动恢复。
3. **睡觉不记成冷落** —— 判定时刻她在睡就顺延（longing 踩过的坑）。
4. **topic 解饿** —— 两道闸都不吃（时间醒来三时段吃满额度也轮不到它饿死），
   代价是自带的节流：一天最多一条、夜里到点顺延。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from attention.care import CareOrchestrator, CareSignal, SourcePolicy, ThreadBook
from attention.rhythm import (
    COLDER_WINDOW_MIN,
    COLD_WINDOW_MIN,
    RhythmModulator,
)
from attention.sources.thinking import MAX_GAP_MIN, MIN_GAP_MIN, ThinkingSource
from topic_pool.care import TopicSource

_CST = timezone(timedelta(hours=8))
#: 周二 15:00 CST —— 她醒着
T_DAY = datetime(2026, 9, 8, 15, 0, tzinfo=_CST)
#: 03:00 CST —— 她睡着
T_NIGHT = datetime(2026, 9, 8, 3, 0, tzinfo=_CST)


class _Store:
    def __init__(self) -> None:
        self._d: dict[str, Any] = {}

    def get_source_state(self, k: str) -> Any:
        return self._d.get(k)

    def set_source_state(self, k: str, v: Any) -> None:
        self._d[k] = v


class _Longing:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value


def _mod(history: list[bool] | None = None, longing: float = 0.0) -> RhythmModulator:
    """造一个调制器。history 直接塞进已判定窗口（True=她理了）。"""
    m = RhythmModulator(_Store(), longing_ref=lambda: _Longing(longing))
    for replied in (history or []):
        m._history.append({"at": T_DAY.isoformat(), "replied": replied})
    return m


def _feed(m: RhythmModulator, replied: bool, spoke_at: datetime,
         reply_within_h: float | None = 1.0) -> None:
    """走一遍真实流程灌一条观察：开口 → 她回/不回 → 到期判定。"""
    m.on_spoke(spoke_at)
    if replied and reply_within_h is not None:
        m.on_contact(spoke_at + timedelta(hours=reply_within_h))
    m.tick(spoke_at + timedelta(hours=5))


# ---------------------------------------------------------------- 冷启动


def test_冷启动只用正反馈():
    m = _mod(history=[], longing=0.5)
    lo, hi = m.gap_window(MIN_GAP_MIN, MAX_GAP_MIN)
    f = 1.25 - 0.5 * 0.5  # longing 0.5 → ×1.0
    assert (lo, hi) == (MIN_GAP_MIN * f, MAX_GAP_MIN * f), "观察不足：只有正反馈"


# ---------------------------------------------------------------- 档位


def test_回复率高_不吃冷档():
    m = _mod(history=[True, True, True, False], longing=0.0)
    assert m._cold_window() is None, "75% 回复率不该进冷档"
    # 但正反馈永远在：不怎么想她（longing=0）→ 默认窗口 ×1.25
    assert m.gap_window(MIN_GAP_MIN, MAX_GAP_MIN) == (MIN_GAP_MIN * 1.25,
                                                      MAX_GAP_MIN * 1.25)


def test_有点冷_拉到三倍档():
    m = _mod(history=[True, False, False, True, False], longing=0.0)
    assert 0.2 <= m.reply_rate() < 0.6
    assert m._cold_window() == COLD_WINDOW_MIN


def test_很冷_收敛档():
    m = _mod(history=[False, False, False, False], longing=0.0)
    assert m.reply_rate() == 0.0
    assert m._cold_window() == COLDER_WINDOW_MIN


def test_她重新回话_自动恢复():
    m = _mod(history=[False, False, False, False, False], longing=0.0)
    assert m._cold_window() == COLDER_WINDOW_MIN
    # 接下来她连着回了 —— 旧观察滚出窗口，档位自己回去
    for i in range(5):
        _feed(m, replied=True, spoke_at=T_DAY + timedelta(days=i))
    assert m._cold_window() is None, "回复率回升后窗口要自己恢复"


# ---------------------------------------------------------------- 判定


def test_超时没回判冷_她说话判暖():
    m = _mod(history=[], longing=0.0)
    _feed(m, replied=False, spoke_at=T_DAY)
    assert m._history[-1]["replied"] is False

    _feed(m, replied=True, spoke_at=T_DAY + timedelta(days=1))
    assert m._history[-1]["replied"] is True


def test_判定时刻她在睡_顺延不判():
    m = _mod(history=[], longing=0.0)
    # 开口在白天，5 小时后到期 —— 但判定点在凌晨 3 点（她在睡，
    # longing 的睡眠窗是 01:00–10:00，不是 gate 的 01:00–09:00）
    m.on_spoke(T_DAY.replace(hour=22))
    m.tick(T_DAY.replace(hour=22) + timedelta(hours=5))
    assert m._pending, "她在睡不许判定，继续等"
    # 上午 11 点再 tick —— 醒了，这时才判
    m.tick(T_NIGHT + timedelta(days=1, hours=8))
    assert m._pending == [] and m._history[-1]["replied"] is False


def test_正反馈_想念压缩窗口和急迫度():
    cold = _mod(history=[False, False, True], longing=0.0)
    warm = _mod(history=[False, False, True], longing=1.0)
    # 同一个「有点冷」档位（回复率 1/3）：longing=0 → ×1.25，longing 满 → ×0.75
    assert cold.gap_window(60, 180) == (75.0, 225.0)
    assert warm.gap_window(60, 180) == (45.0, 135.0)
    assert warm.urgency_boost() == 1.2 > cold.urgency_boost() == 0.8


# ---------------------------------------------------------------- 源集成


def test_惦记源吃调制后的窗口():
    store = _Store()
    m = _mod(history=[False, False, False, False], longing=1.0)
    src = ThinkingSource(store, sessions_store=None, rhythm=m)
    src._schedule(T_DAY)
    gap = src._next_at - T_DAY
    lo = COLDER_WINDOW_MIN[0] * 0.75
    hi = COLDER_WINDOW_MIN[1] * 0.75
    assert timedelta(minutes=lo) <= gap <= timedelta(minutes=hi), \
        f"窗口没吃调制：gap={gap}"


def test_话题源_一天最多一条():
    class _Pool:
        class store:  # noqa: N801
            @staticmethod
            def open_topics(now, include_surfaced=False, limit=1):
                return [_Topic()]

    class _Topic:
        id = "t1"
        hook = "测试钩子"
        source_title = "测试来源"
        source_url = ""
        category = "tech"

    src = TopicSource(_Store(), _Pool())
    src._next_at = T_DAY - timedelta(minutes=1)
    (sig,) = src.poll(T_DAY)
    assert sig.source == "topic"
    assert src.poll(T_DAY + timedelta(hours=2)) == [], "同天第二条不许再出"
    tomorrow = T_DAY + timedelta(days=1)
    src._next_at = tomorrow - timedelta(minutes=1)
    assert len(src.poll(tomorrow)) == 1, "跨天重开"


def test_话题源_夜里到点顺延():
    class _EmptyPool:
        class store:  # noqa: N801
            @staticmethod
            def open_topics(now, include_surfaced=False, limit=1):
                return []

    src = TopicSource(_Store(), _EmptyPool())
    src._next_at = T_NIGHT - timedelta(minutes=1)
    assert src.poll(T_NIGHT) == [], "夜里不产念头"
    assert src._next_at >= T_NIGHT + timedelta(hours=2), "顺延两个钟头"


def test_话题源_两道闸都不吃():
    """gate 永远拦、30 分钟前刚开过链 —— topic 照说不误（解饿回归）。"""
    said: list[str] = []
    book = ThreadBook()
    book.open("company", "刚才的链", now=T_DAY - timedelta(minutes=30))
    o = CareOrchestrator(
        book,
        lambda signal, thread, now: said.append(signal.source) or True,
        policies={"topic": SourcePolicy(takes_quota=False, takes_gate=False,
                                        max_steps=1)},
        gate_check=lambda now: "今日额度已用完",
    )
    outcome = o._decide(CareSignal(source="topic", subject="池子线头"), T_DAY)
    assert outcome.action == "spoke", f"topic 被拦了：{outcome.reason}"
    assert said == ["topic"]
