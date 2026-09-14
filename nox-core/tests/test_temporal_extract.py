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
"""

from __future__ import annotations

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
    a = FakeAdapter('{"kind": "day_offset", "n": 1}')
    TemporalExtractor(a).extract("明天去练腿")
    import re
    assert not re.search(r"20\d{2}", a.seen_system[0])


def test_模型硬塞_resolved_字段会被拒():
    """🔴 `from_dict` 是模型输出进 Core 的唯一入口。

    糖糖 2026-09-14 判它「结构正确」：它把「模型可以自由生成 JSON」
    和「系统允许什么进入 Core」切开了。
    """
    assert _x('{"kind": "day_offset", "n": 1, "resolved_date": "2026-09-15"}').extract("明天") is None


def test_表外的kind会被拒():
    assert _x('{"kind": "next_month"}').extract("下个月") is None


# ---------------------------------------------------------------- 正常路径

@pytest.mark.parametrize("raw, expect", [
    ('{"kind": "day_offset", "n": 1}', Intent(kind="day_offset", n=1)),
    ('{"kind": "weekday_next", "weekday": 3}', Intent(kind="weekday_next", weekday=3)),
    ('{"kind": "weekday_bare", "weekday": 5}', Intent(kind="weekday_bare", weekday=5)),
    ('{"kind": "month_end"}', Intent(kind="month_end")),
    ('{"kind": "duration", "hours": 2}', Intent(kind="duration", hours=2)),
    ('{"kind": "day_offset", "n": -1, "slot": "evening"}',
     Intent(kind="day_offset", n=-1, slot="evening")),
])
def test_认出来的形状(raw, expect):
    assert _x(raw).extract("随便一句") == expect


def test_围栏要剥掉():
    """便宜模型很爱包 ```json，那不算它出错。"""
    assert _x('```json\n{"kind": "day_offset", "n": 1}\n```').extract("明天") == Intent(
        kind="day_offset", n=1)


# ---------------------------------------------------------------- 没说时间 ≠ 解析不了

def test_没有时间表达就不产出intent():
    """🔴 「她没说时间」根本不产出 intent；
    「说了但系统定不了是哪天」产出 intent + precision=none。
    两者是不同的系统事实，不能混（契约第五节 + weekday_bare 的由来）。
    """
    assert _x('{"kind": null}').extract("我有点累") is None


def test_没有时间表达不该产生警告(caplog):
    """🔴 绝大多数话都没有时间表达 —— 那是**常态不是错**。

    如果它走「不合契约」那条告警分支，shadow 日志会被寻常句子淹掉，
    真正该看的东西反而找不到。
    （变异测试抓到的：把 `kind is None` 那个分支去掉，输出仍然是 None，
      只有日志级别变了 —— 只断言返回值的话这条永远绿。）
    """
    with caplog.at_level(logging.DEBUG):
        assert _x('{"kind": null}').extract("我有点累") is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING],         "寻常句子产生了警告 —— shadow 日志会被淹"


def test_说了周几是产出intent而不是没有():
    i = _x('{"kind": "weekday_bare", "weekday": 3}').extract("周三去")
    assert i is not None, "「周三」被当成了『没说时间』—— 那两件事必须分开"
    assert not resolve(i, REF).ok


# ---------------------------------------------------------------- 坏输入

@pytest.mark.parametrize("raw", ["不是 JSON", "[1,2,3]", "", '{"kind": "day_offset"}'])
def test_坏输出一律丢掉不抛(raw):
    assert _x(raw).extract("明天") is None


def test_模型挂了不抛():
    assert TemporalExtractor(DeadAdapter()).extract("明天") is None


def test_没有utility模型就不做():
    assert TemporalExtractor(lambda: None).extract("明天") is None


def test_开关默认off(monkeypatch):
    monkeypatch.delenv("NOX_TEMPORAL", raising=False)
    assert mode() == "off"
    monkeypatch.setenv("NOX_TEMPORAL", "shadow")
    assert mode() == "shadow"


# ---------------------------------------------------------------- shadow 出口

def _result(**over):
    base = dict(text="今天不去，明天再去",
                intent=Intent(kind="day_offset", n=1),
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


def test_未解析的也要记全(caplog):
    """解析失败更该记 —— 那正是要观察的东西。"""
    bad = resolve(Intent(kind="weekday_bare", weekday=5), REF)
    with caplog.at_level(logging.INFO):
        _result(text="周五去", intent=Intent(kind="weekday_bare", weekday=5),
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
    at = resolve(Intent(kind="duration", hours=2), REF)
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
