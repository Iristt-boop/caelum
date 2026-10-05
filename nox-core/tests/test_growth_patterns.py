"""Growth Loop 第 1 期：P1 时段回应度 shadow（CAELUM-GROWTH-LOOP-设计.md 四·六节）。

钉六样东西：

1. **只学相对值** —— 她整体变慢，各时段 bias 不动（整体快慢是 rhythm 的事）
2. **快回才算** —— 30 分钟内回 = 1；慢回、没回 = 0
3. **过滤** —— 她正在聊（沉默 < 20 分钟）、她在晾他（ignored）、没有情境的，都不算
4. **没攒够不算数** —— 8 条且跨 3 天才生效；一天的坏心情不能变成习惯
5. **证据会变旧** —— 两个月前的事几乎不影响现在；有上下限 0.7~1.3
6. **shadow 不改行为** —— 接了观察者，rhythm 的窗口逐字不变；观察者挂了也不连累
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from attention.rhythm import RhythmModulator
from growth import patterns
from growth.patterns import BIAS_HI, BIAS_LO, receptive, shadow

UTC = timezone.utc
CN = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 5, 12, tzinfo=CN)


def row(day: int, hour: int, *, reply_min: int | None = 10, silent: int = 60,
        posture: str = "normal", bucket: str | None = None) -> dict:
    at = datetime(2026, 10, day, hour, tzinfo=CN)
    ctx = {"bucket": bucket or patterns.bucket(at), "posture": posture, "silent_min": silent}
    if reply_min is not None:
        ctx["reply_min"] = reply_min
    return {"at": at.isoformat(), "context": ctx, "outcome": 1.0 if reply_min is not None else 0.0}


def week(hour: int, reply_min: int | None, days=range(1, 5), per_day: int = 2) -> list[dict]:
    """某个时段连着几天、每天几条。"""
    return [row(d, hour, reply_min=reply_min) for d in days for _ in range(per_day)]


# ---------------------------------------------------------------- 1. 只学相对值


def _mix(hour: int, fast_every: int) -> list[dict]:
    """某个时段 4 天 × 每天 4 条，每 `fast_every` 条里有 1 条快回，其余 80 分钟才回。"""
    return [row(d, hour, reply_min=10 if i % fast_every == 0 else 80)
            for d in range(1, 5) for i in range(4)]


def test_她整体都慢了一半_各时段bias不动():
    """节后她整体回得慢，那是 rhythm 的事；P1 的形状（深夜比白天快）不该全体往下掉。"""
    before = receptive(_mix(22, 1) + _mix(10, 2) + _mix(15, 2), NOW)   # 深夜全快、白天一半快
    after = receptive(_mix(22, 2) + _mix(10, 4) + _mix(15, 4), NOW)    # 每个时段都慢了一半
    for b in before:
        assert abs(before[b].bias - after[b].bias) < 1e-9, (b, before[b], after[b])
    assert before["21-1"].bias > 1.0 > before["9-12"].bias


def test_一次快回都没有_全体中性():
    got = receptive(week(10, 50) + week(15, 50) + week(22, None), NOW)
    assert all(p.bias == 1.0 for p in got.values()), "没有「相对」可言"


def test_深夜回得快_白天慢_深夜往上白天往下():
    rows = week(22, 10) + week(10, 80) + week(15, 80)
    got = receptive(rows, NOW)
    assert got["21-1"].bias > 1.05, got["21-1"]
    assert got["9-12"].bias < 1.0 and got["14-18"].bias < 1.0


# ---------------------------------------------------------------- 2. 快回才算


def test_三十分钟内回才算快_没回不算():
    rows = week(22, 30) + week(10, 31) + week(15, None)
    got = receptive(rows, NOW)
    assert got["21-1"].fast_rate == 1.0
    assert got["9-12"].fast_rate == 0.0 and got["14-18"].fast_rate == 0.0


def test_连着二十次没回_这个时段往下走():
    rows = [row(1 + i % 5, 15, reply_min=None) for i in range(20)] + week(10, 10) + week(22, 10)
    p = receptive(rows, NOW)["14-18"]
    assert p.effective and p.bias < 1.0, p


# ---------------------------------------------------------------- 3. 过滤


def test_她正在聊_她在晾他_没有情境的_都不算():
    base = week(10, 80) + week(22, 80)
    noise = ([row(2, 15, reply_min=1, silent=5) for _ in range(10)]
             + [row(3, 15, reply_min=1, posture="ignored") for _ in range(10)]
             + [{"at": NOW.isoformat(), "context": {"reply_min": 1}, "outcome": 1.0}] * 10)
    assert "14-18" not in receptive(base + noise, NOW)
    assert "14-18" in receptive(base + [row(2, 15, reply_min=1, silent=20)], NOW), "20 分钟整算"


# ---------------------------------------------------------------- 4. 没攒够不算数


def test_同一天八条不生效_跨三天才生效():
    same_day = [row(4, 15) for _ in range(8)] + week(10, 80)
    assert not receptive(same_day, NOW)["14-18"].effective
    spread = [row(2 + i % 3, 15) for i in range(8)] + week(10, 80)
    assert receptive(spread, NOW)["14-18"].effective
    seven = [row(2 + i % 3, 15) for i in range(7)] + week(10, 80)
    assert not receptive(seven, NOW)["14-18"].effective


# ---------------------------------------------------------------- 5. 证据变旧 + 上下限


def test_两个月前的事几乎不影响现在():
    old = [{**r, "at": (datetime.fromisoformat(r["at"]) - timedelta(days=60)).isoformat()}
           for r in week(22, 10, per_day=10)]
    rows = old + week(22, 80) + week(10, 80) + week(15, 10)
    p = receptive(rows, NOW)["21-1"]
    assert p.bias < 1.0, f"两个月前深夜回得快，最近深夜回得慢 —— 该听最近的：{p}"


def test_bias_永远在上下限之内():
    """深夜次次秒回、白天两段从来不回 —— 不夹的话深夜 rel≈2.9、bias≈1.58。"""
    rows = ([row(1 + i % 4, 22, reply_min=1) for i in range(200)]
            + [row(1 + i % 4, 10, reply_min=None) for i in range(200)]
            + [row(1 + i % 4, 15, reply_min=None) for i in range(200)])
    got = receptive(rows, NOW)
    assert got["21-1"].rel > 2.5, "构造的数据要真能顶到上限，不然这条测试是假的"
    assert got["21-1"].bias == BIAS_HI
    assert all(BIAS_LO <= p.bias <= BIAS_HI for p in got.values()), got


# ---------------------------------------------------------------- 6. shadow 不改行为


class _Store:
    def __init__(self, rows=None):
        self._d, self.rows = {}, rows or []

    def get_source_state(self, k):
        return self._d.get(k)

    def set_source_state(self, k, v):
        self._d[k] = v

    def list_experiences(self, kind=None):
        return self.rows


def test_接了观察者_窗口逐字不变_只在真摇时间时通知():
    plain = RhythmModulator(_Store())
    watched = RhythmModulator(_Store())
    seen: list = []
    watched.window_observer = lambda **kw: seen.append(kw)
    assert watched.gap_window(20, 90) == plain.gap_window(20, 90)
    assert seen == [], "快照接口也调 gap_window —— 不传 why 就不许通知，否则每读一次状态记一行"
    at = datetime(2026, 10, 5, 22, tzinfo=CN)
    assert watched.gap_window(20, 90, why="惦记", at=at) == plain.gap_window(20, 90)
    assert len(seen) == 1 and seen[0]["why"] == "惦记" and seen[0]["at"] == at
    assert seen[0]["cold"] is False


def test_观察者挂了_窗口照常_而且留痕(caplog):
    m = RhythmModulator(_Store())

    def boom(**_):
        raise RuntimeError("账本锁了")

    m.window_observer = boom
    with caplog.at_level(logging.ERROR, logger="attention.rhythm"):
        assert m.gap_window(20, 90, why="惦记") == RhythmModulator(_Store()).gap_window(20, 90)
    assert any("观察者失败" in r.getMessage() for r in caplog.records)


def test_shadow_日志说得出本来会是多少(caplog):
    rows = week(22, 10, days=range(1, 5)) + week(10, 80) + week(15, 80)
    store = _Store(rows)
    at = datetime(2026, 10, 5, 22, tzinfo=CN)
    with caplog.at_level(logging.INFO, logger="growth.patterns"):
        shadow(store, why="惦记", at=at, window=(20.0, 90.0), cold=False)
        shadow(store, why="惦记", at=at, window=(60.0, 180.0), cold=True)
        shadow(store, why="惦记", at=datetime(2026, 10, 5, 13, tzinfo=CN), window=(20.0, 90.0), cold=False)
    lines = [r.getMessage() for r in caplog.records]
    bias = receptive(rows, datetime.now(UTC))["21-1"].bias
    assert "21-1" in lines[0] and f"本来会是 {20 / bias:.0f}-{90 / bias:.0f} 分钟" in lines[0], lines[0]
    assert "冷档优先" in lines[1]
    assert "12-14" in lines[2] and "没有证据" in lines[2]


def test_惦记和话题摇时间时都会通知_快照不会():
    """why 被谁删了，shadow 会悄悄停掉而测试全绿 —— 所以从两个真的源去调。"""
    from attention.sources.thinking import ThinkingSource
    from topic_pool.care import TopicSource

    seen: list = []
    m = RhythmModulator(_Store())
    m.window_observer = lambda **kw: seen.append(kw["why"])
    think = ThinkingSource(_Store(), sessions_store=None, rhythm=m)
    topic = TopicSource(_Store(), pool=None, rhythm=m)
    anchor = datetime(2026, 10, 5, 15, tzinfo=CN)
    think._schedule(anchor)
    topic._schedule(anchor)
    assert seen == ["惦记", "话题"], seen
    think.snapshot()
    topic.snapshot()
    assert seen == ["惦记", "话题"], "读状态不许记 shadow"
