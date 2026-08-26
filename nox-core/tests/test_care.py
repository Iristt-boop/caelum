"""Care 层的测试。

重点钉三样东西：

1. **任务型链不因为她回话而关闭** —— 2026-08-18 那个 bug 的回归测试。
   他留了「到点把主卧空调关上」的纸条，59 秒后她说了句话，纸条被撤了。
2. **额度按链算，不按消息算** —— 糖糖定的：连续追踪不算多次主动关心。
3. **每个念头都要有下场** —— 静默丢弃是这套东西最难查的病。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from attention.care import (
    COMPANY,
    FOLLOWUP,
    TASK,
    CareOrchestrator,
    CareSignal,
    SourcePolicy,
    ThreadBook,
)

T0 = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


def _orch(**kw):
    """造一个默认会开口的 orchestrator，记下说过什么。"""
    said: list[str] = []

    def deliver(signal, thread, now):
        said.append(f"{signal.source}:{signal.subject}")
        return True

    o = CareOrchestrator(kw.pop("threads", ThreadBook()), kw.pop("deliver", deliver), **kw)
    return o, said


# ---------------------------------------------------------------- 关闭条件


def test_任务链不因为她回话而关闭():
    """2026-08-18 回归：空调那张纸条被她的一句话撤掉了。"""
    book = ThreadBook()
    task = book.open(TASK, "关主卧空调", session_id="s1", now=T0)
    followup = book.open(FOLLOWUP, "她去吃饭了", session_id="s1", now=T0)

    closed = book.close_on_reply("s1")

    assert closed == 1, "只该关掉追问型那一条"
    assert task.alive, "任务型必须活着 —— 空调该不该关跟她回不回话无关"
    assert not followup.alive
    assert "kept_on_reply" in [h["action"] for h in task.history]


def test_陪伴链她回话就关():
    book = ThreadBook()
    t = book.open(COMPANY, "随便想起她", session_id="s1", now=T0)
    book.close_on_reply("s1")
    assert not t.alive


def test_别的会话不受影响():
    book = ThreadBook()
    mine = book.open(FOLLOWUP, "这个会话", session_id="s1", now=T0)
    other = book.open(FOLLOWUP, "别的会话", session_id="s2", now=T0)
    book.close_on_reply("s1")
    assert not mine.alive
    assert other.alive


# ---------------------------------------------------------------- 额度


def test_一小时内只能开一条新链():
    o, said = _orch()
    a = o._decide(CareSignal(source="random", subject="想她了"), T0)
    b = o._decide(CareSignal(source="random", subject="又想她了"), T0 + timedelta(minutes=20))

    assert a.action == "spoke"
    assert b.action == "dropped"
    assert "已经开过一条链" in b.reason
    assert said == ["random:想她了"]


def test_过了冷却可以再开():
    o, said = _orch()
    o._decide(CareSignal(source="random", subject="想她了"), T0)
    later = o._decide(CareSignal(source="random", subject="又想她了"),
                      T0 + timedelta(minutes=61))
    assert later.action == "spoke"
    assert len(said) == 2


def test_链内部连续追踪不吃额度():
    """出门那种 5 / 10 / 30 分钟连问，是一条链，不是三次主动关心。"""
    o, said = _orch()
    first = o._decide(
        CareSignal(source="location", subject="出门了", thread_kind=FOLLOWUP), T0
    )
    tid = first.thread.id

    for minute in (5, 10, 30):
        r = o._decide(
            CareSignal(source="location", subject="出门了", thread_id=tid),
            T0 + timedelta(minutes=minute),
        )
        assert r.action == "spoke", f"T+{minute} 应该能追，结果 {r.reason}"

    assert len(said) == 4
    assert first.thread.steps == 4


def test_唤醒链不吃额度():
    """对话延续，2026-08-14 定的：它不占「她沉默时主动开口」的额度。"""
    o, said = _orch(policies={"wake": SourcePolicy(takes_quota=False)})
    o._decide(CareSignal(source="random", subject="想她了"), T0)
    r = o._decide(CareSignal(source="wake", subject="吃完饭了吗"), T0 + timedelta(minutes=5))
    assert r.action == "spoke"
    assert len(said) == 2


def test_链到步数上限就关了():
    o, _ = _orch(policies={"location": SourcePolicy(takes_quota=False, max_steps=2)})
    first = o._decide(CareSignal(source="location", subject="出门了"), T0)
    tid = first.thread.id
    o._decide(CareSignal(source="location", subject="出门了", thread_id=tid), T0)
    dead = o._decide(CareSignal(source="location", subject="出门了", thread_id=tid), T0)
    assert dead.action == "dropped"
    assert "链已经关了" in dead.reason


# ---------------------------------------------------------------- 时效（做梦用）


def test_梦要等她醒了再说():
    o, said = _orch()
    dawn = T0 + timedelta(hours=6)
    s = CareSignal(source="dream", subject="梦到那个厨房", not_before=dawn)

    o.submit(s)
    assert [r.action for r in o.run(T0)] == ["held"], "凌晨三点不该把她叫醒"
    assert said == []

    # 押后的念头会自己回到队列里，不用重新 submit
    assert [r.action for r in o.run(dawn)] == ["spoke"]
    assert said == ["dream:梦到那个厨房"]


# ---------------------------------------------------------------- 下场


def test_没线头就不说而且留痕():
    """抓不到线头宁可不说 —— 但不能静默，得能查。"""
    o, _ = _orch(deliver=lambda signal, thread, now: False)
    r = o._decide(CareSignal(source="random", subject="想她了"), T0)
    assert r.action == "dropped"
    assert r.reason == "没什么具体的可说"
    assert r.thread.steps == 0, "没说出去就不该计步"
    assert "nothing_to_say" in [h["action"] for h in r.thread.history]


def test_发送失败要留痕不能当成说过了():
    def boom(signal, thread, now):
        raise RuntimeError("推送挂了")

    o, _ = _orch(deliver=boom)
    r = o._decide(CareSignal(source="random", subject="想她了"), T0)
    assert r.action == "failed"
    assert r.thread.steps == 0
    assert "deliver_failed" in [h["action"] for h in r.thread.history]


def test_一个源炸了不带塌别人():
    class Boom:
        name = "boom"

        def poll(self, now):
            raise RuntimeError("这个源坏了")

    class Fine:
        name = "fine"

        def poll(self, now):
            return [CareSignal(source="fine", subject="我还活着")]

    o, said = _orch()
    o.collect([Boom(), Fine()], T0)
    assert [r.action for r in o.run(T0)] == ["spoke"]
    assert said == ["fine:我还活着"]


def test_统一闸拦下就不开链():
    """闸拦下的时候不该留下一条空链 —— 否则额度会被没说出去的话吃掉。"""
    o, said = _orch(gate_check=lambda now: "今天额度用完了", quota_cooldown_min=0)
    r = o._decide(CareSignal(source="sleep", subject="她没睡好"), T0)
    assert r.action == "dropped"
    assert r.reason == "今天额度用完了"
    assert r.thread is None
    assert len(o.threads) == 0
    assert said == []


def test_唤醒链不吃统一闸():
    o, said = _orch(
        gate_check=lambda now: "今天额度用完了",
        quota_cooldown_min=0,
        policies={"wake": SourcePolicy(takes_quota=False, takes_gate=False)},
    )
    assert o._decide(CareSignal(source="wake", subject="吃完饭了吗"), T0).action == "spoke"
    assert said == ["wake:吃完饭了吗"]


def test_续链不再过闸():
    """闸是「要不要开始关心这件事」，不是「这句话准不准发」。"""
    gate = {"blocked": ""}
    o, said = _orch(gate_check=lambda now: gate["blocked"], quota_cooldown_min=0)
    first = o._decide(CareSignal(source="location", subject="出门了"), T0)

    gate["blocked"] = "今天额度用完了"
    again = o._decide(
        CareSignal(source="location", subject="出门了", thread_id=first.thread.id),
        T0 + timedelta(minutes=5),
    )
    assert again.action == "spoke"
    assert len(said) == 2


def test_急的先说():
    o, said = _orch()
    o.submit(CareSignal(source="random", subject="不急", urgency=0.2))
    o.submit(CareSignal(source="sleep", subject="很急", urgency=0.9))
    o.run(T0)
    # 额度只够一条，该留给急的那个
    assert said == ["sleep:很急"]


# ---------------------------------------------------------------- 序列化


def test_存读一轮不丢东西():
    book = ThreadBook()
    t = book.open(TASK, "关空调", session_id="s1", now=T0)
    t.step(T0)
    t.note("spoke", subject="关空调")

    back = ThreadBook.from_list(book.to_list())
    got = back.get(t.id)
    assert got is not None
    assert got.kind == TASK
    assert got.steps == 1
    assert got.closes_on_reply is False


def test_坏掉的一条不该让整本账加载失败():
    book = ThreadBook.from_list([{"id": "x"}, None])  # type: ignore[list-item]
    assert len(book) == 0

