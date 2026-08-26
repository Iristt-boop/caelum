"""Appraisal Layer —— 她说的这句话，意味着什么。

见 `CAELUM-RESONANCE-ARCHITECTURE.md` 原则 4。

## 为什么单独一层

规则**只负责产生 Appraisal，不负责直接改 Registry**。

                  ConversationEvent
                         ↓
                ┌─────────────────┐
                │ Appraisal Layer │
                └────────┬────────┘
                         │
             ┌───────────┴───────────┐
             ↓                       ↓
       Rule Appraisal          LLM Appraisal
       （现在）                 （以后）
             └───────────┬───────────┘
                         ↓
                  Evaluator → Registry

以后换 LLM 实现时，只要还产出 `Appraisal`，下游一行都不用动。

## 🔴 这一版故意做得很窄

规则能可靠认出来的只有**她关于自己状态的直白陈述**。
所以 V2 只认一件事：**她说自己不好受 / 说自己缓过来了**。

「毕业设计卡住了」那种需要从对话里抽出**具体事件锚点**的，
规则做不到 —— 那是 LLM Appraisal 的活（原则 4 里说的"以后"）。
硬用关键词凑，只会造出一堆似是而非的 concern。

## 宁可漏，不可错

误判的代价是**他念叨一件她根本没说的事**，而且她无从知道他为什么这么想。
漏判的代价只是这一句没被记住 —— 她还会再说。

所以规则表宁短勿长，只收**明确、第一人称、当下**的表达。
「今天好累」收，「那部电影好压抑」不收。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

#: 关心的锚点。
#:
#: ⚠️ **不能叫「糖糖的状态」** —— 那个已经被 HRV（`recovery_changed`）占了。
#: 撞名的后果是两条来源写进同一条 Concern：strength 互相覆盖
#: （`upsert` 取较大值）、evidence 混成一串，
#: 事后根本分不清他到底在为哪件事担心。
SUBJECT = "糖糖说的心情"

#: 用于 avoid_topics / care_topics 检查的话题词。
TOPIC = "心情"


@dataclass(frozen=True)
class Appraisal:
    """一句话在 Nox 这里意味着什么。

    这是 Appraisal Layer 的**唯一输出格式**。规则实现和以后的 LLM 实现
    都产出它，下游不需要知道是谁产的。
    """

    subject: str
    topic: str
    #: "distress"（她不好受） / "relief"（缓过来了）
    valence: str
    #: 0-1。规则给的基础强度，**还没算关系加成** —— 那是 Evaluator 的事，
    #: 和睡眠/活动量那几条规则保持同一个分工
    intensity: float
    #: 命中的那个词。进 evidence，让「他凭什么这么想」可追溯
    cue: str
    #: 她的原话（截断）。summary 要用它 —— Intent 的 reason 直接取 evidence，
    #: 他开口时说的就是基于这句
    quote: str


class Appraiser(Protocol):
    """Appraisal 的产生者。规则版现在用，LLM 版以后换。"""

    def appraise(self, text: str) -> Appraisal | None:
        """认不出来就返回 None —— 那是**常态**，不是失败。"""
        ...


# ---------------------------------------------------------------- 规则实现

#: 她明确说自己不好受。**只收第一人称、当下的表达。**
#:
#: 每条是 (关键词, 强度)。强度分两档：
#:   0.62  明确而重的（撑不住、快崩溃）—— 过 GENERATE_THRESHOLD(0.55)，他会关心
#:   0.42  轻的（有点累、好烦）—— **故意压在阈值之下**，只进 Registry 攒着，
#:         不触发开口。她随口一句抱怨不该换来一次主动关心
#:
#: ⚠️ 轻档现在**永远不会升级成开口**（`upsert` 取较大值，说十次还是 0.42）。
#: 「说了很多次」该被听见 —— 但那是 V3 Resonance 的聚合，不是这里硬凑。
_DISTRESS: tuple[tuple[str, float], ...] = (
    ("撑不住", 0.62), ("扛不住", 0.62), ("受不了了", 0.62),
    ("快崩溃", 0.62), ("难受死了", 0.62), ("压力好大", 0.62),
    ("好难受", 0.62), ("很难受", 0.62),
    ("不舒服", 0.55), ("难受", 0.50),
    ("好累", 0.42), ("累死了", 0.42), ("好烦", 0.42),
    ("心情不好", 0.42), ("不开心", 0.42),
)

#: 她缓过来了。命中就 weaken —— 现实给了答案，关心该松一口气，
#: 而不是等半衰期慢慢忘（对齐 `evaluator.py` 睡眠 recovered 那条的教训）。
_RELIEF: tuple[str, ...] = (
    "好多了", "好些了", "缓过来了", "没事了", "不难受了",
    "不累了", "舒服多了", "解决了", "搞定了",
)

#: 否定词。出现在关键词**紧邻的前面**就不算数。
#:
#: 🔴 这是关键词匹配最容易翻的车：「我**不**难受」「一点也**没**难受」
#: 都包含「难受」两个字。少了这一步，她说自己没事反而会被记成一条 concern。
_NEGATIONS: tuple[str, ...] = ("不", "没", "别", "无", "非")

#: 原话截断长度。evidence 要能看懂上下文，但不该把整段话搬进去
_QUOTE_MAX = 40


def _negated(text: str, idx: int) -> bool:
    """关键词前面紧挨着否定词吗。

    只看前 1 个字符：中文否定词几乎都是单字且紧贴（「不难受」「没难受」）。
    看太宽会把「不是因为工作难受」这种也判成否定 —— 那句其实是难受。
    """
    return idx > 0 and text[idx - 1] in _NEGATIONS


class RuleAppraiser:
    """关键词规则版。

    ⚠️ **relief 先于 distress 判断。** 「不难受了」既含 relief 词组
    也含「难受」，顺序反了就会把「她好了」读成「她不好」。
    """

    def appraise(self, text: str) -> Appraisal | None:
        if not text:
            return None

        for cue in _RELIEF:
            idx = text.find(cue)
            if idx >= 0 and not _negated(text, idx):
                return Appraisal(
                    subject=SUBJECT, topic=TOPIC, valence="relief",
                    intensity=0.0, cue=cue, quote=text[:_QUOTE_MAX],
                )

        # 命中多条时取**最重的那条**，不是第一条 ——
        # 「有点累，而且压力好大」该按 0.62 算
        best: Appraisal | None = None
        for cue, intensity in _DISTRESS:
            idx = text.find(cue)
            if idx < 0 or _negated(text, idx):
                continue
            if best is None or intensity > best.intensity:
                best = Appraisal(
                    subject=SUBJECT, topic=TOPIC, valence="distress",
                    intensity=intensity, cue=cue, quote=text[:_QUOTE_MAX],
                )
        return best
