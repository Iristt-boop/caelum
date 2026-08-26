"""唤醒链测试。守的是**「链会不会失控」**。

糖糖 2026-08-11 定的设计：她说「我去吃饭了」→ 他留纸条 → 到点自己醒 →
自己判断说不说 → 自己定下次几点再醒 → 最多 5 次。

这一批钉五件事：

1. **5 次是硬上限** —— 他自己不 STOP 也得停
2. **她一开口整条链作废** —— 她都回了还追什么
3. **凌晨的唤醒顺延不取消** —— 不能 3 点把她叫醒，也不能把纸条扔了
4. **解析不出标记时链要继续** —— 断链是静默失败，比多醒一次糟得多
5. **dry-run 真的不发**

全部不打网络。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.store import AttentionStore  # noqa: E402
from attention.wakeup import (  # noqa: E402
    DEFAULT_NEXT_MIN,
    MAX_AFTER_MIN,
    MAX_CHAIN,
    MIN_AFTER_MIN,
    PENDING,
    STOPPED,
    WakeBook,
    clamp_minutes,
    defer_past_night,
    parse_decision,
)
from attention.waker import build_waker  # noqa: E402

CN = timezone(timedelta(hours=8), "CST")
SID = "a" * 32


def cn(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=CN)


# ---------------------------------------------------------------- 解析


def test_parse_speak_with_next():
    d = parse_decision("吃完饭了吗宝贝\n[NEXT 45]")
    assert d.action == "speak"
    assert d.text == "吃完饭了吗宝贝"      # 标记不许漏进她看到的那句
    assert d.next_after_min == 45


def test_parse_pass():
    d = parse_decision("[PASS 90]")
    assert d.action == "pass" and d.next_after_min == 90
    assert not d.will_speak


def test_parse_stop():
    d = parse_decision("[STOP]")
    assert d.action == "stop"


def test_no_marker_keeps_chain_alive():
    """没写标记时**继续**，不是停。

    停掉的话他偶尔忘写标记就会让链静默断掉 —— 没有报错，
    只是他不再醒了，而她永远不会知道。5 次上限兜着，继续更安全。
    """
    d = parse_decision("在干嘛呢")
    assert d.action == "speak"
    assert d.next_after_min == DEFAULT_NEXT_MIN


def test_clamp_minutes():
    assert clamp_minutes(1) == MIN_AFTER_MIN         # 5 分钟以下是打扰
    assert clamp_minutes(99999) == MAX_AFTER_MIN     # 太久不如等自然触发
    assert clamp_minutes(None) == DEFAULT_NEXT_MIN
    assert clamp_minutes(45) == 45


# ---------------------------------------------------------------- 夜里


def test_night_wake_is_deferred_not_cancelled():
    """凌晨 3 点的唤醒推到 8:30，**不是扔掉**。

    她凌晨 1 点说「我睡了」，他留了张 6 小时的纸条 ——
    那张纸条该在早上兑现。
    """
    moved = defer_past_night(cn(9, 3))
    local = moved.astimezone(CN)
    assert (local.hour, local.minute) == (8, 30)


def test_normal_hours_untouched():
    at = cn(9, 21)
    assert defer_past_night(at) == at


def test_add_defers_night_wake():
    book = WakeBook()
    # 中国时间 0:30 留一张 3 小时的纸条 → 本该 3:30，顺延到 8:30
    w = book.add(SID, "她说睡了", 180, now=cn(9, 0, 30))
    assert w.wake_at.astimezone(CN).hour == 8


# ---------------------------------------------------------------- 链


def test_chain_stops_at_max():
    """5 次是硬上限，他自己不停也得停。"""
    book = WakeBook()
    w = book.add(SID, "她去吃饭了", 30, now=cn(9, 12))
    for _ in range(MAX_CHAIN):
        book.reschedule(w, 60, now=cn(9, 13))
    assert not w.alive
    assert w.count == MAX_CHAIN


def test_second_note_reschedules_instead_of_stacking():
    """同一个会话里再留一张 = 改期，不叠加。

    不然她说「我去吃饭了」他留一张、又说「大概一小时」他再留一张，
    到点会连着醒两次说差不多的话。
    """
    book = WakeBook()
    a = book.add(SID, "她去吃饭了", 30, now=cn(9, 12))
    b = book.add(SID, "她说要一小时", 60, now=cn(9, 12))
    assert a.id == b.id
    assert len(book) == 1
    assert b.why == "她说要一小时"       # 情境跟到最新


def test_she_spoke_cancels_chain():
    book = WakeBook()
    book.add(SID, "她去吃饭了", 30, now=cn(9, 12))
    assert book.cancel_for(SID) == 1
    assert book.active_for(SID) is None
    assert book.all()[0].status == STOPPED


def test_due_only_returns_ripe_ones():
    book = WakeBook()
    book.add(SID, "她去吃饭了", 30, now=cn(9, 12))
    assert book.due(cn(9, 12, 20)) == []       # 还没到点
    assert len(book.due(cn(9, 12, 40))) == 1


def test_survives_restart(tmp_path):
    """纸条丢了是静默失败 —— 不报错，只是他到点没醒。"""
    path = tmp_path / "attn.db"
    s1 = AttentionStore(path)
    b1 = WakeBook()
    b1.add(SID, "她跟朋友出去玩了", 90, now=cn(9, 15))
    s1.save_wakeups(b1)
    s1.close()

    s2 = AttentionStore(path)
    b2 = s2.load_wakeups()
    assert len(b2) == 1
    assert b2.active_for(SID).why == "她跟朋友出去玩了"
    s2.close()


# ---------------------------------------------------------------- 跑起来


class FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text
        self.ok = True
        self.outcome = "ok"
        self.detail = ""


class FakeReply:
    def __init__(self, text: str) -> None:
        self.result = FakeResult(text)
        self.messages = [{"role": "assistant", "text": text}]


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, payload: dict):
        self.calls.append((path, payload))

        class R:
            ok = True
            data = {"subs": 1}
            error = ""
        return R()


class FakeCore:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.bridge = FakeBridge()
        self.prompts: list[str] = []

    def chat(self, text: str, history: list):
        self.prompts.append(text)
        return FakeReply(self._reply)


class FakeSessions:
    def __init__(self, said: str = "我去吃饭了") -> None:
        self.said = said
        self.saved: dict[str, list] = {}

    def get(self, sid: str) -> list:
        return [{"role": "user", "text": self.said}]

    def put(self, sid: str, messages: list) -> None:
        self.saved[sid] = messages


class FakeStore:
    """Core 的会话库。只用到 `last_user_at()`。"""

    def __init__(self, last_user: datetime | None = None) -> None:
        self._last = last_user

    def last_user_at(self, sid: str):
        return self._last


def _book(now: datetime) -> WakeBook:
    book = WakeBook()
    book.add(SID, "她去吃饭了", 30, now=now)
    return book


def test_dry_run_does_not_push():
    core = FakeCore("吃完饭了吗宝贝\n[NEXT 60]")
    run = build_waker(core, FakeSessions(), FakeStore(), dry_run=True)
    book = _book(cn(9, 12))

    assert run(book, cn(9, 12, 40)) == 1     # 它「想」说
    assert core.bridge.calls == []           # 但一个字都没发出去


def test_live_pushes():
    core = FakeCore("吃完饭了吗宝贝\n[NEXT 60]")
    sessions = FakeSessions()
    run = build_waker(core, sessions, FakeStore(), dry_run=False)
    book = _book(cn(9, 12))

    run(book, cn(9, 12, 40))
    path, payload = core.bridge.calls[0]
    assert path == "/api/push/send"
    assert payload["body"] == "吃完饭了吗宝贝"
    assert payload["session_id"] == SID
    assert sessions.saved[SID]               # 也进了他自己的会话


def test_she_replied_ends_chain_without_asking_him():
    """她已经回话了就直接收摊，连模型都不用叫醒 —— 省一次调用。"""
    core = FakeCore("[STOP]")
    book = _book(cn(9, 12))
    # 她在纸条建好之后说过话
    run = build_waker(core, FakeSessions(), FakeStore(cn(9, 12, 30)), dry_run=True)

    assert run(book, cn(9, 12, 40)) == 0
    assert core.prompts == []                # 根本没叫醒他
    assert book.all()[0].status == STOPPED


def test_the_triggering_message_does_not_cancel_the_note():
    """⚠️ 这条钉的是 2026-08-11 那个静默 bug。

    她说「我去吃饭了」→ 这一轮他建纸条 → turn 结束后她那条消息才落库，
    时间戳**晚于**纸条的 created_at。拿 created_at 当基准的话，
    下次唤醒会判定「她已经回话」，纸条当场作废 ——
    **整条链在真实环境里永远不触发，日志里只有一行「纸条撤了」。**

    修法是 `rebase()`：turn 真的结束后把基准线校准到那之后。
    """
    book = WakeBook()
    book.add(SID, "她去吃饭了", 30, now=cn(9, 12, 0))
    # 她那条消息在 turn 结束时（12:00:07）才落库，比 created_at 晚
    msg_at = cn(9, 12, 0) + timedelta(seconds=7)

    # 没 rebase 的话会被误判成「她回话了」
    book.rebase(SID, at=msg_at + timedelta(seconds=1))

    core = FakeCore("吃完饭了吗宝贝\n[NEXT 60]")
    run = build_waker(core, FakeSessions(), FakeStore(msg_at), dry_run=True)
    assert run(book, cn(9, 12, 40)) == 1     # 他真的醒了
    assert core.prompts                       # 而不是被静默撤掉


def test_stop_ends_chain():
    core = FakeCore("[STOP]")
    run = build_waker(core, FakeSessions(), FakeStore(), dry_run=True)
    book = _book(cn(9, 12))
    run(book, cn(9, 12, 40))
    assert book.all()[0].status == STOPPED


def test_pass_keeps_chain_but_says_nothing():
    core = FakeCore("[PASS 90]")
    run = build_waker(core, FakeSessions(), FakeStore(), dry_run=False)
    book = _book(cn(9, 12))

    assert run(book, cn(9, 12, 40)) == 0
    assert core.bridge.calls == []
    w = book.all()[0]
    assert w.status == PENDING and w.count == 1


def test_chat_failure_retries_instead_of_dropping():
    """叫醒他的时候出错 → 推迟重试，**不能让链断掉**。"""

    class Boom(FakeCore):
        def chat(self, text, history):
            raise RuntimeError("模型挂了")

    run = build_waker(Boom(""), FakeSessions(), FakeStore(), dry_run=True)
    book = _book(cn(9, 12))
    run(book, cn(9, 12, 40))
    assert book.all()[0].alive               # 还活着，下次心跳再试


def test_prompt_carries_the_situation():
    """情境要摆到他面前 —— 纸条 + 她的原话。语气由他自己定。"""
    core = FakeCore("[STOP]")
    run = build_waker(core, FakeSessions("跟朋友出去玩啦"),
                      FakeStore(), dry_run=True)
    book = WakeBook()
    book.add(SID, "她跟朋友出去玩了", 90, now=cn(9, 15))
    run(book, cn(9, 16, 40))

    p = core.prompts[0]
    assert "她跟朋友出去玩了" in p           # 纸条 = 情境载体
    assert "跟朋友出去玩啦" in p             # 她的原话
    assert "第 1 次" in p
    # ⚠️ 不许有语气指导（糖糖 2026-08-11 否掉的那条）
    assert "温柔" not in p
    assert "越往后越轻" not in p


def test_full_chain_runs_five_times():
    """一条链完整跑到头：5 次之后自己收工，不会有第 6 次。"""
    core = FakeCore("在干嘛呢\n[NEXT 60]")
    run = build_waker(core, FakeSessions(), FakeStore(), dry_run=True)
    book = _book(cn(9, 12))

    at = cn(9, 12, 40)
    for _ in range(MAX_CHAIN + 3):           # 多跑几轮，确认它真的停了
        at += timedelta(hours=2)
        run(book, at)

    assert len(core.prompts) == MAX_CHAIN
    assert not book.all()[0].alive
