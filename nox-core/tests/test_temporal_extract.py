"""Temporal Extraction + shadow 出口 + Todo 推迟决策（第二层的另一半）。

契约见 `CAELUM-Temporal-Intent-Contract.md`。

## 这些测试能挡什么

- 提示词里混进日期（那条红线的落地方式就是"不给材料"）
- 模型输出的表外 kind / 未知字段被放行进 Core
- shadow 日志缺了「为什么没接下游」那一段
- Todo 决策里长出「标完成」的可能
- 「没有时间表达」和「解析不了」被混成一件事

## 挡不住什么

- 真实模型认得准不准 —— 那要跑真实数据（shadow 就是为这个）
- 提示词改了之后模型行为怎么变 —— 只能靠观察
- **`message_time` 缺失时那条退回分支**。两个端点现在都传了，
  所以应用层够不到它，是防御性代码。变异「把那条 warning 去掉」
  抓不到 —— 如实写在这儿，而不是造一个扭曲的测试假装覆盖
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.todo_defer import (  # noqa: E402
    MECH_FIRED, MECH_NEEDS_FIELD, DeferDecision, decide,
)
from temporal import CST  # noqa: E402
from temporal.event import TemporalEvent  # noqa: E402
from temporal.extract import _PROMPT, TemporalExtractor, mode  # noqa: E402
from temporal.intent import Intent  # noqa: E402
from temporal.resolver import Resolution, resolve  # noqa: E402
from temporal.result import TemporalResult  # noqa: E402

REF = datetime(2026, 9, 14, 12, 0, tzinfo=CST)   # 周一
TODAY = date(2026, 9, 14)


class FakeAdapter:
    """按预设回一段文本。记下它收到的 system，给提示词那条测试用。"""

    def __init__(self, text: str, stop: str = "end_turn") -> None:
        self.text, self.stop = text, stop
        self.seen_system: list[str] = []

    def complete(self, messages, tools, system=None, **kw):
        self.seen_system.append(system or "")
        return type("T", (), {"text": self.text, "stop_reason": self.stop, "error": None})()


class DeadAdapter:
    def complete(self, *a, **kw):
        raise RuntimeError("模型挂了")


def _x(text: str) -> TemporalExtractor:
    return TemporalExtractor(FakeAdapter(text))


def _ev(expression: str, temporal: dict, event: str = "去练腿", act: str = "plan") -> dict:
    return {"expression": expression, "event": event, "act": act, "temporal": temporal}


def _raw(*events: dict) -> str:
    """模型该吐的形状（2026-09-28 第二版）：`{"events": [...]}`。"""
    return json.dumps({"events": list(events)}, ensure_ascii=False)


def _one(raw: str, text: str) -> Intent | None:
    """只认出一个事件时，取它的时间关系 —— 老用例大多是单时间的。"""
    got = _x(raw).extract(text)
    assert len(got) <= 1, f"只该认出一个，拿到 {got}"
    return got[0].intent if got else None


# ---------------------------------------------------------------- 那条红线

def test_提示词里一个日期都不给():
    """🔴 契约第二节：不是在提示词里写「别算日期」，是**不给它算的材料**。

    能挡什么：有人为了"帮模型一把"往 system 里塞今天的日期。
    挡不住什么：模型自己幻觉出一个日期 —— 那由 `Intent` 没有
                resolved_* 字段 + `from_dict` 拒绝未知字段挡（下面两条）。
    """
    import re
    # 判据挑**具体的日期值**，不挑字眼。
    #
    # ⚠️ 第一版写了 `assert "今天是" not in _PROMPT`，结果它匹配到了
    # 提示词里那句「你**不知道**今天是几号」—— 一句否定句。
    # 挑字眼的判据会在语义相反的地方成立，挑值不会。
    for pattern, why in [
        (r"20\d{2}", "年份"),
        (r"\d{1,2}月\d{1,2}日", "中文日期"),
        (r"\d{4}-\d{2}-\d{2}", "ISO 日期"),
    ]:
        hit = re.search(pattern, _PROMPT)
        assert hit is None, f"提示词里出现了{why}：{hit.group(0) if hit else ''}"


def test_运行时也不会把日期塞进去():
    """静态查提示词不够 —— 还得确认调用时没在别处拼上去。"""
    a = FakeAdapter(_raw(_ev("明天", {"kind": "day_offset", "n": 1})))
    TemporalExtractor(a).extract("明天去练腿")
    import re
    assert not re.search(r"20\d{2}", a.seen_system[0])


def test_模型硬塞_resolved_字段会被拒():
    """🔴 `from_dict` 是模型输出进 Core 的唯一入口。

    糖糖 2026-09-14 判它「结构正确」：它把「模型可以自由生成 JSON」
    和「系统允许什么进入 Core」切开了。
    """
    raw = _raw(_ev("明天", {"kind": "day_offset", "n": 1, "resolved_date": "2026-09-15"}))
    assert _x(raw).extract("明天去") == []
    #: 事件那一层也一样：多一个字段就不放行
    raw = _raw({**_ev("明天", {"kind": "day_offset", "n": 1}), "resolved_date": "2026-09-15"})
    assert _x(raw).extract("明天去") == []


def test_表外的kind会被拒():
    assert _x(_raw(_ev("下个月", {"kind": "next_month"}))).extract("下个月") == []


def test_表外的act会被拒():
    """act 是封闭集合 —— 模型自创一个 greeting / task 进不来。"""
    assert _x(_raw(_ev("明天", {"kind": "day_offset", "n": 1}, act="task"))).extract("明天去") == []


# ---------------------------------------------------------------- 正常路径

@pytest.mark.parametrize("expression, temporal, expect", [
    ("明天", {"kind": "day_offset", "n": 1}, Intent(kind="day_offset", n=1)),
    ("下周三", {"kind": "weekday_next", "weekday": 3}, Intent(kind="weekday_next", weekday=3)),
    ("周五", {"kind": "weekday_bare", "weekday": 5}, Intent(kind="weekday_bare", weekday=5)),
    ("月底", {"kind": "month_end"}, Intent(kind="month_end")),
    ("两个小时后", {"kind": "duration", "hours": 2, "direction": "future"},
     Intent(kind="duration", hours=2, direction="future")),
    ("这3个小时", {"kind": "duration", "hours": 3, "direction": "past", "span": True},
     Intent(kind="duration", hours=3, direction="past", span=True)),
    ("昨天晚上", {"kind": "day_offset", "n": -1, "slot": "evening"},
     Intent(kind="day_offset", n=-1, slot="evening")),
])
def test_认出来的形状(expression, temporal, expect):
    assert _one(_raw(_ev(expression, temporal)), f"{expression}随便一句") == expect


def test_围栏要剥掉():
    """便宜模型很爱包 ```json，那不算它出错。"""
    raw = "```json\n" + _raw(_ev("明天", {"kind": "day_offset", "n": 1})) + "\n```"
    assert _one(raw, "明天去") == Intent(kind="day_offset", n=1)


# ---------------------------------------------------------------- 一句话多个事件（2026-09-28）

def test_一句话两个时间各挂各的事件():
    """🔴 shadow 真实句子。第一版只抽到「今晚」，接 Todo 就是「蒸蛋 @ 今晚」。"""
    text = "早上起得晚嘛。收到领导，我今天晚上就去放好鸡蛋，明天你给我蒸"
    raw = _raw(
        _ev("今天晚上", {"kind": "day_offset", "n": 0, "slot": "evening"}, event="放好鸡蛋"),
        _ev("明天", {"kind": "day_offset", "n": 1}, event="蒸鸡蛋", act="request"),
    )
    got = _x(raw).extract(text)
    assert [(e.expression, e.event, e.act) for e in got] == [
        ("今天晚上", "放好鸡蛋", "plan"), ("明天", "蒸鸡蛋", "request")]
    #: 蒸蛋落在明天，不是今晚
    steam = next(e for e in got if e.event == "蒸鸡蛋")
    assert resolve(steam.intent, REF).date == date(2026, 9, 15)


def test_坏的那个事件不连累好的(caplog):
    """逐条丢，不整句丢。"""
    raw = _raw(
        _ev("明天", {"kind": "day_offset", "n": 1}),
        _ev("下个月", {"kind": "next_month"}, event="搬家"),
    )
    with caplog.at_level(logging.WARNING):
        got = _x(raw).extract("明天去练腿，下个月搬家")
    assert [e.expression for e in got] == ["明天"]
    assert "不合契约" in caplog.text, "丢了但没留痕"


def test_原话里没有的时间词不放行(caplog):
    """🔴 结构性的防编造：它说她说了「后天」，原话里没有 —— 进不来。"""
    raw = _raw(_ev("后天", {"kind": "day_offset", "n": 2}))
    with caplog.at_level(logging.WARNING):
        assert _x(raw).extract("明天去练腿") == []
    assert "不在她原话里" in caplog.text


def test_比对原话忽略空白():
    """她打字常带空格 —— 「10点 好了」里的「10点」不能因为空格被判成编的。"""
    raw = _raw(_ev("明天 上午", {"kind": "day_offset", "n": 1, "slot": "morning"}))
    assert len(_x(raw).extract("明天上午去")) == 1


def test_一句话认出太多个只留前几个(caplog):
    from temporal.event import MAX_EVENTS
    text = "今天" * (MAX_EVENTS + 3)
    raw = _raw(*[_ev("今天", {"kind": "day_offset", "n": 0})] * (MAX_EVENTS + 3))
    with caplog.at_level(logging.WARNING):
        assert len(_x(raw).extract(text)) == MAX_EVENTS
    assert "只留前" in caplog.text


# ---------------------------------------------------------------- duration 的方向（2026-09-28）

def test_duration_不给方向会被拒():
    """🔴 不许默认往后 —— 那正是「这3个小时」落到三小时之后的原因。"""
    raw = _raw(_ev("3个小时", {"kind": "duration", "hours": 3}))
    assert _x(raw).extract("这3个小时连不上你") == []


# ---------------------------------------------------------------- 没说时间 ≠ 解析不了

def test_没有时间表达就不产出事件():
    """🔴 「她没说时间」根本不产出事件；
    「说了但系统定不了是哪天」产出事件 + precision=none。
    两者是不同的系统事实，不能混（契约第五节 + weekday_bare 的由来）。
    """
    assert _x(_raw()).extract("我有点累") == []


def test_没有时间表达不该产生警告(caplog):
    """🔴 绝大多数话都没有时间表达 —— 那是**常态不是错**。

    如果它走「不合契约」那条告警分支，shadow 日志会被寻常句子淹掉，
    真正该看的东西反而找不到。
    （变异测试抓到的：把空列表那个分支去掉，输出仍然是空，
      只有日志级别变了 —— 只断言返回值的话这条永远绿。）
    """
    with caplog.at_level(logging.DEBUG):
        assert _x(_raw()).extract("我有点累") == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING],         "寻常句子产生了警告 —— shadow 日志会被淹"


def test_说了周几是产出事件而不是没有():
    i = _one(_raw(_ev("周三", {"kind": "weekday_bare", "weekday": 3})), "周三去")
    assert i is not None, "「周三」被当成了『没说时间』—— 那两件事必须分开"
    assert not resolve(i, REF).ok


# ---------------------------------------------------------------- 坏输入

@pytest.mark.parametrize("raw", [
    "不是 JSON", "[1,2,3]", "", '{"kind": "day_offset", "n": 1}',   # 第一版的形状也不认了
    '{"events": "明天"}', '{"events": [1]}',
    _raw(_ev("明天", {"kind": "day_offset"})),                       # 缺字段
    _raw({"expression": "明天", "event": "", "act": "plan",
          "temporal": {"kind": "day_offset", "n": 1}}),               # 没说修饰哪件事
])
def test_坏输出一律丢掉不抛(raw):
    assert _x(raw).extract("明天去") == []


def test_模型挂了不抛():
    assert TemporalExtractor(DeadAdapter()).extract("明天") == []


def test_没有utility模型就不做():
    assert TemporalExtractor(lambda: None).extract("明天") == []


def test_开关默认off(monkeypatch):
    monkeypatch.delenv("NOX_TEMPORAL", raising=False)
    assert mode() == "off"
    monkeypatch.setenv("NOX_TEMPORAL", "shadow")
    assert mode() == "shadow"


# ---------------------------------------------------------------- shadow 出口

def _event(intent: Intent, expression: str = "明天", event: str = "再去") -> TemporalEvent:
    return TemporalEvent(expression=expression, event=event, act="plan", intent=intent)


def _result(**over):
    base = dict(text="今天不去，明天再去",
                event=_event(Intent(kind="day_offset", n=1)),
                resolution=resolve(Intent(kind="day_offset", n=1), REF),
                reference_time=REF, applied=False,
                why_not_applied="shadow 模式")
    base.update(over)
    return TemporalResult(**base)


def test_没接下游必须说明为什么():
    """🔴 shadow 没有可观察的出口，它不是实验，是黑洞（糖糖 2026-09-14）。

    做成「缺一段就构造不出来」—— 靠自觉写日志的话，
    迟早有人为了图省事传个空字符串。
    """
    with pytest.raises(ValueError, match="少了这一段"):
        _result(why_not_applied=None)
    with pytest.raises(ValueError, match="少了这一段"):
        _result(why_not_applied="")


def test_接了下游要说明接给谁():
    with pytest.raises(ValueError, match="接给谁"):
        _result(applied=True, why_not_applied=None, applied_to=None)


def test_日志三段齐全(caplog):
    """① 模型说了什么 ② Resolver 算了什么（带锚点）③ 为什么没接。"""
    with caplog.at_level(logging.INFO):
        _result().log()
    t = caplog.text
    assert "day_offset" in t, "缺第①段：模型认成什么"
    assert "2026-09-15" in t, "缺第②段：算出来是哪天"
    assert REF.isoformat() in t, "缺锚点 —— 没有它没法复算"
    assert "shadow 模式" in t, "缺第③段：为什么没接"
    assert "「明天」→ 再去（plan）" in t, "缺第⓪段：这个时间修饰的是哪件事"


def test_日志真的是一行(caplog):
    """🔴 原来拼成三行，journald 按行切开，grep 只拿到第一行 ——
    09-22 复盘误判成「只记了未接」。①②③ 必须在同一条记录的同一行里。"""
    with caplog.at_level(logging.INFO):
        _result(text="今天不去，\n明天再去").log()
    [rec] = [r for r in caplog.records if "时间理解" in r.getMessage()]
    msg = rec.getMessage()
    assert "\n" not in msg, "日志被换行切开了"
    assert "①" in msg and "②" in msg and "③" in msg


def test_同一句的几个事件标得出是第几个(caplog):
    with caplog.at_level(logging.INFO):
        _result(index=2, of=2).log()
    assert "[2/2]" in caplog.text


def test_未解析的也要记全(caplog):
    """解析失败更该记 —— 那正是要观察的东西。"""
    bad = resolve(Intent(kind="weekday_bare", weekday=5), REF)
    with caplog.at_level(logging.INFO):
        _result(text="周五去", event=_event(Intent(kind="weekday_bare", weekday=5), "周五", "去"),
                resolution=bad, why_not_applied="时间没解析出来").log()
    assert "weekday_ambiguous" in caplog.text


# ---------------------------------------------------------------- Todo 决策

def _res(n: int) -> Resolution:
    return resolve(Intent(kind="day_offset", n=n), REF)


def test_推到明天用现有机制():
    d = decide(_res(1), todo_id="t1", today=TODAY)
    assert d.should_defer and d.until == date(2026, 9, 15)
    assert d.mechanism == MECH_FIRED


def test_推得更远需要bridge加字段():
    """现有的 `/api/todo/fired` 只表达得了「今天追过了，跨天失效」。

    这条测试的价值不是挡 bug，是**把缺口写下来** ——
    shadow 日志里这个比例会直接告诉我们 bridge 该不该加 deferred_until。
    """
    d = decide(_res(5), todo_id="t1", today=TODAY)
    assert d.should_defer and d.mechanism == MECH_NEEDS_FIELD


@pytest.mark.parametrize("n, why", [(0, "没有往后推"), (-1, "没有往后推")])
def test_没往后推的不算推迟(n, why):
    d = decide(_res(n), todo_id="t1", today=TODAY)
    assert not d.should_defer and why in d.why_not


def test_解析不了就不动():
    bad = resolve(Intent(kind="weekday_bare", weekday=5), REF)
    d = decide(bad, todo_id="t1", today=TODAY)
    assert not d.should_defer
    assert "weekday_ambiguous" in d.why_not


def test_精度对不上就不动():
    """「两个小时后」落在一个时刻上，待办是按天追的 —— 宁可不动，
    也不要把它硬取整成一天。"""
    at = resolve(Intent(kind="duration", hours=2, direction="future"), REF)
    d = decide(at, todo_id="t1", today=TODAY)
    assert not d.should_defer and "精度对不上" in d.why_not


def test_deadline也不动():
    dl = resolve(Intent(kind="deadline", before=Intent(kind="weekday_next", weekday=5)), REF)
    d = decide(dl, todo_id="t1", today=TODAY)
    assert not d.should_defer, "上界被当成了普通事件日期"


def test_没有在追的待办():
    d = decide(_res(1), todo_id=None, today=TODAY)
    assert not d.should_defer and "没有在追的待办" in d.why_not


def test_决策里根本表达不了标完成():
    """🔴 结构性：这个模块产出的东西**压根没有「完成」这个可能**。

    审计 F8 要拆的就是「没有完成」和「继续现在这个提醒策略」，
    所以推迟决策不该有任何触达状态的字段。
    """
    fields = set(DeferDecision.__dataclass_fields__)
    assert fields == {"should_defer", "todo_id", "until", "mechanism", "why_not"}
    assert not any(f in fields for f in ("status", "done", "completed", "complete"))


def test_不推迟必须说明为什么():
    with pytest.raises(ValueError, match="说明为什么"):
        DeferDecision(should_defer=False)


# ---------------------------------------------------------------- todo 归属状态
#
# 糖糖 2026-09-14 坚持要这个字段：几天后看到一堆 todo_id: null，
# 分不清是「确实没有在追的待办」「有但系统还不会匹配」还是「根本没试过」。


def test_todo_match_status_是封闭集合():
    from temporal.result import TODO_MATCH_STATUSES
    assert TODO_MATCH_STATUSES == {"not_attempted", "matched", "no_candidate", "ambiguous"}


def test_表外的归属状态会被拒():
    with pytest.raises(ValueError, match="表外的 todo_match_status"):
        _result(todo_match_status="maybe")


def test_没匹配上却带着todo_id是自相矛盾():
    """日志是这一版唯一的产出，不能让它自己打自己的脸。"""
    with pytest.raises(ValueError, match="却带着 todo_id"):
        _result(todo_match_status="not_attempted", todo_id="t1")


def test_日志里写明有没有试过匹配(caplog):
    """🔴 不是记一个含义不明的 null。"""
    with caplog.at_level(logging.INFO):
        _result().log()
    assert "not_attempted" in caplog.text, "没写明有没有试过匹配 —— 几天后这批数据就废了"


# ---------------------------------------------------------------- 真接线
#
# 上面那些测的是零件。这一节测**它真的被调用了** ——
# 今天已经栽过一次「写好了但没人调，而且一个错都不报」。


class _UtilityAdapter:
    """假的 utility 模型，固定回一个事件（「明天」去练腿）。"""

    def __init__(self, payload: str | None = None) -> None:
        payload = payload or _raw(_ev("明天", {"kind": "day_offset", "n": 1}))
        self.payload = payload
        self.calls = 0

    def complete(self, messages, tools, system=None, **kw):
        self.calls += 1
        return type("T", (), {"text": self.payload, "stop_reason": "end_turn", "error": None})()


class _ChatCore:
    """够 `create_app` 起来、且 `/chat` 能跑一轮的最小 core。

    不复用 test_api 的 FakeNox 是为了不在测试之间建耦合 ——
    那边改一下这边就红，而且红的原因看不出来。
    """

    class _Loop:
        tools: dict = {"recall_memory": None}

        def register(self, spec, handler):
            pass

    def __init__(self, tmp_path, utility) -> None:
        class _Cfg:
            db_path = str(tmp_path / "nox.db")
            history_limit = 40
            recent_window_tokens = 8000
            context_budget_tokens = 20000

            class primary:  # noqa: N801
                model = "fake-model"

        self.cfg = _Cfg()
        self.loop = self._Loop()
        self.bridge = None
        self.system_prompt = "（前缀）"
        self.current_session_id = None
        #: extraction 用的就是它
        self.router = type("R", (), {"light_adapter": utility})()

    def model_name(self, model=None):
        return model or "fake-model"

    def chat(self, text, history=None, **kw):
        from agent.llm import Message
        from agent.loop import LoopResult, Usage
        from router.intent import Decision, Intent as RIntent
        from router.router import RouteResult
        history = list(history or [])
        return RouteResult(
            LoopResult(outcome="answered", text="好", iterations=1,
                       usage=Usage(input_tokens=10, output_tokens=1, cache_read_tokens=0),
                       messages=[*history, Message(role="user", text=text),
                                 Message(role="assistant", text="好")],
                       attachments=[]),
            Decision(RIntent.SMALL_TALK, "测试"),
        )


def _client(tmp_path, monkeypatch, utility, *, now=None, clock=None):
    """真跑 create_app + TestClient，后台线程改成同步（否则断言撞时序）。"""
    from fastapi.testclient import TestClient

    import api.server as server
    from api.server import Store, create_app

    monkeypatch.setenv("NOX_TEMPORAL", "shadow")

    class _SyncThread:
        """同步跑，否则断言会撞时序。**args 必须转发** ——
        第一版漏了，压缩那条线程被调成了无参调用。"""

        def __init__(self, target=None, args=(), kwargs=None, **kw):
            self.target, self.args, self.kwargs = target, args, kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(server.threading, "Thread", _SyncThread)
    if clock is not None:
        #: 🔴 **会走的时钟**：第一次调（端点取 message_time）给一个值，
        #: 之后调（抽取时的"现在"）给另一个。
        #:
        #: 固定值的桩**测不出这条** —— 把 `ref = message_time` 改成
        #: `ref = now()` 两边返回同一个东西，变异照样绿。
        #: 我第一版就是这么写的，被变异测试抓到了。
        seq = iter(clock)
        last = [clock[-1]]

        def _tick():
            try:
                last[0] = next(seq)
            except StopIteration:
                pass
            return last[0]

        monkeypatch.setattr(server, "temporal_now", _tick)
    elif now is not None:
        monkeypatch.setattr(server, "temporal_now", lambda: now)

    core = _ChatCore(tmp_path, utility)
    return TestClient(create_app(core, Store(tmp_path / "s.db")))


def test_一轮对话真的会产出shadow日志(tmp_path, monkeypatch, caplog):
    """🔴 挡「写好了但没人调」—— 那种失败的形状是**什么都没发生**。"""
    utility = _UtilityAdapter()
    c = _client(tmp_path, monkeypatch, utility, now=REF)

    with caplog.at_level(logging.INFO):
        assert c.post("/chat", json={"text": "明天去练腿", "session_id": "s-1"}).status_code == 200

    assert utility.calls == 1, "extraction 根本没被调用"
    assert "时间理解" in caplog.text
    assert "day_offset" in caplog.text, "缺第①段"
    assert "2026-09-15" in caplog.text, "缺第②段 —— 锚点是 message_time 才算得出这天"
    assert "not_attempted" in caplog.text, "缺 todo 归属状态"


def test_一句两个事件真跑出两条日志(tmp_path, monkeypatch, caplog):
    """🔴 挡「抽出了两个，只记了一个」—— 第一版的形状就是一句一条。"""
    utility = _UtilityAdapter(_raw(
        _ev("今晚", {"kind": "day_offset", "n": 0, "slot": "evening"}, event="放好鸡蛋"),
        _ev("明天", {"kind": "day_offset", "n": 1}, event="蒸鸡蛋", act="request"),
    ))
    c = _client(tmp_path, monkeypatch, utility, now=REF)
    with caplog.at_level(logging.INFO):
        c.post("/chat", json={"text": "今晚放好鸡蛋，明天你给我蒸", "session_id": "s-1"})
    lines = [r.getMessage() for r in caplog.records if "时间理解" in r.getMessage()]
    assert len(lines) == 2, lines
    assert "[1/2]" in lines[0] and "放好鸡蛋" in lines[0]
    assert "[2/2]" in lines[1] and "蒸鸡蛋" in lines[1] and "2026-09-15" in lines[1], \
        "蒸蛋没落在明天"


def test_注入型会话不抽时间(tmp_path, monkeypatch, caplog):
    """🔴 主动开口的 prompt 里**就带着日期**（speaker 的 _clock_with_date）——
    抽它等于让模型去读我们自己写进去的时间，纯噪音。"""
    utility = _UtilityAdapter()
    c = _client(tmp_path, monkeypatch, utility, now=REF)

    from attention.appraisal_llm import NOT_HER_WORDS
    prefix = NOT_HER_WORDS if isinstance(NOT_HER_WORDS, str) else NOT_HER_WORDS[0]
    c.post("/chat", json={"text": "明天去练腿", "session_id": f"{prefix}x"})
    assert utility.calls == 0, "注入型会话被抽了时间"


@pytest.mark.parametrize("text", [
    #: 09-22 线上真实形状：她在看《摩登家庭》，他想插一句 —— 她根本没开口
    "【共影·主动】她在看一部本地的片子，刚到第 106 秒，这里换场了，你想说一句。"
    "【片名】摩登家庭S01E01 (AAC音轨).mkv 【当前画面】明天他们要去球场",
    #: 她暂停了问一句，但整段是 bridge 拼的场景描述 + 字幕
    "【共影】她暂停在 57.5 秒问你：【当前画面】这是一个户外，明年世界毁灭",
    #: 他主动开口的开场白，落在**主会话**里
    "（系统提示：不是她在跟你说话。现在是明天早上 08:55，你忽然想起她了。）",
])
def test_主会话里程序拼的消息不抽时间(tmp_path, monkeypatch, caplog, text):
    """🔴 2026-09-28 查 shadow 抓到的：共影塞在**主会话**里，会话前缀那道闸
    拦不住，Temporal 把「曼尼加油啊」那段当她的话抽了。"""
    utility = _UtilityAdapter()
    c = _client(tmp_path, monkeypatch, utility, now=REF)
    with caplog.at_level(logging.INFO):
        c.post("/chat", json={"text": text, "session_id": "s-1"})
    assert utility.calls == 0, "程序拼的消息被当成她的话抽了时间"
    assert "理解层不读" in caplog.text, "挡掉了但没留痕"


def test_程序拼的轮次整轮不做记忆抽取(tmp_path, monkeypatch):
    """记忆抽取读「她说 + 他回」。她那半是空的时候只剩他对片子的吐槽 ——
    抽出来的是**他看剧的感想**，不是关于她的记忆。纯图片轮照旧抽（那是她发的图）。"""
    dialogs = []

    class _OB:
        async def aextract_memory(self, dialog):
            dialogs.append(dialog)
            return type("R", (), {"ok": True, "text": "", "error": None})()

    monkeypatch.setattr(_ChatCore, "ob", _OB(), raising=False)
    #: 🔴 他那句要够长 —— 抽取对不满 30 字的回合本来就跳过，
    #: 拿「好」当回复的话，闸门拆了这条也照样绿（变异测试抓到的）
    real_chat = _ChatCore.chat

    def long_reply(self, text, history=None, **kw):
        r = real_chat(self, text, history, **kw)
        r.result.text = "场边喊得比场上还卖力，曼尼加油啊！这孩子被拒了都能秒消化"
        return r

    monkeypatch.setattr(_ChatCore, "chat", long_reply)
    c = _client(tmp_path, monkeypatch, _UtilityAdapter(), now=REF)

    c.post("/chat", json={"text": "【共影·主动】她在看片，刚到第 106 秒，这里换场了，你想说一句",
                          "session_id": "s-1"})
    assert dialogs == [], f"程序拼的轮次被抽了记忆：{dialogs}"

    c.post("/chat", json={"text": "今天和朋友去爬山了，腿好酸好酸，明天估计走不动路了", "session_id": "s-1"})
    assert len(dialogs) == 1 and "爬山" in dialogs[0], "她的原话没抽 —— 上面那条是空集通过"


def test_她的原话照样抽(tmp_path, monkeypatch):
    """对照组：闸门不许把她自己的话也挡了（开头像提示词的只认我们自己的标记）。"""
    utility = _UtilityAdapter()
    c = _client(tmp_path, monkeypatch, utility, now=REF)
    c.post("/chat", json={"text": "【转发】明天去练腿", "session_id": "s-1"})
    assert utility.calls == 1


def test_开关off时一次模型都不调(tmp_path, monkeypatch):
    utility = _UtilityAdapter()
    c = _client(tmp_path, monkeypatch, utility, now=REF)
    monkeypatch.setenv("NOX_TEMPORAL", "off")
    c.post("/chat", json={"text": "明天去练腿", "session_id": "s-1"})
    assert utility.calls == 0


def test_锚点用的是message_time不是现在(tmp_path, monkeypatch, caplog):
    """🔴 跨午夜那一轮：她 23:58 说「明天」，模型 10 秒后才回。

    用「现在」当锚点的话会解析成后天。判据是**算出来的日期**，
    不是「有没有传参」—— 传了但没用上的代码到处都是。
    """
    utility = _UtilityAdapter()
    #: 她 23:58 说，模型跑完已经是第二天 00:02 —— 真实场景就是这样
    said_at = datetime(2026, 9, 14, 23, 58, tzinfo=CST)
    ran_at = datetime(2026, 9, 15, 0, 2, tzinfo=CST)
    c = _client(tmp_path, monkeypatch, utility, clock=[said_at, ran_at])

    with caplog.at_level(logging.INFO):
        c.post("/chat", json={"text": "明天去练腿", "session_id": "s-1"})

    assert "2026-09-15" in caplog.text, "锚点不对：她说的「明天」应该是 09-15"
    assert "2026-09-16" not in caplog.text, "用了「现在」（09-15 00:02）当锚点，偏了一天"


def test_提示词认得中午():
    from temporal.extract import _PROMPT as SYSTEM_PROMPT
    assert "noon（中午）" in SYSTEM_PROMPT
    from temporal.intent import Intent
    Intent(kind="day_offset", n=0, slot="noon")   # 不许被校验拒掉


def test_on模式_她说明天练腿_真的去推迟那条待办(tmp_path, monkeypatch, caplog):
    """🔴 P4 接线（2026-10-06）：挡「写好了 apply 但 _temporal_async 没调」。
    判据是 bridge 真的收到了推迟请求、日志写着「已接」—— 不是函数存在。"""
    from types import SimpleNamespace

    class Bridge:
        def __init__(self):
            self.posts = []

        def get(self, path):
            return SimpleNamespace(ok=True, data={"items": [{"id": "t-leg", "text": "臀腿训练"}]})

        def post(self, path, body):
            self.posts.append((path, body))
            return SimpleNamespace(ok=True)

    class Utility(_UtilityAdapter):
        def complete(self, messages, tools, system=None, **kw):
            self.calls += 1
            text = self.payload if self.calls == 1 else "t-leg"      # 第二问：哪条待办
            return type("T", (), {"text": text, "stop_reason": "end_turn", "error": None})()

    bridge = Bridge()
    orig = _ChatCore.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        self.bridge = bridge
    monkeypatch.setattr(_ChatCore, "__init__", init)
    utility = Utility()
    c = _client(tmp_path, monkeypatch, utility, now=REF)
    monkeypatch.setenv("NOX_TEMPORAL", "on")
    with caplog.at_level(logging.INFO):
        c.post("/chat", json={"text": "明天去练腿", "session_id": "s-1"})
    assert ("/api/todo/defer", {"id": "t-leg", "until": "2026-09-15"}) in bridge.posts
    assert "已接" in caplog.text and "臀腿训练" in caplog.text


def test_shadow模式_不碰待办(tmp_path, monkeypatch):
    from types import SimpleNamespace
    touched = []
    orig = _ChatCore.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        self.bridge = SimpleNamespace(get=lambda p: touched.append(p), post=lambda p, b: touched.append(p))
    monkeypatch.setattr(_ChatCore, "__init__", init)
    c = _client(tmp_path, monkeypatch, _UtilityAdapter(), now=REF)
    c.post("/chat", json={"text": "明天去练腿", "session_id": "s-1"})
    assert not any("todo" in str(p) for p in touched)
