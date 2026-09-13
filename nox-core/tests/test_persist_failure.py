"""落盘失败要看得见（审计 2.4）。

## 它治的是"他今天怎么翻来覆去说同一件事"

`AttentionService._persist()` 在**每次开口之后**都会走到。
原来是「try 一次，失败了 `logger.exception` 就算了」——
留痕是留了，但失败之后没有任何人会知道：

    进程还活着、内存里的状态是对的、下一轮照常跑 —— 一切正常
    但盘上那份停在了开口**之前**

于是一旦在这中间重启：
  · `gate` 回到旧的 → **今天的开口额度静默归零**
  · `ledger` / `threads` 回到旧的 → 他**把刚说过的话再说一遍**

她看到的是他在念叨同一件事，而日志里只有一行几小时前的 exception。

## 三条判据

  1. 瞬时失败要能自己缓过来（重试一次）
  2. 一直失败要**看得见**：`/health` 上的 `attention_persist` 变陈旧
  3. 失败不许把这一轮 tick 带塌 —— 内存里的状态还是对的，下一轮还要接着跑
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.gate import DailyGate  # noqa: E402
from attention.service import AttentionService  # noqa: E402
from attention.store import AttentionStore  # noqa: E402
from obs import heartbeat  # noqa: E402


class _Provider:
    def get_state(self, turn=None, force_refresh=False):
        return {}


@pytest.fixture(autouse=True)
def _clean_heartbeat():
    heartbeat.reset()
    yield
    heartbeat.reset()


@pytest.fixture
def svc(tmp_path):
    store = AttentionStore(tmp_path / "attention.db")
    return AttentionService(store=store, provider=_Provider(), gate=DailyGate())


def test_persist_success_marks_heartbeat(svc):
    heartbeat.declare("attention_persist", every_s=900)
    svc._persist()
    snap = heartbeat.snapshot()["attention_persist"]
    assert snap["count"] == 1
    assert snap["stale"] is False
    assert svc._persist_fails == 0
    assert svc._persist_last_ok is not None


def test_transient_failure_recovers_on_retry(svc, caplog):
    """第一次失败、第二次成功 —— 这一轮就该算过去了，不留痕迹。

    SQLite 的失败绝大多数是瞬时锁竞争，为这个报警是给她制造噪音。
    """
    calls = {"n": 0}
    real = svc.store.save_intents

    def flaky(intents):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return real(intents)

    svc.store.save_intents = flaky
    with caplog.at_level(logging.WARNING, logger="attention.service"):
        svc._persist()

    assert calls["n"] == 2, "应该重试一次"
    assert svc._persist_fails == 0, "重试成功就不该计失败"
    assert any("马上重试" in r.getMessage() for r in caplog.records)


def test_persistent_failure_counts_and_shouts(svc, caplog):
    """一直写不进去：计数要涨，而且要吼出来 —— 不能只是 debug 一行。"""
    def always_fail(*a, **kw):
        raise RuntimeError("disk I/O error")

    svc.store.save_intents = always_fail

    with caplog.at_level(logging.ERROR, logger="attention.service"):
        svc._persist()
        svc._persist()

    assert svc._persist_fails == 2
    errs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errs, "连续失败必须是 ERROR 级，WARNING 会被日常噪音盖掉"
    assert any("重复开口" in r.getMessage() for r in errs), (
        "错误信息里要写清后果 —— 只说「落盘失败」的话，看到的人不知道该多着急"
    )


def test_persistent_failure_shows_up_as_stale(svc, monkeypatch):
    """🔴 **这条是判据**：一直写不进去，`/health` 上要看得见。

    走的就是今天装的那条链：heartbeat → `/health` 的 `background_stale`
    → bridge 的 `nox_background` → 看门狗 → 她手机。
    """
    import time

    heartbeat.declare("attention_persist", every_s=10)
    svc._persist()                       # 先成功一次，把基准打上
    assert heartbeat.stale_jobs() == []

    svc.store.save_intents = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("坏了"))
    svc._persist()
    svc._persist()

    # 时间往前拨过容忍窗口（3 倍节奏，下限 60 秒）
    base = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: base + 61)
    assert "attention_persist" in heartbeat.stale_jobs(), (
        "🔴 一直落盘失败却没在 /health 上体现 —— 这套东西白做了"
    )


def test_recovery_is_logged(svc, caplog):
    """从坏到好要留一行 —— 不然翻日志的人不知道那段失败到什么时候为止。"""
    def always_fail(*a, **kw):
        raise RuntimeError("坏了")

    real = svc.store.save_intents
    svc.store.save_intents = always_fail
    svc._persist()
    assert svc._persist_fails == 1

    svc.store.save_intents = real
    with caplog.at_level(logging.INFO, logger="attention.service"):
        svc._persist()
    assert svc._persist_fails == 0
    assert any("落盘恢复" in r.getMessage() for r in caplog.records)


def test_failure_does_not_break_the_tick(svc):
    """落盘炸了不许把这一轮带塌 —— 内存里的状态还是对的，下一轮还要接着跑。"""
    svc.store.save_intents = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("坏了"))
    svc._persist()          # 不抛
    # 内存状态没被动过
    assert svc.gate is not None
    snap = svc.snapshot()
    assert snap["persist"]["consecutive_failures"] == 1


def test_snapshot_exposes_persist_health(svc):
    """`/health` 读的是 snapshot，字段得在。"""
    svc._persist()
    p = svc.snapshot()["persist"]
    assert p["consecutive_failures"] == 0
    assert p["last_ok"] is not None
    # ISO 8601 带时区，不然事后对时间对不上
    assert datetime.fromisoformat(p["last_ok"]).tzinfo is not None


def test_store_has_busy_timeout(tmp_path):
    """拿不到锁要等，不要当场放弃。

    ⚠️ **这条挡不住"有人删掉那行 PRAGMA"** —— Python 的
    `sqlite3.connect()` 默认 `timeout=5.0` 本来就给 5000
    （2026-09-13 变异验证时发现的：删掉 PRAGMA，这条照样绿）。

    它挡的是另一件事：**有人给 connect 加了 `timeout=0`**。
    实测 `connect(timeout=0)` → `busy_timeout=0` → 一拿不到锁就
    `database is locked`。那时候这条会红。

    写清楚它能挡什么、不能挡什么 —— 一条说不清自己在防什么的测试，
    下次重构时会被当成噪音删掉。
    """
    store = AttentionStore(tmp_path / "a.db")
    got = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert got >= 5000, f"busy_timeout 是 {got}，一拿不到锁就会当场失败"


def test_store_is_wal(tmp_path):
    """顺手钉住 WAL —— 没有它，读会被写整个挡住。"""
    store = AttentionStore(tmp_path / "a.db")
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal", f"journal_mode 是 {mode}"
