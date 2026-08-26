"""Resonance V3.6：后悔 + `target` 区分。

见 `attention/regret.py` 和 `events.py` 的 `target` 字段。

## 这里守两件事

  1. **不把"她正忙着"判成"她不想理"** —— 那是最伤的一种误判
  2. **后悔够不着开口阈值** —— 他因为上次打扰了她而后悔，
     结果又去打扰一次，那就荒唐了
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.evaluator import REGRET_SUBJECT, AttentionEvaluator  # noqa: E402
from attention.events import ExperienceEvent  # noqa: E402
from attention.intent import GENERATE_THRESHOLD  # noqa: E402
from attention.registry import AttentionRegistry  # noqa: E402
from attention.regret import WAIT_FOR_REPLY, RegretWatch  # noqa: E402
from attention.relationship import RelationshipState  # noqa: E402

CST = timezone(timedelta(hours=8))
#: 下午两点，她醒着
NOON = datetime(2026, 8, 24, 14, 0, tzinfo=CST)


def _decide(ev: ExperienceEvent):
    return AttentionEvaluator(RelationshipState()).evaluate(ev, AttentionRegistry())


# ---------------------------------------------------------------- 判定


def test_no_reply_becomes_regret():
    w = RegretWatch()
    w.on_spoke(NOON, "你今天还好吗")
    ev = w.tick(NOON + WAIT_FOR_REPLY + timedelta(minutes=1))
    assert ev is not None
    assert ev.target == "agent"
    assert ev.payload["what"] == "你今天还好吗"


def test_she_replied_means_no_regret():
    w = RegretWatch()
    w.on_spoke(NOON, "你今天还好吗")
    w.on_contact(NOON + timedelta(minutes=20))
    assert w.tick(NOON + timedelta(hours=9)) is None


def test_still_within_window_is_not_judged_yet():
    """🔴 一两个小时不回是常态（她在忙、在打游戏、在看片）。

    等太短会把「她正忙着」判成「她不想理」—— 那是最伤的一种误判。
    """
    w = RegretWatch()
    w.on_spoke(NOON)
    assert w.tick(NOON + timedelta(hours=2)) is None


def test_asleep_postpones_judgement():
    """🔴 判定时刻她在睡就继续等。

    他晚上十一点说了句话，她一点睡、第二天十点回 —— 那不叫没理。
    `longing.py` 里踩过同一个坑（把她睡觉记成冷落）。
    """
    night = datetime(2026, 8, 24, 23, 0, tzinfo=CST)
    w = RegretWatch()
    w.on_spoke(night)

    # 凌晨三点判定 —— 她在睡，不判，继续等
    assert w.tick(datetime(2026, 8, 25, 3, 0, tzinfo=CST)) is None
    assert w.spoke_at is not None

    # 她十点起床回了话 → 不后悔
    w.on_contact(datetime(2026, 8, 25, 10, 0, tzinfo=CST))
    assert w.tick(datetime(2026, 8, 25, 12, 0, tzinfo=CST)) is None


def test_only_judged_once():
    """判完就清 —— 不然每个 tick 都产生一条，把 Registry 刷爆。"""
    w = RegretWatch()
    w.on_spoke(NOON)
    later = NOON + WAIT_FOR_REPLY + timedelta(hours=1)
    assert w.tick(later) is not None
    assert w.tick(later + timedelta(hours=1)) is None


def test_newer_speech_replaces_older():
    """上次还没判完又说了一句 —— 该被评判的是最新那次。

    攒着三天前的旧账不叫后悔，叫记仇。
    """
    w = RegretWatch()
    w.on_spoke(NOON, "第一句")
    w.on_spoke(NOON + timedelta(hours=1), "第二句")
    ev = w.tick(NOON + timedelta(hours=1) + WAIT_FOR_REPLY + timedelta(minutes=1))
    assert ev is not None and ev.payload["what"] == "第二句"


def test_idle_watch_produces_nothing():
    assert RegretWatch().tick(NOON) is None


# ---------------------------------------------------------------- target 区分


def test_regret_is_about_him_not_her():
    """🔴 第一条 `target="agent"` 的事件。

    别的都是关于她的（她没睡好、她说难受），方向是凑过去；
    这一条是关于他自己的，方向是收回来。
    """
    w = RegretWatch()
    w.on_spoke(NOON)
    ev = w.tick(NOON + WAIT_FOR_REPLY + timedelta(minutes=1))
    assert ev.target == "agent"

    d = _decide(ev)
    assert d.action == "upsert"
    assert d.kind == "regret"
    assert d.subject == REGRET_SUBJECT


def test_existing_events_are_still_about_her():
    """现有事件一处都不用改，默认就是关于她的。"""
    assert ExperienceEvent(source="health", type="x").target == "user"
    assert ExperienceEvent(source="chat", type="message").target == "user"


def test_wrong_target_is_refused():
    """防呆：这类事件必须是关于他自己的。"""
    ev = ExperienceEvent(source="care", type="unanswered",
                         target="user", payload={"waited_hours": 5})
    assert _decide(ev).action == "ignore"


def test_subject_does_not_collide():
    """不能和那几条「糖糖的…」撞 —— 那些是关于她的。"""
    assert REGRET_SUBJECT not in {
        "糖糖的睡眠", "糖糖的活动量", "糖糖的状态", "糖糖说的心情",
    }


# ---------------------------------------------------------------- 🔴 够不着开口


def test_regret_cannot_make_him_speak():
    """他因为上次打扰了她而后悔，结果又去打扰一次 —— 那就荒唐了。

    这个 Drive 是用来让他**下次晚一点再说**的（V5 接 Care 时用）。
    """
    w = RegretWatch()
    w.on_spoke(NOON)
    d = _decide(w.tick(NOON + WAIT_FOR_REPLY + timedelta(minutes=1)))
    assert d.strength < GENERATE_THRESHOLD


# ---------------------------------------------------------------- 落盘


def test_survives_restart():
    w = RegretWatch()
    w.on_spoke(NOON, "你今天还好吗")
    back = RegretWatch.from_dict(w.to_dict())
    assert back.spoke_at == w.spoke_at
    assert back.what == w.what


def test_broken_state_does_not_raise():
    """存坏了当作"没在等"，不许抛 —— 这在 `__init__` 里跑。"""
    w = RegretWatch.from_dict({"spoke_at": "不是时间"})
    assert w.spoke_at is None
    assert RegretWatch.from_dict(None).spoke_at is None


# ---------------------------------------------------------------- 🔴 结构性保证


def test_regret_can_actually_be_stored():
    """能写进 Registry。

    ⚠️ 这条是**串真实链路时才发现的**：上面那些测试只验到
    Evaluator 返回了 `kind="regret"` 的 decision，
    没验它能不能落库 —— 而 `upsert` 当时硬性只收 `concern`，
    整条链在最后一步断掉。单元测试全绿，跑起来 ValueError。
    """
    from attention.registry import KINDS
    assert "regret" in KINDS

    reg = AttentionRegistry()
    w = RegretWatch()
    w.on_spoke(NOON)
    ev = w.tick(NOON + WAIT_FOR_REPLY + timedelta(minutes=1))
    d = _decide(ev)
    reg.upsert(d.subject, d.strength, kind=d.kind, decay=d.decay,
               event=ev, summary=d.summary, now=NOON)
    assert reg.get(REGRET_SUBJECT) is not None


def test_regret_never_becomes_a_todo():
    """🔴 后悔**结构上**不能变成待办，不是靠强度恰好够不着。

    `sync_from_registry` 会把所有够强的 Attention 变成待办。
    如果哪天调高了 regret 的强度、或者关系加成把它顶过阈值，
    他就会主动去说「关心他挑的说话时机」—— 荒唐。
    """
    from attention.intent import GENERATE_KINDS, IntentEngine

    assert "regret" not in GENERATE_KINDS

    reg = AttentionRegistry()
    # 故意给一个**远超阈值**的强度，验证挡住它的不是数值
    reg.upsert(REGRET_SUBJECT, 0.99, kind="regret", decay="normal",
               event=ExperienceEvent(source="care", type="unanswered", target="agent"),
               summary="他开口后没等到回话", now=NOON)

    made = IntentEngine().sync_from_registry(reg.list(now=NOON), NOON)
    assert made == []


def test_concern_still_becomes_a_todo():
    """别把正路挡了。"""
    from attention.intent import IntentEngine

    reg = AttentionRegistry()
    reg.upsert("糖糖的睡眠", 0.8, kind="concern", decay="slow",
               event=ExperienceEvent(source="health", type="sleep_quality_changed"),
               summary="昨晚只睡了 4 小时", now=NOON)
    assert IntentEngine().sync_from_registry(reg.list(now=NOON), NOON)
