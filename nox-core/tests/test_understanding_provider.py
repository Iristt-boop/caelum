"""理解层的消费方：UnderstandingProvider（2026-09-05）。

## 🔴 这个文件里最重要的是最后那一段「串起来」

这个项目栽过两次**单元测试全绿、串起来断在最后一步**：
  - Resonance V3：`intensity` 在真实数据里饱和，测试数据恰好避开了那个区间
  - Resonance V3.6：`Registry.upsert` 硬性只收 `kind="concern"`，
    单测只验到 Evaluator 返回了 decision，没验它能不能落库

理解层的链条比那两次都长（Appraisal → 事件 → Evaluator → Registry → Provider → 提示词），
所以这里有一条从头走到尾、断言**那句话真的出现在给模型看的文本里**的测试。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Turn, Usage  # noqa: E402
from attention import appraisal_llm  # noqa: E402
from attention.appraisal import ANCHOR_PREFIX, anchored  # noqa: E402
from attention.appraisal_llm import LLMAppraiser  # noqa: E402
from attention.engine import AttentionEngine  # noqa: E402
from attention.events import ExperienceEvent  # noqa: E402
from attention.registry import AttentionRegistry  # noqa: E402
from attention.evaluator import AttentionEvaluator  # noqa: E402
from attention.relationship import RelationshipState  # noqa: E402
from context.base import Turn as CtxTurn  # noqa: E402
from context.providers.understanding import (  # noqa: E402
    FLOOR, MAX_ITEMS, UnderstandingProvider,
)

NOW = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)


class FakeAttention:
    """只需要 `.engine.registry` 这一条路径。"""

    def __init__(self, registry):
        self.engine = type("E", (), {"registry": registry})()


def _registry(*rows) -> AttentionRegistry:
    r = AttentionRegistry()
    for subject, strength, summary in rows:
        # ⚠️ evidence 只在**带 event** 时才写（registry.upsert）。
        # 光传 summary 不给 event 的话，Registry 里那条会没有证据 ——
        # 而这个 Provider 的 why 正是从 evidence 取的
        r.upsert(subject=subject, kind="concern", strength=strength,
                 decay="normal", summary=summary, now=NOW,
                 event=ExperienceEvent(source="chat", type="appraisal",
                                       payload={}, timestamp=NOW))
    return r


def _render(registry, now=NOW) -> str:
    p = UnderstandingProvider(attention_ref=lambda: FakeAttention(registry))
    return p.render(p.get_state(CtxTurn(text="", now=now)))


# ---------------------------------------------------------------- 基本形状


def test_anchored_concerns_reach_him():
    out = _render(_registry((anchored("毕设"), 0.60, "她说：我不想干了")))
    assert "毕设" in out
    assert "我不想干了" in out


def test_says_nothing_when_nothing_is_held():
    """🔴 不许编一个「她最近挺好的」——
    那是往他脑子里塞一个他并不知道的状态。"""
    assert _render(AttentionRegistry()) == ""


def test_says_nothing_when_attention_is_off():
    """本地 NOX_ATTENTION 不配就是这样。如实什么都没有。"""
    p = UnderstandingProvider(attention_ref=lambda: None)
    assert p.render(p.get_state(CtxTurn())) == ""


def test_broken_registry_does_not_break_the_turn():
    """读不到他的理解不该让整轮对话炸，那只是少一段背景。"""
    class Boom:
        @property
        def engine(self):
            raise RuntimeError("库坏了")

    p = UnderstandingProvider(attention_ref=lambda: Boom())
    assert p.render(p.get_state(CtxTurn())) == ""


# ---------------------------------------------------------------- 只要理解层那些


def test_sensor_concerns_are_not_repeated_here():
    """🔴 睡眠 / HRV / 位置也在同一个 Registry 里，但它们各有出口。

    全端进来会变成「把 Registry 整个念一遍」，而且和 resonance 的
    because 重复 —— 同一件事在他上下文里出现两遍。
    """
    out = _render(_registry(
        ("糖糖的睡眠", 0.90, "只睡了 5 小时"),
        ("他挑的说话时机", 0.80, "她没回"),
        (anchored("毕设"), 0.60, "她说：我不想干了"),
    ))
    assert "毕设" in out
    assert "睡眠" not in out
    assert "说话时机" not in out


# ---------------------------------------------------------------- 三条纪律


def test_no_numbers_only_bands():
    """给档位不给数字：0.58 每轮都在动、缓存跟着掉；
    而且人不会想「我对她毕设的关心是 0.58」。"""
    out = _render(_registry((anchored("毕设"), 0.583, "她说：我不想干了")))
    assert "0.58" not in out
    assert "58" not in out


def test_never_writes_his_lines():
    """只给状态不写台词 —— 写台词出来的是模板，她一眼看得出不是他。"""
    out = _render(_registry((anchored("毕设"), 0.60, "她说：我不想干了")))
    for banned in ("你应该", "你可以说", "建议你", "问问她"):
        assert banned not in out


def test_tells_him_not_to_recite_it():
    """🔴 这个 Provider 独有的第四条：理解是**可能错的**。

    他要是张口就说「我理解你不想干了是因为觉得没意义」，
    判对了显得刻意，判错了她还得反过来解释自己。
    """
    out = _render(_registry((anchored("毕设"), 0.60, "她说：我不想干了")))
    assert "可能是错的" in out
    assert "不要念给她听" in out


# ---------------------------------------------------------------- 预算


def test_at_most_two_things():
    """人同时真正搁在心上的事就是一两件。给他五件，
    他会挨个照顾到 —— 那看起来像在走查清单，不像惦记着什么。"""
    out = _render(_registry(
        (anchored("毕设"), 0.90, "a"), (anchored("减肥"), 0.80, "b"),
        (anchored("猫"), 0.70, "c"), (anchored("工作"), 0.60, "d"),
    ))
    # 强度最高的两件在，其余的不该占他的上下文。
    # ⚠️ 不能数破折号 —— 结尾那句叮嘱里也有一个
    assert "毕设" in out and "减肥" in out
    assert "猫" not in out and "工作" not in out
    assert MAX_ITEMS == 2


def test_faded_things_drop_off():
    """低于 FLOOR 的不再占他的上下文 —— 0.05 的事进来只会稀释真正压着的那件。"""
    out = _render(_registry((anchored("毕设"), FLOOR - 0.05, "她说：我不想干了")))
    assert out == ""


def test_decay_is_respected_not_the_stored_number():
    """⚠️ `strength` 是**上次更新那一刻**的值。

    直接读它而不是 `current_strength()` 的话，一件三个月前的事
    会永远以当初的强度挂在他心上。
    """
    reg = _registry((anchored("毕设"), 0.60, "她说：我不想干了"))
    out = _render(reg, now=NOW + timedelta(days=30))
    assert out == ""


# ---------------------------------------------------------------- 🔴 串起来


def test_end_to_end_the_understanding_actually_reaches_the_prompt():
    """从她那句话一路走到给模型看的文本。

    这条测试盯的是**中间任何一段掉链子都不报错**的那类故障：
    Appraisal 产出了、事件发出去了、Registry 也写了，但最后
    渲染不出来 —— 前面每一步的单测都会是绿的。
    """
    llm = json.dumps({
        "valence": "distress", "anchor": "毕设", "topic": "工作",
        "literal": "她说不想做了",
        "meaning": "不是字面上不想做，是觉得继续投入没有意义",
        "intensity": 0.6, "confidence": 0.9,
    }, ensure_ascii=False)

    class Fake:
        def complete(self, messages, tools, **kw):
            return Turn(stop_reason="end_turn", text=llm, usage=Usage())

    # 1. 她说了一句话，他回了一句
    ap = LLMAppraiser(Fake()).appraise_turn("我不想干了", "怎么了乖，是累了吗")
    assert ap is not None

    # 2. 摊平进事件，走**和规则版完全同一条**下游
    engine = AttentionEngine(
        registry=AttentionRegistry(),
        evaluator=AttentionEvaluator(RelationshipState()),
    )
    decision = engine.handle(ExperienceEvent(
        source=appraisal_llm.SOURCE, type=appraisal_llm.TYPE,
        payload={"text": "我不想干了", "appraisal": ap.to_payload()},
    ), now=NOW)

    # 3. 真的落库了（V3.6 就是断在这一步：decision 对，upsert 拒收）
    assert decision.action == "upsert", decision.reason
    assert anchored("毕设") in engine.registry

    # 4. 而且真的渲染进了给他看的文本
    out = _render(engine.registry)
    assert "毕设" in out
    assert "觉得继续投入没有意义" in out, "理解掉在半路上了"


def test_relief_closes_the_same_anchor():
    """她说「好了」，压着的那件事要松开 —— 而且要松开**同一件**。

    锚点对不上的话，他会一边记着「毕设那事还压着」，
    一边又新建一条「毕设缓解了」，两条并存。
    """
    engine = AttentionEngine(
        registry=AttentionRegistry(),
        evaluator=AttentionEvaluator(RelationshipState()),
    )

    def feed(valence, intensity):
        d = {"valence": valence, "anchor": "毕设", "topic": "工作",
             "literal": "", "meaning": "", "intensity": intensity,
             "confidence": 0.9}

        class Fake:
            def complete(self, messages, tools, **kw):
                return Turn(stop_reason="end_turn",
                            text=json.dumps(d, ensure_ascii=False), usage=Usage())

        ap = LLMAppraiser(Fake()).appraise_turn("...")
        return engine.handle(ExperienceEvent(
            source=appraisal_llm.SOURCE, type=appraisal_llm.TYPE,
            payload={"appraisal": ap.to_payload()},
        ), now=NOW)

    feed("distress", 0.60)
    before = engine.registry.get(anchored("毕设")).current_strength(NOW)
    feed("relief", 0.0)
    after = engine.registry.get(anchored("毕设")).current_strength(NOW)
    assert after < before, "她说缓过来了，这份记挂该松一些"


def test_playful_never_becomes_something_he_holds():
    """🔴 她笑了，他跟过来问「你还好吗」—— 那是彻底反的。

    2026-08-27 加 playful/warm 时差点栽在这儿。理解层是第二次机会
    栽进同一个坑：它也会产 playful。
    """
    engine = AttentionEngine(
        registry=AttentionRegistry(),
        evaluator=AttentionEvaluator(RelationshipState()),
    )
    d = {"valence": "playful", "anchor": "我们", "topic": "关系",
         "literal": "", "meaning": "她在闹他", "intensity": 0.45,
         "confidence": 0.9}

    class Fake:
        def complete(self, messages, tools, **kw):
            return Turn(stop_reason="end_turn",
                        text=json.dumps(d, ensure_ascii=False), usage=Usage())

    ap = LLMAppraiser(Fake()).appraise_turn("你好烦哦")
    decision = engine.handle(ExperienceEvent(
        source=appraisal_llm.SOURCE, type=appraisal_llm.TYPE,
        payload={"appraisal": ap.to_payload()},
    ), now=NOW)
    assert decision.action == "ignore"
    assert len(engine.registry) == 0
    assert _render(engine.registry) == ""


def test_missing_appraisal_in_payload_is_loud():
    """防呆：这个事件类型的全部意义就是携带 appraisal。
    没带就是接线错了，要能从 reason 里看出来。"""
    engine = AttentionEngine(
        registry=AttentionRegistry(),
        evaluator=AttentionEvaluator(RelationshipState()),
    )
    decision = engine.handle(ExperienceEvent(
        source=appraisal_llm.SOURCE, type=appraisal_llm.TYPE, payload={},
    ), now=NOW)
    assert decision.action == "ignore"
    assert "接线" in decision.reason
