"""开口压力（2026-10-22 她定：值驱动、不定次数，直接上线）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from attention.sources import pressure as P
from attention.sources.pressure import PressureSource

T0 = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)


class _Store:
    def __init__(self):
        self.d = {}

    def get_source_state(self, k):
        return self.d.get(k)

    def set_source_state(self, k, v):
        self.d[k] = v


class _Sessions:
    def __init__(self, at=None):
        self.at = at

    def recent(self, limit=1, clean_only=True):
        return [SimpleNamespace(id="s")] if self.at else []

    def last_user_at(self, sid):
        return self.at


def _drive(name, v, because=()):
    return SimpleNamespace(name=name, intensity=v, because=list(because))


def _run(src, start, minutes):
    out = []
    for i in range(minutes + 1):
        out += [(i, s) for s in src.poll(start + timedelta(minutes=i))]
    return out


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("NOX_PRESSURE_THETA", raising=False)
    monkeypatch.delenv("NOX_PRESSURE", raising=False)


def test_心事越重想起得越快():
    calm = PressureSource(_Store(), _Sessions(), drives_ref=lambda now: {})
    heavy = PressureSource(_Store(), _Sessions(),
                           drives_ref=lambda now: {"concern": _drive("concern", 0.9), "longing": _drive("longing", 0.8)})
    first_calm = _run(calm, T0, 600)[0][0]
    first_heavy = _run(heavy, T0, 600)[0][0]
    # θ=2：只有 BASE 0.3 → 400 分钟；心事 1.7 → 2.0/小时 → 60 分钟
    assert 395 <= first_calm <= 405
    assert 55 <= first_heavy <= 65


def test_触发时带上最重的几股_并清零():
    src = PressureSource(_Store(), _Sessions(), drives_ref=lambda now: {
        "concern": _drive("concern", 0.9, ["她说的：腰"]), "longing": _drive("longing", 0.3),
        "curiosity": _drive("curiosity", 0.5), "regret": _drive("regret", 0.1)})
    for i in range(200):
        sigs = src.poll(T0 + timedelta(minutes=i))
        if sigs:
            break
    sig = sigs[0]
    assert sig.source == "random" and sig.payload["theta"] == 2.0
    assert [d["name"] for d in sig.payload["drives"]] == ["concern", "curiosity", "longing"]
    assert sig.payload["drives"][0]["because"] == ["她说的：腰"]
    assert src.value == 0.0


def test_她说话了清零():
    ses = _Sessions(at=T0 - timedelta(hours=1))
    src = PressureSource(_Store(), ses, drives_ref=lambda now: {"x": _drive("x", 1.7)})
    _run(src, T0, 50)
    assert src.value > 1.5
    ses.at = T0 + timedelta(minutes=51)
    src.poll(T0 + timedelta(minutes=52))
    assert src.value < 0.1


def test_重启后不一口气补几小时():
    st = _Store()
    src = PressureSource(st, _Sessions(), drives_ref=lambda now: {"x": _drive("x", 1.7)})
    src.poll(T0)
    again = PressureSource(st, _Sessions(), drives_ref=lambda now: {"x": _drive("x", 1.7)})
    assert again.poll(T0 + timedelta(hours=6)) == []
    assert again.value <= 2.0 * 5 / 60 + 1e-6


def test_θ可调():
    import os
    os.environ["NOX_PRESSURE_THETA"] = "1.0"
    try:
        src = PressureSource(_Store(), _Sessions(), drives_ref=lambda now: {"x": _drive("x", 1.7)})
        assert 28 <= _run(src, T0, 100)[0][0] <= 32
    finally:
        del os.environ["NOX_PRESSURE_THETA"]


def test_节奏冷档让它慢下来():
    class _Cold:
        def pace(self, at):
            return 0.5
    src = PressureSource(_Store(), _Sessions(), rhythm=_Cold(), drives_ref=lambda now: {"x": _drive("x", 1.7)})
    assert 115 <= _run(src, T0, 200)[0][0] <= 125


def test_rhythm_pace_不含想念_冷档减速():
    from attention.rhythm import RhythmModulator
    r = RhythmModulator(_Store(), longing_ref=lambda: SimpleNamespace(value=1.0))
    assert r.pace(T0) == 1.0, "想念已经在压力里了，pace 不能再乘它"
    r._history = [{"replied": False}] * 5
    assert r.pace(T0) == pytest.approx(110 / 540)
    r._history = [{"replied": True}] * 5
    r.window_bias = lambda at: 2.0
    assert r.pace(T0) == 1.3


def test_醒着不吃8次配额_睡着的额度照旧(tmp_path):
    from attention.care import her_state
    from attention.service import AWAKE_DAILY_MAX, AttentionService
    from attention.store import AttentionStore
    svc = AttentionService.__new__(AttentionService)
    svc.store = AttentionStore(tmp_path / "a.db")
    awake = SimpleNamespace(posture="normal")
    svc._think_budget_save({"day": "2026-10-22", "awake": AWAKE_DAILY_MAX})
    now = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)
    assert svc._think_budget_blocks(awake, now) is not None
    assert svc._think_budget_blocks(awake, now, by_pressure=True) is None
    asleep = SimpleNamespace(posture=her_state.ASLEEP)
    svc._think_budget_save({"day": "2026-10-22", "awake": 0, "night": "2026-10-22",
                            "night_budget": 0, "night_used": 0})
    assert svc._think_budget_blocks(asleep, now, by_pressure=True) is not None
    svc.store.close()


def test_开场白里有为什么是现在():
    from attention.service import _pressure_note
    note = _pressure_note([{"name": "concern", "intensity": 0.9, "because": ["她说的：腰", "她说的：月经"]},
                           {"name": "longing", "intensity": 0.4, "because": []}])
    assert note == "这会儿你心里最重的是：担心她（她说的：腰、她说的：月经）；想她。"
