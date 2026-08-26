"""CareLedger —— 他今天惦记过她几次（糖糖 2026-08-18 定的可观测性）。

钉住的是这条公式和它背后的分工：

    considered = spoke + skipped + blocked

    spoke   他说了
    skipped **他自己**想了想，觉得没什么具体的可说
    blocked **规则**不让他说（额度 / 闸 / 链内间隔）

skipped 和 blocked 混成一个数，就分不清「他不粘人」和「我们栏杆太紧」——
而那正是这份账本要回答的问题。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from attention.care import (
    CareOrchestrator,
    CareSignal,
    SourcePolicy,
    ThreadBook,
)
from attention.care.ledger import BLOCK, FAILED, SKIP, SPEAK, CareLedger

CST = timezone(timedelta(hours=8))
# 中国时间 2026-08-18 20:00
T0 = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- 账本本体


def test_公式对得上():
    led = CareLedger()
    for d in (SPEAK, SPEAK, SPEAK):
        led.record(source="random", decision=d, now=T0)
    for _ in range(5):
        led.record(source="random", decision=SKIP, now=T0)
    for _ in range(4):
        led.record(source="random", decision=BLOCK, reason="60 分钟内已经开过一条链了", now=T0)

    s = led.summary(T0)
    assert (s["spoke"], s["skipped"], s["blocked"]) == (3, 5, 4)
    assert s["considered"] == 12, "considered = 3+5+4，不是 8"


def test_failed_不进considered():
    """故障不是决策 —— 它要的是修，不是解读。"""
    led = CareLedger()
    led.record(source="sleep", decision=SPEAK, now=T0)
    led.record(source="sleep", decision=FAILED, reason="推送挂了", now=T0)
    s = led.summary(T0)
    assert s["considered"] == 1
    assert s["failed"] == 1


def test_按来源分组():
    led = CareLedger()
    led.record(source="sleep", decision=SPEAK, now=T0)
    led.record(source="random", decision=SPEAK, now=T0)
    led.record(source="random", decision=SKIP, now=T0)
    led.record(source="wake", decision=SPEAK, now=T0)

    s = led.summary(T0)
    assert s["by_source"]["random"][SPEAK] == 1
    assert s["by_source"]["random"][SKIP] == 1
    assert s["by_source"]["sleep"][SPEAK] == 1
    assert s["by_source"]["wake"][SPEAK] == 1


def test_拦下的理由要能分类看():
    """哪条栏杆最常挡着他，是调参的唯一依据。"""
    led = CareLedger()
    led.record(source="random", decision=BLOCK, reason="今天额度用完了", now=T0)
    led.record(source="random", decision=BLOCK, reason="今天额度用完了", now=T0)
    led.record(source="todo", decision=BLOCK, reason="刚追过", now=T0)
    assert led.summary(T0)["blocked_why"] == {"今天额度用完了": 2, "刚追过": 1}


def test_只存id不存原文():
    """原文属于 conversations（糖糖 2026-08-18）。"""
    led = CareLedger()
    led.record(source="wake", decision=SPEAK, thread_id="care-1", message_id="msg-9", now=T0)
    ev = led.events[-1]
    assert ev["thread_id"] == "care-1"
    assert ev["message_id"] == "msg-9"
    assert not any("我想你了" in str(v) for v in ev.values())


def test_读不许改状态():
    """2026-08-19 的真 bug：`summary()` 里调了 `_roll()`，
    于是跨过零点之后**任何一次读**都会把昨天的账本清空 —— 读把数据删了。"""
    led = CareLedger()
    led.record(source="random", decision=SPEAK, now=T0)       # 中国 8-18 20:00
    assert led.summary()["spoke"] == 1                        # 用真实「现在」读
    assert led.summary()["spoke"] == 1, "读了一次就没了"
    assert len(led.events) == 1


def test_按中国时区跨天():
    """⚠️ 别用 UTC 日期：中国时间 00:30 会被算成昨天。"""
    late = datetime(2026, 8, 18, 16, 30, tzinfo=timezone.utc)   # 中国 8-19 00:30
    led = CareLedger()
    led.record(source="random", decision=SPEAK, now=T0)          # 中国 8-18 20:00
    assert led.summary(T0)["date"] == "2026-08-18"

    led.record(source="random", decision=SPEAK, now=late)
    s = led.summary(late)
    assert s["date"] == "2026-08-19", "跨天没换账本"
    assert s["spoke"] == 1, "昨天的数字漏进今天了"


def test_事件数封顶():
    led = CareLedger()
    for _ in range(400):
        led.record(source="random", decision=SKIP, now=T0)
    assert len(led.events) <= 300


def test_存读一轮不丢():
    led = CareLedger()
    led.record(source="sleep", decision=SPEAK, thread_id="t1", now=T0)
    back = CareLedger.from_dict(led.to_dict())
    assert back.summary(T0)["spoke"] == 1
    assert back.date == led.date


def test_坏数据不炸():
    assert CareLedger.from_dict(None).events == []
    assert CareLedger.from_dict({"events": "不是数组"}).events == []


# ---------------------------------------------------------------- 和决策层接上


def _orch(**kw):
    led = CareLedger()
    deliver = kw.pop("deliver", lambda s, t, n: "msg-1")
    o = CareOrchestrator(ThreadBook(), deliver, ledger=led, **kw)
    return o, led


def test_说了会记成speak并带上消息id():
    o, led = _orch()
    o.submit(CareSignal(source="random", subject="想起你了"))
    o.run(T0)
    ev = led.events[-1]
    assert ev["decision"] == SPEAK
    assert ev["message_id"] == "msg-1"
    assert ev["thread_id"]


def test_他自己不说记成skip不是block():
    """这条是整个账本的重点 —— 分不清就没法判断该调哪一边。"""
    o, led = _orch(deliver=lambda s, t, n: False)
    o.submit(CareSignal(source="random", subject="想起你了"))
    o.run(T0)
    assert led.events[-1]["decision"] == SKIP


def test_被闸拦下记成block并带理由():
    o, led = _orch(gate_check=lambda now: "今天额度用完了", quota_cooldown_min=0,
                   policies={"sleep": SourcePolicy(takes_gate=True)})
    o.submit(CareSignal(source="sleep", subject="她没睡好"))
    o.run(T0)
    ev = led.events[-1]
    assert ev["decision"] == BLOCK
    assert ev["reason"] == "今天额度用完了"


def test_额度拦下也是block():
    o, led = _orch()
    o.submit(CareSignal(source="random", subject="第一次"))
    o.run(T0)
    o.submit(CareSignal(source="random", subject="第二次"))
    o.run(T0 + timedelta(minutes=10))
    s = led.summary(T0)
    assert s["spoke"] == 1
    assert s["blocked"] == 1
    assert s["considered"] == 2


def test_押后的念头先不记():
    """做梦那条在等天亮，还没有下场 —— 记了会一晚上刷几十笔。"""
    o, led = _orch()
    o.submit(CareSignal(source="dream", subject="梦到那个厨房",
                        not_before=T0 + timedelta(hours=6)))
    o.run(T0)
    assert led.events == []

    o.run(T0 + timedelta(hours=6))
    assert led.events[-1]["decision"] == SPEAK


def test_发送失败记成failed():
    def boom(s, t, n):
        raise RuntimeError("推送挂了")

    o, led = _orch(deliver=boom)
    o.submit(CareSignal(source="sleep", subject="她没睡好"))
    o.run(T0)
    assert led.events[-1]["decision"] == FAILED
    assert led.summary(T0)["considered"] == 0, "故障不算一次惦记"


def test_汇总挂在snapshot上():
    o, _ = _orch()
    o.submit(CareSignal(source="random", subject="想起你了"))
    o.run(T0)
    snap = o.snapshot()
    assert snap["ledger"]["spoke"] == 1
    assert snap["ledger"]["considered"] == 1
