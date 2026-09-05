"""理解层：LLM Appraisal（2026-09-05）。

守的是 `appraisal_llm.py` 那四道闸。每一条都对应一种
「它编了但看起来很像真的」的失败方式 —— 这一层判错的代价是
**他念叨一件她根本没说过的事，而她无从知道他为什么这么想**。

⚠️ 不打网络。假 adapter 返回什么，就等于模型返回了什么。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Turn, Usage  # noqa: E402
from attention.appraisal import (  # noqa: E402
    ANCHOR_PREFIX, SUBJECT, TOPIC, Appraisal, RuleAppraiser, anchored,
)
from attention.appraisal_llm import (  # noqa: E402
    CEILING, MIN_CONFIDENCE, LLMAppraiser, mode,
)


class FakeAdapter:
    """把一段固定文本当成模型的输出。"""

    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict] = []

    def complete(self, messages, tools, **kw):
        self.calls.append({"messages": messages, **kw})
        return Turn(stop_reason="end_turn", text=self.text, usage=Usage())


class DeadAdapter:
    def complete(self, messages, tools, **kw):
        raise RuntimeError("模型挂了")


def _reply(**over) -> str:
    d = {
        "valence": "distress", "anchor": "毕设", "topic": "工作",
        "literal": "她说不想做了",
        "meaning": "不是字面上不想做，是觉得继续投入没有意义",
        "intensity": 0.55, "confidence": 0.85,
    }
    d.update(over)
    return json.dumps(d, ensure_ascii=False)


def _appraiser(text: str) -> LLMAppraiser:
    return LLMAppraiser(FakeAdapter(text))


# ---------------------------------------------------------------- 正常路径


def test_meaning_is_the_whole_point():
    """产出的必须是**推断**，不是把字面复述一遍。

    这条测试的存在本身就是这一层的定义：如果 meaning 丢了，
    那这一层就退化成了一个更贵的关键词匹配。
    """
    ap = _appraiser(_reply()).appraise_turn("我不想干了", "怎么了乖")
    assert ap is not None
    assert ap.meaning == "不是字面上不想做，是觉得继续投入没有意义"
    assert ap.literal == "她说不想做了"


def test_anchor_becomes_a_real_subject():
    """subject 不再是那个写死的常量 —— 这是「他分得清是哪件事」的前提。"""
    ap = _appraiser(_reply()).appraise_turn("我不想干了")
    assert ap.anchor == "毕设"
    assert ap.subject == f"{ANCHOR_PREFIX}毕设"


def test_the_whole_turn_is_sent_not_just_her_line():
    """🔴 后置的全部价值就在于能看到他回了什么。

    只喂她那句的话，这一层还不如放前面 —— 那样至少能影响当轮。
    """
    fake = FakeAdapter(_reply())
    LLMAppraiser(fake).appraise_turn("我不想干了", "是累了还是觉得没意思")
    body = fake.calls[0]["messages"][0].text
    assert "我不想干了" in body
    assert "是累了还是觉得没意思" in body


def test_known_anchors_are_offered_for_reuse():
    """「毕设」和「毕业设计」分成两条，他就会以为是两件事。"""
    fake = FakeAdapter(_reply())
    LLMAppraiser(fake).appraise_turn("又卡住了", "", ("毕设", "减肥"))
    assert "毕设" in fake.calls[0]["system"]
    assert "减肥" in fake.calls[0]["system"]


# ---------------------------------------------------------------- 四道闸


def test_low_confidence_is_dropped():
    """够不着门槛的**不留痕**。

    留下来的话，「攒够几次低置信度就算数」这种想法迟早有人去实现，
    而那正是造出似是而非的 concern 的方式。
    """
    ap = _appraiser(_reply(confidence=MIN_CONFIDENCE - 0.01)).appraise_turn("嗯")
    assert ap is None


def test_intensity_is_capped():
    """🔴 模型给 0.95 也只能拿到 0.62。

    不给这一层单独把一件事顶到很难被别的信号压过去的权力 ——
    它恰恰是最可能判错的一层。
    """
    ap = _appraiser(_reply(intensity=0.95)).appraise_turn("我快崩溃了")
    assert ap.intensity == pytest.approx(CEILING)


def test_none_is_the_normal_case():
    """她一天说几十上百句，绝大多数不是心事。"""
    assert _appraiser(_reply(valence="none")).appraise_turn("今天几号") is None


@pytest.mark.parametrize("bad", [
    "这不是 JSON",
    "",
    '{"valence": "怎么都行"}',          # 未知 valence
    '["distress"]',                      # 不是对象
    '{"valence": "distress", "confidence": "很确定"}',   # confidence 不是数
])
def test_garbage_never_reaches_the_registry(bad):
    """任何一步不对就返回 None。**绝不半信半疑地写一条进去。**"""
    assert _appraiser(bad).appraise_turn("我不想干了") is None


def test_code_fence_is_tolerated():
    """便宜模型很爱加 ```json 围栏，那不算它出错。"""
    ap = _appraiser(f"```json\n{_reply()}\n```").appraise_turn("我不想干了")
    assert ap is not None


def test_meaning_is_capped_at_the_source():
    """🔴 meaning 是模型唯一能自由发挥长度的字段，而它一路流进每轮的提示词。

    不封顶的话，一段 200 字的心理分析会把 800 字符预算顶掉别人 ——
    而且只留一条 warning，没人会看见。
    """
    from attention.appraisal_llm import MAX_MEANING
    ap = _appraiser(_reply(meaning="很" * 300, literal="啊" * 300)).appraise_turn("我不想干了")
    assert len(ap.meaning) == MAX_MEANING
    assert len(ap.literal) == MAX_MEANING


def test_model_failure_is_not_fatal():
    """这一层挂了的正确表现是「少一层理解」，不是整轮对话炸。"""
    assert LLMAppraiser(DeadAdapter()).appraise_turn("我不想干了") is None


def test_no_adapter_means_no_guessing():
    """便宜模型没配就什么都不做，**不许退回主模型**（每轮都跑，账单会很难看）。"""
    assert LLMAppraiser(lambda: None).appraise_turn("我不想干了") is None


# ---------------------------------------------------------------- 命名空间隔离


def test_unknown_topic_falls_back_instead_of_being_used_raw():
    """🔴 `care_weight()` 查不到的 topic 会静默拿到中性权重 0.5。

    表现成「关系加成没生效」，而且完全不报错 —— 所以不在表里的一律回退。
    """
    ap = _appraiser(_reply(topic="毕业设计相关的焦虑")).appraise_turn("我不想干了")
    assert ap.topic == TOPIC


def test_anchor_can_never_collide_with_a_sensor_subject():
    """🔴 这是 `appraisal.py:53` 那条血泪注释的结构化版本。

    理解层会**自己造 subject**，靠人盯着防撞名立刻不成立：
    它哪天推断出一个叫「糖糖的状态」的锚点（那个被 HRV 占着），
    两条来源就会写进同一条 Concern，strength 互相覆盖、evidence 混成一串。
    """
    ap = _appraiser(_reply(anchor="糖糖的状态")).appraise_turn("我不想干了")
    assert ap.subject.startswith(ANCHOR_PREFIX)
    assert ap.subject != "糖糖的状态"
    assert ap.subject != SUBJECT


def test_empty_anchor_falls_back_to_the_shared_subject():
    """认不出具体是什么事，就还是记在「她说的心情」这条大账上，
    而不是造一个叫「她说的：」的空锚点。"""
    assert anchored("") == SUBJECT
    assert anchored("  ") == SUBJECT


# ---------------------------------------------------------------- 契约不回归


def test_rule_appraiser_still_produces_the_old_shape():
    """原则 4：换实现的成本必须留在实现里，不许外溢成契约大改。

    规则版一行都没改过，新字段必须全是默认值。
    """
    ap = RuleAppraiser().appraise("好难受")
    assert ap is not None
    assert ap.subject == SUBJECT
    assert (ap.meaning, ap.literal, ap.anchor) == ("", "", "")
    assert ap.confidence == 1.0


def test_payload_roundtrip_keeps_the_understanding():
    """🔴 理解要跨线程搬运，靠的是这一趟摊平 + 还原。

    meaning 在这里掉了的话，Registry 里就只剩原话 ——
    表现成「他记得她说过什么，但不记得他理解成了什么」。
    """
    ap = _appraiser(_reply()).appraise_turn("我不想干了")
    back = Appraisal.from_payload(ap.to_payload())
    assert back == ap


def test_from_payload_tolerates_missing_fields():
    """输入可能来自上一个版本写下的事件，宁可少几个字段也不要抛。"""
    back = Appraisal.from_payload({"subject": "她说的：毕设", "valence": "distress"})
    assert back.subject == "她说的：毕设"
    assert back.meaning == ""


# ---------------------------------------------------------------- 开关


def test_default_is_off(monkeypatch):
    """⚠️ 默认关，和 NOX_CHAT_CONCERN（默认开）相反 ——
    这条线要花钱、而且会写 Registry，行为还没被观察过。"""
    monkeypatch.delenv("NOX_LLM_APPRAISAL", raising=False)
    assert mode() == "off"


@pytest.mark.parametrize("val,want", [
    ("shadow", "shadow"), ("1", "on"), ("on", "on"), ("ON", "on"),
    ("0", "off"), ("off", "off"), ("胡写的", "off"),
])
def test_switch_values(monkeypatch, val, want):
    """认不出来的值一律当关 —— 打错一个字母不该悄悄把它打开。"""
    monkeypatch.setenv("NOX_LLM_APPRAISAL", val)
    assert mode() == want
