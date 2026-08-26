"""时间醒来 + 统一闸的集成测试：额度共享、唤醒链独立。纯逻辑，不打网络。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.events import ExperienceEvent  # noqa: E402
from attention.gate import DailyGate  # noqa: E402
from attention.intent import Intent  # noqa: E402
from attention.scheduler import SchedulerDecision  # noqa: E402
from attention.service import AttentionService  # noqa: E402
from attention.sources.times import TimeWakeSource  # noqa: E402
from attention.store import AttentionStore  # noqa: E402

CN = timezone(timedelta(hours=8), "CST")


def _cn(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=CN)


class FakeStore:
    """内存版 AttentionStore 接口（不真开 SQLite）。"""

    def __init__(self):
        self.attentions = []
        self.intents = []
        self.wakeups = []
        self.source_state: dict[str, dict] = {}

    def load(self):
        from attention.registry import AttentionRegistry
        return AttentionRegistry.from_list([])

    def save(self, registry):
        self.attentions = registry.to_list()
        return len(self.attentions)

    def load_intents(self):
        from attention.intent import IntentEngine
        return IntentEngine.from_list(self.intents)

    def save_intents(self, engine):
        self.intents = engine.to_list()
        return len(self.intents)

    def load_wakeups(self):
        from attention.wakeup import WakeBook
        return WakeBook.from_list(self.wakeups)

    def save_wakeups(self, book):
        self.wakeups = book.to_list()
        return len(self.wakeups)

    def get_source_state(self, key):
        return self.source_state.get(key)

    def set_source_state(self, key, value):
        self.source_state[key] = value


class FakeProvider:
    def get_state(self, turn=None, force_refresh=False):
        return {"available": False, "has_data": False}


class RecordingSpeaker:
    """记下被调用的 intent，代替真推送。"""

    def __init__(self):
        self.calls: list[Intent] = []

    def __call__(self, intent, decision):
        self.calls.append(intent)


def _make_service(speaker=None, time_wakes="", quota=3):
    store = FakeStore()
    gate = DailyGate(daily_quota=quota)
    ts = TimeWakeSource(time_wakes, store) if time_wakes else None
    svc = AttentionService(
        store, FakeProvider(),
        speaker=speaker, gate=gate, time_source=ts,
    )
    return svc, store


# ---------------------------------------------------------------- 时间醒来


def test_time_wake_fires_and_counts_quota():
    sp = RecordingSpeaker()
    svc, _ = _make_service(sp, time_wakes="12:00:午饭")
    now = _cn(2026, 8, 8, 12, 0)
    svc.tick(now)
    assert len(sp.calls) == 1
    assert sp.calls[0].subject == "午饭"
    # 额度记了一次
    assert svc.gate.can_speak(now).spoken_today == 1


def test_time_wake_blocked_when_quota_exhausted():
    sp = RecordingSpeaker()
    svc, _ = _make_service(sp, time_wakes="12:00:午饭", quota=1)
    # 先把唯一额度用掉
    svc.gate.note_spoke(_cn(2026, 8, 8, 9, 0))
    svc.tick(_cn(2026, 8, 8, 12, 0))
    assert len(sp.calls) == 0  # 被闸拦下，没说


def test_time_wake_blocked_in_quiet_hours():
    sp = RecordingSpeaker()
    svc, _ = _make_service(sp, time_wakes="03:00:深夜")
    svc.tick(_cn(2026, 8, 8, 3, 0))
    assert len(sp.calls) == 0  # 安静时段，不说


def test_time_wake_dry_run_does_not_call_speaker():
    """没有 speaker（dry-run）时只想不推。"""
    svc, _ = _make_service(None, time_wakes="12:00:午饭")
    svc.tick(_cn(2026, 8, 8, 12, 0))
    assert svc.dry_run is True


# ---------------------------------------------------------------- 唤醒链不占额度


def test_wakeup_chain_does_not_consume_gate_quota():
    """唤醒链（对话延续）不占统一闸额度 —— 这是重构的核心修正。"""
    from attention.wakeup import WakeBook, Wakeup

    sp = RecordingSpeaker()
    store = FakeStore()
    gate = DailyGate(daily_quota=1)  # 只有 1 次额度（给时间醒来/睡眠用）

    book = WakeBook.from_list([])
    w = Wakeup(session_id="s1", why="她去吃饭了",
               wake_at=_cn(2026, 8, 8, 13, 0), count=0)
    book.add = lambda sid, why, after_min, now=None: w  # 简化：直接有纸条

    class FakeWaker:
        def __call__(self, wake_book, now):
            # 模拟唤醒链真的开口了 5 次（MAX_CHAIN 内）
            for _ in range(5):
                gate.note_spoke(now) if False else None  # 唤醒链不碰 gate
            return 5

    svc = AttentionService(store, FakeProvider(), speaker=sp, gate=gate,
                           waker=FakeWaker())
    svc.tick(_cn(2026, 8, 8, 14, 0))
    # 唤醒链跑完，gate 额度原封不动
    assert gate.can_speak(_cn(2026, 8, 8, 14, 0)).spoken_today == 0
