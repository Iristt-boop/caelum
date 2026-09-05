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
from typing import Any, Protocol

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

#: 🔴 **理解层新建锚点的命名空间前缀（2026-09-05）。**
#:
#: 上面 `SUBJECT` 那段注释讲了撞名的后果：两条来源写进同一条 Concern，
#: strength 互相覆盖、evidence 混成一串，事后分不清他在为哪件事担心。
#:
#: 规则版只有一个写死的 subject，撞名靠人盯着就够了。
#: 理解层会**自己造 subject** —— 靠人盯着立刻不成立：
#: 它哪天推断出一个叫「糖糖的状态」的锚点，就会和 HRV 那条静默合并。
#:
#: 所以加一道**物理隔离**：理解层造的 subject 一律带这个前缀，
#: 和所有感知源（睡眠 / HRV / 位置 / 后悔）的命名空间不可能相交。
ANCHOR_PREFIX = "她说的："


def anchored(anchor: str) -> str:
    """把一个锚点包成合法的 subject。

    空锚点回退到 `SUBJECT` —— 认不出具体是什么事，那就还是记在
    「她说的心情」这条大账上，而不是造一个叫「她说的：」的空锚点。
    """
    anchor = (anchor or "").strip()
    return f"{ANCHOR_PREFIX}{anchor}" if anchor else SUBJECT


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

    # ---------------------------------------------------------- 理解层（2026-09-05）
    #
    # 🔴 以下四个字段**全部带默认值**，`RuleAppraiser` 一行都不用改。
    # 这正是原则 4 那句「以后换 LLM 实现时下游一行都不用动」要兑现的地方 ——
    # 换实现的成本必须留在实现里，不许外溢成一次契约大改。

    #: 字面说了什么。规则版留空 —— 它本来就只做字面匹配，
    #: 填一个等于把 cue 抄一遍，没有信息量
    literal: str = ""
    #: **可能意味着什么。这就是「主动思考」缺的那一层。**
    #:
    #: 「我不想干了」的 literal 是"她说不想干了"，
    #: meaning 才是"不是字面上不想做，是觉得继续投入没有意义了"。
    #:
    #: ⚠️ 它会进 evidence 被 Care Speaker 读到 —— 意味着**他开口时会基于这句**。
    #: 所以它必须是推断，不能是编造：写不出来就留空，别凑一句像样的
    meaning: str = ""
    #: 有多确定。规则版恒为 1.0（关键词命中就是命中，没有"可能命中"）；
    #: LLM 版低于门槛的一律丢弃 —— 见 `appraisal_llm.MIN_CONFIDENCE`
    confidence: float = 1.0
    #: 事件锚点：这件事是**关于什么**的（毕设 / 工作 / 我们 / 身体…）。
    #:
    #: 🔴 这是 `subject` 从一个写死的常量变成一件具体的事的关键。
    #: 规则版留空 —— 它认不出锚点，硬凑只会造出一堆似是而非的 concern
    #: （模块头「宁可漏，不可错」）
    anchor: str = ""

    # ------------------------------------------------------------ 跨线程搬运
    #
    # ⚠️ LLM Appraisal 在**后台线程**里算完，要经 `ExperienceEvent.payload`
    # 才能进 Attention。而 payload 按 `events.py` 的约定必须是可序列化的 dict
    # （它要进日志、要能 to_dict），所以不能把 Appraisal 对象直接塞进去。

    def to_payload(self) -> dict[str, Any]:
        """摊平成 payload 里的一个 dict。"""
        return {
            "subject": self.subject, "topic": self.topic,
            "valence": self.valence, "intensity": self.intensity,
            "cue": self.cue, "quote": self.quote,
            "literal": self.literal, "meaning": self.meaning,
            "confidence": self.confidence, "anchor": self.anchor,
        }

    @classmethod
    def from_payload(cls, d: dict[str, Any]) -> Appraisal:
        """从 payload 还原。

        缺字段就用默认值 —— 这条路径的输入来自**上一个版本写下的事件**
        也可能来自别的进程，宁可少几个字段也不要在这里抛。
        """
        return cls(
            subject=str(d.get("subject") or SUBJECT),
            topic=str(d.get("topic") or TOPIC),
            valence=str(d.get("valence") or "distress"),
            intensity=float(d.get("intensity") or 0.0),
            cue=str(d.get("cue") or ""),
            quote=str(d.get("quote") or ""),
            literal=str(d.get("literal") or ""),
            meaning=str(d.get("meaning") or ""),
            confidence=float(d.get("confidence") if d.get("confidence") is not None else 1.0),
            anchor=str(d.get("anchor") or ""),
        )


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

