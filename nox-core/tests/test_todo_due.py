"""到点追待办（`Todo-Daily-Planner-设计.md` 第二节）。

钉住糖糖 2026-08-18 拍板的那套节奏：

    1 小时追一次 · 链内最多 3 次 · 跨天重开
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from attention.care import (
    TASK,
    CareOrchestrator,
    CareSignal,
    SourcePolicy,
    ThreadBook,
)
from attention.sources.todo_due import MAX_CHASE, TodoDueSource

T0 = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)


class FakeResult:
    def __init__(self, ok=True, data=None, error=None):
        self.ok, self.data, self.error = ok, data, error


class FakeBridge:
    """假 bridge。记下 POST 过什么，方便断言「有没有标记追完」。"""

    def __init__(self, items=None, ok=True):
        self.items = items or []
        self.ok = ok
        self.posted: list[tuple[str, dict]] = []

    def get(self, path, params=None):
        if not self.ok:
            return FakeResult(False, error="连不上 bridge")
        return FakeResult(True, {"items": self.items})

    def post(self, path, body=None):
        self.posted.append((path, body or {}))
        return FakeResult(True, {"ok": True})


def _policy():
    return {"todo": SourcePolicy(
        takes_quota=False, takes_gate=False,
        max_steps=MAX_CHASE, min_step_gap_min=60, thread_kind=TASK,
    )}


# ---------------------------------------------------------------- Source


def test_到期的待办变成任务型念头():
    src = TodoDueSource(FakeBridge([
        {"id": "t1", "text": "运动", "at": "20:00", "repeat": "daily"},
    ]))
    signals = src.poll(T0)
    assert len(signals) == 1
    s = signals[0]
    assert s.source == "todo"
    assert s.subject == "运动"
    assert s.thread_kind == TASK, "必须是任务型：她回话不代表事做了"
    assert s.payload["todo_id"] == "t1"


def test_读不到就当没念头不拿旧清单顶上():
    """拿过期的清单去追人，会追一件她刚划掉的事。"""
    src = TodoDueSource(FakeBridge(ok=False))
    assert src.poll(T0) == []


def test_没有id或正文的条目跳过():
    src = TodoDueSource(FakeBridge([
        {"id": "", "text": "没有 id"},
        {"id": "t2", "text": "   "},
        {"id": "t3", "text": "好的那条", "at": "20:00"},
    ]))
    assert [s.subject for s in src.poll(T0)] == ["好的那条"]


def test_续链用记住的thread_id():
    src = TodoDueSource(FakeBridge([{"id": "t1", "text": "运动", "at": "20:00"}]))
    assert src.poll(T0)[0].thread_id is None
    src.remember_thread("t1", "care-abc")
    assert src.poll(T0)[0].thread_id == "care-abc"


# ---------------------------------------------------------------- 节奏


def test_一天只追一次():
    """糖糖 2026-09-15：「我感觉他一天一直提醒我，因为还有上下文。
    主动一次就可以了。」—— 到点说一次链就关，mark_fired 让今天收手；
    跨天重开由 bridge 的 fired_on 日期失效保证。"""
    o = CareOrchestrator(ThreadBook(), lambda s, t, n: True, policies=_policy())
    first = o._decide(CareSignal(source="todo", subject="运动", thread_kind=TASK), T0)
    tid = first.thread.id
    assert first.action == "spoke"

    # 1 小时后再来同一件 —— 链已到步数上限，不再追
    again = o._decide(
        CareSignal(source="todo", subject="运动", thread_kind=TASK, thread_id=tid),
        T0 + timedelta(hours=1),
    )
    assert again.action == "dropped"
    assert "链已经关了" in again.reason


def test_追待办不吃开口闸():
    """她自己设的时间，不该被「今天额度用完了」挡掉。"""
    o = CareOrchestrator(
        ThreadBook(), lambda s, t, n: True,
        policies=_policy(), gate_check=lambda now: "今天额度用完了",
    )
    r = o._decide(CareSignal(source="todo", subject="运动", thread_kind=TASK), T0)
    assert r.action == "spoke"


def test_她回话了任务链不关():
    """2026-08-18 的规矩：只有「做完了」才翻篇。"""
    book = ThreadBook()
    t = book.open(TASK, "运动", session_id="s1", now=T0)
    book.close_on_reply("s1")
    assert t.alive


# ---------------------------------------------------------------- 跨天


def test_标记fired走bridge():
    """mark_fired 就是往 bridge 打一发 /api/todo/fired（一天一次的记账）。"""
    bridge = FakeBridge()
    src = TodoDueSource(bridge)
    src.mark_fired("t1")
    assert bridge.posted == [("/api/todo/fired", {"id": "t1"})]


def test_标记失败不抛异常():
    class Bad(FakeBridge):
        def post(self, path, body=None):
            return FakeResult(False, error="炸了")

    assert TodoDueSource(Bad()).mark_fired("t1") is False
