"""M4 测试：主动关心真的发出去。

这一批守的是**「发出去的那一刻会不会出岔子」**：

1. 落不落进会话（不落的话她回一句他就懵了）
2. 推送带不带 session_id（不带的话锁屏有话、点进去空的）
3. 模型没吐字的时候会不会硬发
4. 失败会不会被吞成「已发出」——那是最坏的：冷却照记，她什么都没收到
5. 唠叨过几次他自己知不知道

全部不打网络。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.intent import PENDING, TRIGGERED, Intent  # noqa: E402
from attention.relationship import RelationshipState  # noqa: E402
from attention.scheduler import SchedulerDecision  # noqa: E402
from attention.service import AttentionService  # noqa: E402
from attention.speaker import (  # noqa: E402
    FALLBACK_SESSION,
    build_prompt,
    build_speaker,
)
from attention.store import AttentionStore  # noqa: E402

CN = timezone(timedelta(hours=8), "CST")
SUBJECT = "糖糖的睡眠"
EVENING = datetime(2026, 8, 8, 22, tzinfo=CN)


# ---------------------------------------------------------------- 替身


class FakeProvider:
    def __init__(self, **kw: Any) -> None:
        self.state: dict[str, Any] = {"has_data": True, **kw}

    def get_state(self, turn: Any = None, force_refresh: bool = False) -> dict[str, Any]:
        return self.state


def _sleep_provider(hours: float, date: str = "2026-08-08") -> FakeProvider:
    return FakeProvider(sleep_date=date, sleep_min=hours * 60)


class FakeResult:
    def __init__(self, text: str, ok: bool = True) -> None:
        self.text = text
        self.ok = ok
        self.outcome = "ok" if ok else "empty"
        self.detail = ""


class FakeReply:
    def __init__(self, text: str, ok: bool = True) -> None:
        self.result = FakeResult(text, ok)
        self.messages = [{"role": "assistant", "text": text}]


class FakeBridge:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, payload: dict):
        self.calls.append((path, payload))

        class R:
            ok = self.ok
            data = {"subs": 2}
            error = "" if self.ok else "bridge 挂了"
        return R()


class FakeCore:
    def __init__(self, text: str = "昨晚才睡了四个半小时，今天早点躺下好不好",
                 ok: bool = True, bridge: FakeBridge | None = None) -> None:
        self.reply = FakeReply(text, ok)
        self.bridge = bridge if bridge is not None else FakeBridge()
        self.prompts: list[str] = []

    def chat(self, text: str, history: list, **kw):
        self.prompts.append(text)
        return self.reply


class FakeSessions:
    def __init__(self) -> None:
        self.saved: dict[str, list] = {}

    def get(self, sid: str) -> list:
        return self.saved.get(sid, [])

    def put(self, sid: str, messages: list) -> None:
        self.saved[sid] = messages


class FakeSessionInfo:
    def __init__(self, sid: str) -> None:
        self.id = sid


class FakeStore:
    """Core 的会话库。只用到 `recent()`。"""

    def __init__(self, sid: str | None = "a" * 32) -> None:
        self._sid = sid

    def recent(self, limit: int = 20, clean_only: bool = False):
        return [FakeSessionInfo(self._sid)] if self._sid else []


@pytest.fixture
def astore(tmp_path):
    s = AttentionStore(tmp_path / "attn.db")
    yield s
    s.close()


def _decision(intent: Intent) -> SchedulerDecision:
    return SchedulerDecision(intent=intent, reason="睡前，聊睡眠正合适",
                             effective_score=0.85, context_fit=1.0)


def _intent() -> Intent:
    return Intent(subject=SUBJECT, title=f"关心{SUBJECT}",
                  reason="昨晚睡了 4.5 小时，比平常少 2.7 小时",
                  attention_strength=0.85)


# ---------------------------------------------------------------- 发出去


def test_speak_pushes_and_saves_session(astore):
    core, sessions, store = FakeCore(), FakeSessions(), FakeStore()
    speak = build_speaker(core, sessions, store, astore)

    intent = _intent()
    speak(intent, _decision(intent))

    path, payload = core.bridge.calls[0]
    assert path == "/api/push/send"
    assert payload["body"].startswith("昨晚才睡了四个半小时")
    # 带 session_id，否则锁屏有话、点进去是空的
    assert payload["session_id"] == "a" * 32
    # 也进了他自己的会话 —— 她回「嗯」的时候他得知道自己说过什么
    assert sessions.saved["a" * 32]


def test_uses_her_latest_session(astore):
    """落在她最近在聊的那条对话里，不是新开一个。"""
    core, sessions = FakeCore(), FakeSessions()
    speak = build_speaker(core, sessions, FakeStore("b" * 32), astore)
    intent = _intent()
    speak(intent, _decision(intent))
    assert core.bridge.calls[0][1]["session_id"] == "b" * 32


def test_falls_back_when_no_session(astore):
    """全新部署上一条真实会话都没有，也不能崩。"""
    core, sessions = FakeCore(), FakeSessions()
    speak = build_speaker(core, sessions, FakeStore(None), astore)
    intent = _intent()
    speak(intent, _decision(intent))
    assert core.bridge.calls[0][1]["session_id"] == FALLBACK_SESSION


# ---------------------------------------------------------------- 别硬发


def test_empty_text_raises_instead_of_pushing(astore):
    """模型没吐字就别推。宁可这次不响，也不推一句空的。"""
    core, sessions = FakeCore(text="", ok=False), FakeSessions()
    speak = build_speaker(core, sessions, FakeStore(), astore)
    intent = _intent()
    with pytest.raises(RuntimeError):
        speak(intent, _decision(intent))
    assert core.bridge.calls == []


def test_bridge_failure_raises(astore):
    """⚠️ 最要命的一条：推送失败必须抛出去。

    吞掉的话 `service._speak()` 会记成「已发出」，冷却照常起 ——
    结果是这件事今天再也不会说了，而她什么都没收到。
    """
    core = FakeCore(bridge=FakeBridge(ok=False))
    speak = build_speaker(core, FakeSessions(), FakeStore(), astore)
    intent = _intent()
    with pytest.raises(RuntimeError):
        speak(intent, _decision(intent))


def test_no_bridge_raises(astore):
    core = FakeCore()
    core.bridge = None
    speak = build_speaker(core, FakeSessions(), FakeStore(), astore)
    intent = _intent()
    with pytest.raises(RuntimeError):
        speak(intent, _decision(intent))


# ---------------------------------------------------------------- 别唠叨


def test_prompt_mentions_repeat_count():
    """说过几次要写进 prompt，否则第三天还是同一句话。"""
    intent = _intent()
    now = datetime.now(timezone.utc)
    fresh = build_prompt(intent, _decision(intent), 0, None, now)
    again = build_prompt(intent, _decision(intent), 2,
                         now - timedelta(hours=25), now)

    assert "已经跟她说过" not in fresh
    assert "已经跟她说过 2 次" in again
    assert "昨天" in again


def test_prompt_carries_the_number():
    """关心必须落在具体数字上 —— 空泛地问「睡得好吗」不看数据也说得出口。"""
    intent = _intent()
    p = build_prompt(intent, _decision(intent), 0, None, EVENING)
    assert "4.5 小时" in p
    assert "22:00" in p              # 她那边的时间，不是服务器时间
    assert "睡前" in p


def test_counts_only_real_sends(astore):
    """dry-run 那些不算「跟她说过」。

    不排掉的话，观察期攒的几十条 dry-run 记录会让他一上线就觉得
    自己已经唠叨了很多次，第一句话就先道歉。
    """
    core, sessions = FakeCore(), FakeSessions()
    speak = build_speaker(core, sessions, FakeStore(), astore)

    # 手动塞一条 dry-run 记录进库
    old = _intent()
    old.status = TRIGGERED
    old.action_history = [{
        "at": datetime.now(timezone.utc).isoformat(),
        "action": "triggered",
        "note": "dry-run，没有真的发",
    }]
    from attention.intent import IntentEngine
    astore.save_intents(IntentEngine([old]))

    intent = _intent()
    speak(intent, _decision(intent))
    assert "已经跟她说过" not in core.prompts[0]


def test_counts_previous_real_send(astore):
    core, sessions = FakeCore(), FakeSessions()
    speak = build_speaker(core, sessions, FakeStore(), astore)

    old = _intent()
    old.status = TRIGGERED
    old.action_history = [{
        "at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        "action": "triggered",
        "note": "已发出",
    }]
    from attention.intent import IntentEngine
    astore.save_intents(IntentEngine([old]))

    intent = _intent()
    speak(intent, _decision(intent))
    assert "已经跟她说过 1 次" in core.prompts[0]


# ---------------------------------------------------------------- 接进 service


def test_end_to_end_through_service(astore):
    """睡眠数据 → Concern → Intent → Scheduler → 真的推出去。"""
    core, sessions = FakeCore(), FakeSessions()
    speak = build_speaker(core, sessions, FakeStore(), astore)

    svc = AttentionService(astore, _sleep_provider(4.5),
                           RelationshipState(), speaker=speak)
    assert not svc.dry_run

    d = svc.tick(EVENING)
    assert d.will_speak
    assert core.bridge.calls[0][0] == "/api/push/send"

    # 记成「已发出」而不是 dry-run
    triggered = [i for i in svc.intents.all() if i.action_history]
    assert triggered[0].action_history[-1]["note"] == "已发出"


def test_service_records_failure_not_success(astore):
    """bridge 挂了的时候，绝不能记成「已发出」。"""
    core = FakeCore(bridge=FakeBridge(ok=False))
    speak = build_speaker(core, FakeSessions(), FakeStore(), astore)

    svc = AttentionService(astore, _sleep_provider(4.5),
                           RelationshipState(), speaker=speak)
    svc.tick(EVENING)      # 不抛
    triggered = [i for i in svc.intents.all() if i.action_history]
    assert triggered[0].action_history[-1]["note"] == "发送失败"


from datetime import timedelta  # noqa: E402

# ------------------------------------------------------------------ 时间口径
#
# 糖糖 2026-09-14：「上午说的话，他会说是昨天聊的」。
# 主动开口这条路上他手里只有 prompt 和历史 —— 两边都得有绝对日期才算得出来。


# ⏸ 「提示里必须有日期」那条测试跟着 c3218c4 一起暂缓了 ——
# 断言留着而行为退了的话，它会变成一条永远红的测试。
# 要上的时候连测试一起从 `git show c3218c4` 取回来。
#
# ⚠️ 下面这条**留着**：日历天口径属于第一层（temporal.relative），
# 和 c3218c4 无关，已经上线。


def test_今天昨天按日历天算不按小时差():
    """🔴 原来是 hours<20 → 今天，两头都会错。

    · 凌晨 2 点说的话，当天 23 点回看 = 21 小时 → 旧逻辑报「昨天」，而那是同一天
    · 昨晚 23 点的话，今早 9 点看 = 10 小时 → 旧逻辑报「今天」，而那是昨天
    """
    from datetime import datetime, timezone
    from attention.speaker import _humanize

    CST = timezone(timedelta(hours=8))

    def cst(d, h):
        """直接造一个中国时间 —— 判据是「她那边是哪天」，不是 UTC 哪天。"""
        return datetime(2026, 9, d, h, tzinfo=CST)

    # 同一天：CST 02:00 → CST 23:00（21 小时）
    assert _humanize(cst(14, 2), cst(14, 23)) == "今天", "同一天被说成昨天"
    # 跨天：CST 昨晚 23:00 → 今早 09:00（10 小时）
    assert _humanize(cst(13, 23), cst(14, 9)) == "昨天", "隔了一天被说成今天"
    # 再往前 —— 2026-09-14 起和 timeline 共用同一张词表（审计 F6）。
    # 措辞有两处变化，都落在 speaker 的 7 天窗口内：
    #   「2 天前」→「前天」   「3 天前」→「3天前」（少一个空格）
    # 断言写死新口径，就是为了下次有人再分叉时这里会红
    assert _humanize(cst(12, 12), cst(14, 12)) == "前天"
    assert _humanize(cst(11, 12), cst(14, 12)) == "3天前"

    # 和 timeline 那侧必须逐字相同 —— 两套并一套的意义就在这
    from temporal import humanize
    assert _humanize(cst(11, 12), cst(14, 12)) == humanize(3)
