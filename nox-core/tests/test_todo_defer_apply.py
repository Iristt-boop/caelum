"""Temporal P4：她说「哪天再做」→ 认出是哪条待办 → 推到那天再追（2026-10-06 她：上线接 P4）。

🔴 两条底线：
  · 只推迟、不完成 —— 这里没有任何「标完成」的路
  · 认不准就不动 —— 模型只能从清单里选，回别的一律当拿不准
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

from attention import todo_defer
from temporal import CST
from temporal.intent import Intent
from temporal.resolver import resolve

TODAY = date(2026, 10, 6)
REF = datetime(2026, 10, 6, 9, 0, tzinfo=CST)
TODOS = [{"id": "t-leg", "text": "臀腿训练"}, {"id": "t-word", "text": "背英语单词"}]


class Bridge:
    def __init__(self, todos=TODOS, defer_ok=True):
        self.todos, self.defer_ok, self.posts = todos, defer_ok, []

    def get(self, path):
        assert path == "/api/todo/list"
        return SimpleNamespace(ok=True, data={"items": self.todos})

    def post(self, path, body):
        self.posts.append((path, body))
        return SimpleNamespace(ok=self.defer_ok, error=None if self.defer_ok else "HTTP 500")


class Ask:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def run(*, intent, act="plan", event="练腿", answer="t-leg", bridge=None):
    bridge = bridge or Bridge()
    ask = Ask(answer)
    a = todo_defer.apply(act=act, expression="明天", event=event, text=f"今天不练了，{event}明天",
                         resolution=resolve(intent, REF), today=TODAY, bridge=bridge, ask=ask)
    return a, bridge, ask


def test_明天再练_推到明天():
    a, bridge, ask = run(intent=Intent(kind="day_offset", n=1))
    assert a.applied and a.todo_id == "t-leg" and a.todo_match_status == "matched"
    assert bridge.posts == [("/api/todo/defer", {"id": "t-leg", "until": "2026-10-07"})]
    assert "臀腿训练" in a.applied_to and "2026-10-07" in a.applied_to
    assert "臀腿训练" in ask.prompts[0], "清单要给模型看"


def test_后天和下周五_推到那天():
    a, bridge, _ = run(intent=Intent(kind="day_offset", n=2), event="背单词", answer="t-word")
    assert a.applied and bridge.posts[0][1]["until"] == "2026-10-08"
    a, bridge, _ = run(intent=Intent(kind="weekday_next", weekday=5), event="背单词", answer="t-word")
    assert a.applied and bridge.posts[0][1]["until"] == "2026-10-16"


def test_光说周五_有歧义_不推():
    """「周五」是这周还是下周，契约里判有歧义（09-14 起不猜）—— 宁可不推，也不推错一周。"""
    a, bridge, ask = run(intent=Intent(kind="weekday_bare", weekday=5))
    assert not a.applied and "weekday_ambiguous" in a.why_not and ask.prompts == []


def test_已经发生的事不推_也不去问模型():
    a, bridge, ask = run(intent=Intent(kind="day_offset", n=1), act="report")
    assert not a.applied and bridge.posts == [] and ask.prompts == []


def test_今天的事不推_也不去问模型():
    a, bridge, ask = run(intent=Intent(kind="day_offset", n=0))
    assert not a.applied and a.why_not and ask.prompts == [] and bridge.posts == []


def test_一会_不推():
    a, _, ask = run(intent=Intent(kind="vague"))
    assert not a.applied and ask.prompts == []


def test_没有待办就不问():
    a, _, ask = run(intent=Intent(kind="day_offset", n=1), bridge=Bridge(todos=[]))
    assert not a.applied and a.todo_match_status == "no_candidate" and ask.prompts == []


def test_模型说none_不动():
    a, bridge, _ = run(intent=Intent(kind="day_offset", n=1), event="去驻马店", answer="none")
    assert not a.applied and a.todo_match_status == "no_candidate" and bridge.posts == []


def test_模型回表外的东西_当拿不准_不动():
    for answer in ("t-legx", "臀腿训练", "我觉得是 t-leg", ""):
        a, bridge, _ = run(intent=Intent(kind="day_offset", n=1), answer=answer)
        assert not a.applied and a.todo_match_status == "ambiguous", answer
        assert bridge.posts == []


def test_模型挂了_不动():
    a, bridge, _ = run(intent=Intent(kind="day_offset", n=1), answer=RuntimeError("429"))
    assert not a.applied and bridge.posts == []


def test_bridge没推成_说出来():
    a, _, _ = run(intent=Intent(kind="day_offset", n=1), bridge=Bridge(defer_ok=False))
    assert not a.applied and "没推成" in a.why_not and a.todo_id == "t-leg"


def test_结局能原样填进TemporalResult():
    """TemporalResult 的校验（没接要说为什么、带 id 只能是 matched）每种结局都得过。"""
    from temporal.event import TemporalEvent
    from temporal.result import TemporalResult
    cases = [
        run(intent=Intent(kind="day_offset", n=1))[0],
        run(intent=Intent(kind="day_offset", n=1), act="report")[0],
        run(intent=Intent(kind="day_offset", n=1), answer="none")[0],
        run(intent=Intent(kind="day_offset", n=1), bridge=Bridge(defer_ok=False))[0],
    ]
    ev = TemporalEvent(expression="明天", event="练腿", act="plan", intent=Intent(kind="day_offset", n=1))
    for a in cases:
        TemporalResult(text="明天练腿", event=ev, resolution=resolve(ev.intent, REF), reference_time=REF,
                       applied=a.applied, applied_to=a.applied_to, why_not_applied=a.why_not,
                       todo_match_status=a.todo_match_status, todo_id=a.todo_id).log()
