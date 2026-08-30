"""M3 测试：Intent + Scheduler + dry-run 全链路。

这一批守的是**「会不会烦人」**：时间窗对不对、冷却拦不拦得住、
dry-run 会不会偷偷把消息发出去。

时间一律按 Asia/Shanghai 构造 —— VPS 在东京，用服务器时间判断
「现在是不是晚上」会差一小时，足够让睡前关心跑到她睡着之后。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.intent import (  # noqa: E402
    GENERATE_THRESHOLD,
    PENDING,
    Intent,
    IntentEngine,
)
from attention.registry import Attention  # noqa: E402
from attention.relationship import RelationshipState  # noqa: E402
from attention.scheduler import Scheduler, context_fit  # noqa: E402
from attention.service import AttentionService  # noqa: E402
from attention.store import AttentionStore  # noqa: E402

#: 和 scheduler.LOCAL_TZ 一致：固定 UTC+8，不走 zoneinfo（Windows 上没有 tzdata）
CN = timezone(timedelta(hours=8), "CST")
SUBJECT = "糖糖的睡眠"


def cn(day: int, hour: int, minute: int = 0) -> datetime:
    """构造一个中国时间。"""
    return datetime(2026, 8, day, hour, minute, tzinfo=CN)


EVENING = cn(8, 22)      # 睡前
MORNING = cn(8, 10)      # 刚起
AFTERNOON = cn(8, 15)    # 白天
DEEP_NIGHT = cn(8, 4)    # 她睡着了


class FakeProvider:
    def __init__(self, **kw: Any) -> None:
        self.state: dict[str, Any] = {"has_data": True, **kw}

    def get_state(self, turn: Any = None, force_refresh: bool = False) -> dict[str, Any]:
        return self.state


def _sleep_provider(hours: float, date: str = "2026-08-08") -> FakeProvider:
    return FakeProvider(sleep_date=date, sleep_min=hours * 60)


def _attention(strength: float, now: datetime) -> Attention:
    return Attention(subject=SUBJECT, strength=strength, since=now, last_updated=now)


@pytest.fixture
def store(tmp_path):
    s = AttentionStore(tmp_path / "attn.db")
    yield s
    s.close()


# ---------------------------------------------------------------- Intent


def test_weak_attention_does_not_become_intent():
    """只是「记着」，还不到「要说」。"""
    eng = IntentEngine()
    assert eng.generate(_attention(GENERATE_THRESHOLD - 0.01, EVENING), EVENING) is None
    assert len(eng) == 0


def test_strong_attention_becomes_intent():
    eng = IntentEngine()
    i = eng.generate(_attention(0.85, EVENING), EVENING)
    assert i is not None and i.status == PENDING and i.subject == SUBJECT


def test_same_subject_never_has_two_pending():
    """不挡住的话，Concern 每被加强一次就多一条待办，
    Scheduler 会以为有好几件事要说。"""
    eng = IntentEngine()
    first = eng.generate(_attention(0.75, EVENING), EVENING)
    second = eng.generate(_attention(0.90, EVENING), EVENING)
    assert first is second
    assert len(eng.list_pending(EVENING)) == 1


def test_existing_intent_gets_refreshed_when_worse():
    """事情变严重了，待办也该跟着变重要。"""
    eng = IntentEngine()
    eng.generate(_attention(0.75, EVENING), EVENING)
    eng.generate(_attention(0.95, EVENING), EVENING)
    assert eng.pending_for(SUBJECT).attention_strength == pytest.approx(0.95)


def test_intent_expires():
    """隔两天再说「你前天没睡好」很怪。"""
    eng = IntentEngine()
    eng.generate(_attention(0.85, EVENING), EVENING)
    assert eng.expire_stale(EVENING + timedelta(hours=25))
    assert eng.list_pending(EVENING + timedelta(hours=25)) == []


def test_drop_for_kills_pending():
    """关心淡掉了，待办也该消失。"""
    eng = IntentEngine()
    eng.generate(_attention(0.85, EVENING), EVENING)
    assert eng.drop_for(SUBJECT) == 1
    assert eng.list_pending(EVENING) == []


def test_intent_roundtrip():
    eng = IntentEngine()
    eng.generate(_attention(0.85, EVENING), EVENING)
    back = IntentEngine.from_list(eng.to_list())
    assert back.pending_for(SUBJECT) is not None


# ---------------------------------------------------------------- context_fit


@pytest.mark.parametrize("when,expected", [
    (EVENING, 1.0),        # 22:00 睡前
    (cn(8, 1), 0.2),       # 01:00 过了零点，说昨晚已经晚了
    (DEEP_NIGHT, 0.0),     # 04:00 睡着了
    (MORNING, 0.7),        # 10:00 刚起
    (AFTERNOON, 0.3),      # 15:00 白天
])
def test_context_fit_windows(when, expected):
    fit, _ = context_fit(SUBJECT, when)
    assert fit == expected


def test_context_fit_uses_her_timezone_not_utc():
    """中国时间 22:00 是 UTC 14:00。按 UTC 判就成了下午，
    睡前那个窗口会整个错过。"""
    assert context_fit(SUBJECT, EVENING)[0] == 1.0
    assert EVENING.utctimetuple().tm_hour == 14


def test_never_speaks_while_she_sleeps():
    """凌晨四点，多严重都不许开口。"""
    sched = Scheduler()
    intent = Intent(subject=SUBJECT, title="关心睡眠", reason="", attention_strength=1.0)
    assert not sched.tick([intent], DEEP_NIGHT).will_speak


# ---------------------------------------------------------------- Scheduler


def _intent(strength: float = 0.9, subject: str = SUBJECT) -> Intent:
    return Intent(subject=subject, title=f"关心{subject}", reason="测试",
                  attention_strength=strength, created_at=EVENING,
                  expires_at=EVENING + timedelta(hours=24))


def test_no_intents_means_silence():
    assert not Scheduler().tick([], EVENING).will_speak


def test_speaks_when_score_is_high_enough():
    d = Scheduler().tick([_intent(0.9)], EVENING)
    assert d.will_speak
    assert d.effective_score == pytest.approx(0.9)


def test_low_score_waits():
    """白天聊睡眠：0.715 × 0.3 = 0.21，不够。
    没被选中不等于丢弃，下次 tick 会重新算。"""
    d = Scheduler().tick([_intent(0.715)], AFTERNOON)
    assert not d.will_speak
    assert "再等等" in d.reason


def test_global_cooldown_blocks():
    sched = Scheduler()
    i = _intent()
    sched.note_spoke(i, EVENING)
    assert not sched.tick([i], EVENING + timedelta(minutes=30)).will_speak


def test_global_cooldown_expires():
    """全局冷却（3 小时）过了就能再开口。

    这个测试踩了两次，都记下来免得再犯：

    1. 必须用**不同的 subject** —— 同一话题还压着 20 小时的话题冷却
    2. 起点不能用 22:00 —— 加 4 小时就是凌晨 2 点，撞进「她在睡觉」
       那个窗口，fit=0.0，永远开不了口

    所以从 18:00 起跳，落在 22:00 的睡前窗口里。
    """
    sched = Scheduler()
    sched.note_spoke(_intent(subject="别的事"), cn(8, 18))
    assert sched.tick([_intent(subject=SUBJECT)], cn(8, 22)).will_speak


def test_subject_cooldown_is_much_longer():
    """「你昨晚没睡好」一天说一次够了。
    全局冷却过了，话题冷却还没过。"""
    sched = Scheduler()
    sched.note_spoke(_intent(), EVENING)
    d = sched.tick([_intent()], EVENING + timedelta(hours=5))
    assert not d.will_speak
    assert "话题冷却" in d.reason


def test_picks_only_one_by_effective_score():
    """一次只开一次口，选 effective_score 最高的那条。

    注意：现在 `context_fit` **还不分话题**（只有睡眠一个 subject，
    分表就是纯间接层），所以同一时刻两条待办比的就是 priority。
    第二个话题接进来时，这个测试要改成「同一时刻不同话题 fit 不同」。
    """
    sched = Scheduler()
    d = sched.tick([_intent(0.8, subject="别的事"), _intent(0.95)], EVENING)
    assert d.will_speak
    assert d.intent.subject == SUBJECT
    assert len(d.considered) == 2


def test_scheduler_state_survives_restart():
    """重启后不该觉得自己很久没说话了，可以马上开口。"""
    s1 = Scheduler()
    s1.note_spoke(_intent(), EVENING)

    s2 = Scheduler()
    s2.load_state(s1.dump_state())
    assert not s2.tick([_intent()], EVENING + timedelta(minutes=30)).will_speak


# ---------------------------------------------------------------- 全链路


def test_dry_run_does_not_speak(store):
    """M3 的核心保证：完整地想一遍，但一个字都不发出去。"""
    svc = AttentionService(store, _sleep_provider(4.5), RelationshipState())
    assert svc.dry_run

    d = svc.tick(EVENING)
    assert d.will_speak          # 它「想」说
    assert svc.speaker is None   # 但没有任何发送渠道


def test_live_mode_calls_speaker(store):
    said: list[str] = []
    svc = AttentionService(
        store, _sleep_provider(4.5), RelationshipState(),
        speaker=lambda intent, decision: said.append(intent.title),
    )
    assert not svc.dry_run
    svc.tick(EVENING)
    assert said == ["关心糖糖的睡眠"]


def test_dry_run_still_records_cooldown(store):
    """dry-run 不记冷却的话，观察到的开口频率会比真实情况高得多 ——
    那就白观察了。"""
    svc = AttentionService(store, _sleep_provider(4.5), RelationshipState())
    assert svc.tick(EVENING).will_speak
    assert not svc.tick(EVENING + timedelta(minutes=30)).will_speak


def test_full_chain_bad_sleep_to_intent(store):
    """睡眠数据 → Concern → Intent → 决定开口，一条龙。"""
    svc = AttentionService(store, _sleep_provider(4.5), RelationshipState())
    d = svc.tick(EVENING)

    assert d.will_speak
    assert svc.engine.registry.get(SUBJECT) is not None
    assert len(svc.intents.list_pending(EVENING)) == 0   # 已经 triggered 了
    assert "关心糖糖的睡眠" in d.render()


def test_good_sleep_produces_nothing(store):
    """睡得好就该完全安静。"""
    svc = AttentionService(store, _sleep_provider(7.5), RelationshipState())
    d = svc.tick(EVENING)
    assert not d.will_speak
    assert len(svc.engine.registry) == 0


def test_speaker_failure_does_not_break_tick(store):
    """发送失败不该让整个 tick 挂掉，但要记进 action_history，
    否则会变成「以为说了其实没说」。"""

    def boom(intent, decision):
        raise RuntimeError("推送服务挂了")

    svc = AttentionService(store, _sleep_provider(4.5), RelationshipState(), speaker=boom)
    svc.tick(EVENING)          # 不抛
    triggered = [i for i in svc.intents.all() if i.action_history]
    assert triggered[0].action_history[-1]["note"] == "发送失败"


def test_service_state_survives_restart(tmp_path):
    """整个链路的状态都要落盘：关心、待办、冷却。"""
    path = tmp_path / "attn.db"

    s1 = AttentionStore(path)
    svc1 = AttentionService(s1, _sleep_provider(4.5), RelationshipState())
    assert svc1.tick(EVENING).will_speak
    s1.close()

    s2 = AttentionStore(path)                  # 模拟 nox-core 重启
    svc2 = AttentionService(s2, _sleep_provider(4.5), RelationshipState())
    assert svc2.engine.registry.get(SUBJECT) is not None
    # 冷却也记着 —— 重启不该变成一次重新开口的机会
    assert not svc2.tick(EVENING + timedelta(minutes=30)).will_speak
    s2.close()


def test_snapshot_shows_state(store):
    svc = AttentionService(store, _sleep_provider(4.5), RelationshipState())
    svc.tick(EVENING)
    snap = svc.snapshot()
    assert snap["dry_run"] is True
    assert snap["attentions"][0]["subject"] == SUBJECT


def test_憋着的话要带上话题(store):
    """🔴 `pending_intents` 少了 `subject`，界面会重复显示同一件事。

    `title` 是一句话（「关心糖糖的睡眠」），`attentions` 里是话题
    （「糖糖的睡眠」）。没有 subject 就对不上，于是同一件事在
    「Attention 聚焦」里出现两次 —— 一条「他有话想说」、一条「他在留意着」。
    糖糖 2026-08-30 截图问的就是这个。

    ⚠️ 这不是显示层能补的：光有那句话，前端只能去猜它在说哪个话题。
    """
    svc = AttentionService(store, _sleep_provider(4.5), RelationshipState())
    svc.tick(EVENING)                      # 让 attentions 里有「糖糖的睡眠」
    #: 直接放一条进去，不走生成路径 —— 这里验的是**序列化**，
    #  而 tick 是 dry_run，说完就把它清了
    i = _intent()
    assert i.title == f"关心{SUBJECT}", "这就是界面上显示成「关心your的睡眠」的那句"
    #: ⚠️ `list_pending()` 按**真实当下**算过期，而 EVENING 是个固定的过去时刻 ——
    #  不往后推的话它一进来就是过期的，snapshot 里永远是空的
    i.expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    svc.intents._intents[i.id] = i
    snap = svc.snapshot()

    pend = snap["pending_intents"]
    assert pend, "这一 tick 本该憋出一条话来"
    assert pend[0]["subject"] == SUBJECT
    #: 且它必须跟 attentions 里的那个**是同一个字符串** —— 能对上才谈得上去重
    assert pend[0]["subject"] in {a["subject"] for a in snap["attentions"]}