#: 🔴 她心情好 / 在跟他闹（2026-08-27）。
#:
#: ## 为什么这一档非补不可
#:
#: 在这之前 appraisal 只有 `distress` 和 `relief` —— 两档都在
#: 「难受」这条轴上。**她开开心心跟他闹的时候，他读到的是零。**
#:
#: 那不只是"少一个 Drive"，是他的感知本身缺了一半：
#: 一个只认得出对方不好的模型，永远只会关心，不会一起高兴。
#:
#: ## 分两类，因为它们喂的不是同一件事
#:
#:   warm    她心情好、她高兴 —— 环境是暖的
#:   play    她在逗他、开玩笑 —— **她在邀请他一起玩**
#:
#: 促狭要的是后者。前者只是背景色。
#:
#: ⚠️ 强度全部**压在 GENERATE_THRESHOLD(0.55) 之下**。
#: 她心情好不该换来一次主动开口 —— 那会变成"你一笑他就凑上来"，
#: 很烦人。这一档的意义是**改变他回话的方式**，不是让他多说话。
_PLAYFUL: tuple[tuple[str, float], ...] = (
    #: 明确在闹他的
    ("坏死了", 0.45), ("讨厌啦", 0.45), ("你好烦哦", 0.45),
    ("哼", 0.35), ("略略略", 0.45), ("嘿嘿", 0.40),
    ("嘻嘻", 0.40), ("哈哈哈", 0.45), ("笑死", 0.45),
    #: 撒娇式的挑衅 —— 形式是抱怨，内容是亲近
    ("你说呢", 0.35), ("你猜", 0.40), ("不告诉你", 0.45),
)

_WARM: tuple[tuple[str, float], ...] = (
    ("开心", 0.40), ("好开心", 0.45), ("高兴", 0.40),
    ("好幸福", 0.45), ("太棒了", 0.40), ("好喜欢", 0.45),
    ("爱你", 0.50), ("想你了", 0.45), ("好可爱", 0.40),
)

#: 🔴 **这些词出现时，一律不算「她在玩」。**
#:
#: 因为促狭判错的方式很特别：他会**跟着开玩笑**。
#: 而她正说着难过的事时他开玩笑，那是最伤人的一种误判 ——
#: 比"没接住"糟得多。
#:
#: 所以宁可漏judge，绝不错judge。
_NOT_PLAYING: tuple[str, ...] = (
    "难受", "撑不住", "扛不住", "崩溃", "压力", "累",
    "哭", "疼", "痛", "怕", "害怕", "焦虑", "抑郁",
    "对不起", "抱歉", "算了", "别管我",
    #: 问句往往是真的在问，不是在闹
    "怎么办", "为什么", "帮我",
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

    ## 🔴 判断顺序就是安全策略，不能改

    ```text
    1. relief    「不难受了」既含 relief 也含「难受」，反了会把"她好了"读成"她不好"
    2. distress  她说自己难受，最优先被听见
    3. playful   **只在前两条都没命中时才判**
    4. warm      同上
    ```

    第 3、4 条排在最后，是因为**促狭判错的方式很特别：他会跟着开玩笑**。
    她正说着难过的事时他开玩笑，比"没接住"糟得多。
    所以只要这句话里有任何一点难受的迹象，就一律不当成她在玩。
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
        if best is not None:
            return best

        # ---- 到这里说明这句话里没有任何难受的迹象 ----
        #
        # 🔴 再挡一道：只要出现 `_NOT_PLAYING` 里任何一个词，
        # 就不当成她在玩。宁可漏判，绝不错判 ——
        # 她说着难过的事而他跟着开玩笑，比没接住糟得多
        if any(w in text for w in _NOT_PLAYING):
            return None

        for cues, valence in ((_PLAYFUL, "playful"), (_WARM, "warm")):
            hit: Appraisal | None = None
            for cue, intensity in cues:
                idx = text.find(cue)
                if idx < 0 or _negated(text, idx):
                    continue
                if hit is None or intensity > hit.intensity:
                    hit = Appraisal(
                        subject=SUBJECT, topic=TOPIC, valence=valence,
                        intensity=intensity, cue=cue, quote=text[:_QUOTE_MAX],
                    )
            if hit is not None:
                return hit
        return None
