"""World Model 第一阶段。

守的是架构文档 v1.3 第 4 节那三条硬规矩，外加一条今天才发现的：

1. 原始 Observation 永不改写
2. 事实和推断分层（`5h20m` 是观察，`poor` 是判断）
3. 来源是强制字段，能顺着 `based_on` 回溯
4. 🔴 **没有变化的日子也要留档** —— 这条最容易漏，见
   `test_boring_days_are_still_recorded`

不打网络，全同步。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from world_model import WorldModel  # noqa: E402
from world_model.types import FRESH, STALE, UNKNOWN, Observation  # noqa: E402

CST = timezone(timedelta(hours=8))
T0 = datetime(2026, 8, 16, 9, 0, tzinfo=CST)


@pytest.fixture()
def wm(tmp_path):
    m = WorldModel(tmp_path / "world.db")
    yield m
    m.store.close()


def _sleep(wm, day: int, hours: float, now: datetime | None = None):
    d = f"2026-08-{day:02d}"
    return wm.observe(
        source="health", type="sleep_duration",
        observed={"value": hours, "unit": "hour", "sleep_date": d},
        observed_at=now or datetime(2026, 8, day, 9, 0, tzinfo=CST),
        dedup_key=f"sleep/{d}",
    )


# ---------------------------------------------------------------- observe


def test_observe_and_read_back(wm):
    obs = _sleep(wm, 16, 5.3)
    assert obs is not None
    got = wm.query("sleep_duration")
    assert len(got) == 1
    assert got[0].raw["value"] == 5.3
    assert got[0].source == "health"          # 来源是强制字段


def test_same_night_is_idempotent(wm):
    """HealthProvider 的 ttl 是 6 小时，一天会 poll 好几次 ——
    同一觉不能存成好几条，否则趋势会被自己刷爆。"""
    assert _sleep(wm, 16, 5.3) is not None
    assert _sleep(wm, 16, 5.3) is None        # 第二次被 dedup_key 挡掉
    assert len(wm.query("sleep_duration")) == 1


def test_observation_is_frozen():
    """原始事实不许改写（架构文档第 4 节第一条）。"""
    obs = Observation(source="health", type="sleep_duration",
                      observed={"value": 5.3}, observed_at=T0)
    with pytest.raises(Exception):
        obs.observed = {"value": 8.0}          # type: ignore[misc]


def test_naive_datetime_is_rejected():
    """没有时区的时间戳混进来，跨天判断和 TTL 全会错。"""
    with pytest.raises(ValueError):
        Observation(source="health", type="x", observed={},
                    observed_at=datetime(2026, 8, 16, 9, 0))


# ---------------------------------------------------------------- state


def test_state_is_fresh_then_stale(wm):
    _sleep(wm, 16, 5.3)
    st = wm.get_state("sleep_duration", now=datetime(2026, 8, 16, 20, 0, tzinfo=CST))
    assert st.status == FRESH

    # 36 小时之后就不能再当成当前值说出去
    st = wm.get_state("sleep_duration", now=datetime(2026, 8, 18, 20, 0, tzinfo=CST))
    assert st.status == STALE
    assert "没有新数据" in st.describe()       # 🔴 不许伪装成 current


def test_unknown_is_explicit(wm):
    """查一个从没观察过的东西，要明确说「不知道」，不能返回 None
    让调用方自己猜 —— 「不知道」和「正常」混起来会让他说错话。"""
    st = wm.get_state("weight")
    assert st.status == UNKNOWN
    assert "不知道" in st.describe()


def test_backfill_does_not_move_current_state(wm):
    """补录一条旧数据，不该把「当前状态」改回过去。"""
    _sleep(wm, 16, 5.3)
    _sleep(wm, 10, 8.0)                        # 补录六天前的
    st = wm.get_state("sleep_duration", now=datetime(2026, 8, 16, 20, 0, tzinfo=CST))
    assert st.value["value"] == 5.3            # 还是最新那条
    assert len(wm.query("sleep_duration")) == 2  # 但两条都留着


# ---------------------------------------------------------------- query


def test_query_is_newest_first_and_windowed(wm):
    for d, h in [(12, 5.0), (13, 5.2), (14, 6.9), (15, 5.1), (16, 4.8)]:
        _sleep(wm, d, h)
    rows = wm.query("sleep_duration", days=3,
                    now=datetime(2026, 8, 16, 20, 0, tzinfo=CST))
    vals = [r.raw["value"] for r in rows]
    assert vals == [4.8, 5.1, 6.9]             # 新→旧，且只要窗口内的


def test_evidence_carries_provenance(wm):
    """返回的是**带来源的证据**，不是拼好的一段文本 ——
    他要能回答「你怎么知道的」。"""
    _sleep(wm, 16, 5.3)
    ev = wm.query("sleep_duration")[0]
    assert ev.kind == "observed"
    assert ev.source == "health"
    assert ev.reference.startswith("health://sleep_duration/")
    assert ev.observed_at.tzinfo is not None
