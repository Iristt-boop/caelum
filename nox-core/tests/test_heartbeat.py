"""后台心跳台账（审计 1.4）。

## 这里钉住的是什么

三条后台循环各自死掉的症状都**不是报错**：他不再主动找她、位置跃迁没了、
话题池悄悄变空。它们都用 `except Exception: 下一轮继续` 兜着 —— 那是对的，
但也意味着「连着失败一千次」和「一切正常」在外面长得一模一样。

所以最要紧的两条用例是两个反向的错误：
  · **停了却报正常** —— 这套东西白做
  · **刚启动就报停了** —— 每次部署都红一片，红几次之后没人看，等于白做

第二条同样致命，而且更容易写错。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from obs import heartbeat  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    heartbeat.reset()
    yield
    heartbeat.reset()


def test_declared_but_never_run_shows_up():
    """🔴 一条**从没成功过**的循环必须在台账里看得见。

    如果只在 beat 时登记，它就根本不存在 —— 而"一次都没跑起来"
    正是最该看见的那种坏。
    """
    heartbeat.declare("attention_tick", every_s=900)
    snap = heartbeat.snapshot()
    assert "attention_tick" in snap
    assert snap["attention_tick"]["count"] == 0
    assert snap["attention_tick"]["last_ok"] is None


def test_fresh_after_beat():
    heartbeat.declare("care_tick", every_s=60)
    heartbeat.beat("care_tick")
    j = heartbeat.snapshot()["care_tick"]
    assert j["count"] == 1
    assert j["stale"] is False
    assert j["last_ok"] is not None
    assert j["age_s"] < 5


def test_goes_stale_after_three_intervals(monkeypatch):
    """3 倍节奏之后算停了。漏一次是抖动，连漏三次是坏了。"""
    heartbeat.declare("topic_scout", every_s=1)
    heartbeat.beat("topic_scout")
    assert heartbeat.stale_jobs() == []

    # 把单调钟往前拨，而不是真的 sleep —— 测试不该花 4 秒
    base = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: base + 61)  # 过了下限 60s
    assert heartbeat.stale_jobs() == ["topic_scout"]


def test_floor_protects_fast_loops(monkeypatch):
    """秒级循环抖一下不该报警 —— 所以有 60 秒下限。"""
    heartbeat.declare("care_tick", every_s=1)
    heartbeat.beat("care_tick")
    base = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: base + 10)  # 10 倍节奏，但没到 60s
    assert heartbeat.stale_jobs() == []


def test_just_started_is_not_stale():
    """🔴 刚起来不算停了。

    一条 6 小时的循环本来就要等 6 小时才第一次跑。不处理这一点的话，
    **每次部署完 /health 都会红一片** —— 红几次之后就没人看了，
    那就又回到了这条排期要治的原点。
    """
    heartbeat.declare("topic_scout", every_s=6 * 3600)
    assert heartbeat.stale_jobs() == []
    assert heartbeat.snapshot()["topic_scout"]["stale"] is False


def test_never_ran_eventually_goes_stale(monkeypatch):
    """但一直不跑，最后还是要报出来 —— 不能永远给"刚启动"当免死金牌。"""
    heartbeat.declare("attention_tick", every_s=900)
    base = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: base + 900 * 3 + 10)
    assert heartbeat.stale_jobs() == ["attention_tick"]


def test_beat_without_declare_still_recorded(caplog):
    """漏了 declare 也不能丢数据，但要吼一声 —— 那时 stale 判定用的是瞎猜的节奏。"""
    import logging

    with caplog.at_level(logging.WARNING, logger="obs.heartbeat"):
        heartbeat.beat("野生活计")
    assert heartbeat.snapshot()["野生活计"]["count"] == 1
    assert any("没有 declare" in r.getMessage() for r in caplog.records)


def test_declare_twice_updates_rhythm_without_losing_history():
    """重新 declare（改了环境变量重启）不该把已有的计数清零。"""
    heartbeat.declare("care_tick", every_s=60)
    heartbeat.beat("care_tick")
    heartbeat.declare("care_tick", every_s=120)
    j = heartbeat.snapshot()["care_tick"]
    assert j["count"] == 1
    assert j["every_s"] == 120.0


def test_wall_clock_change_does_not_fake_staleness(monkeypatch):
    """系统时钟被改不该让一条活着的循环显示成停了 —— 年龄走单调钟。"""
    heartbeat.declare("attention_tick", every_s=900)
    heartbeat.beat("attention_tick")
    real = time.time()
    monkeypatch.setattr(time, "time", lambda: real - 86400)  # 时钟倒退一天
    assert heartbeat.stale_jobs() == []
