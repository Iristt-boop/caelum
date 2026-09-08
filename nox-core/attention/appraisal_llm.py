"""LLM Appraisal —— 她这句话**可能意味着什么**。

`appraisal.py` 模块头那张图里写着「LLM Appraisal（以后）」，这里就是那个以后。
糖糖 2026-09-05 的原话：

> 现在的 Nox 有主动行为（proactive behavior），但缺少主动思考（proactive cognition）。
> 它能做到「我注意到你今天睡少了」，但做不到「我说『不想干了』，这句话可能不是
> 字面上的不想做，而是累了、失望了，或者觉得继续投入没有意义」。

## 🔴 缺的不是「当轮的理解力」，是「理解留不下来」

这一点想清楚了才知道为什么这个文件是**后置**的。

主模型是前沿模型，她说「我不想干了」，他当轮读得懂那不是字面意思，回话也接得住。
真正的问题是：**回完这一句，那份理解就蒸发了**。第二天她再提，他不知道她为什么想放弃；
一周后 Care 开口，他只说得出「你最近心情不好」，说不出「毕设那事」。

所以这一层干的不是"替他理解"，是**把已经发生的理解抽出来、锚定、存住**。

## 为什么放在回应之后（糖糖 2026-09-05 拍的）

| | 前置（回应前推断） | 后置（这里） |
|---|---|---|
| 延迟 | 每轮 +1~2s，闲聊 2.7s 变 4s | **0** |
| 看得到什么 | 只有她这一句 | **她说的 + 他回的 + 工具查到的** |
| 影响哪一轮 | 当轮 | 下一轮起（多轮对话里第二句就活了） |

后置多知道的那些正是判断的依据 —— 他回了什么、查到了什么，
往往比她那一句话更能说明这件事到底是什么。

## 🔴 「宁可漏，不可错」在这里更重要，不是更宽松

`appraisal.py` 模块头那条纪律（误判 = 他念叨一件她根本没说的事）对规则版成立，
对这一层**成立得更厉害**：规则版判错顶多是词表选糟了，一眼看得出；
LLM 判错会造出一个**读起来很合理但根本不存在**的心事，而且它会进 evidence、
被 Care Speaker 当成开口的理由说出来 —— 她无从知道他为什么这么想。

所以这里有四道闸，一道比一道硬：

    1. shadow 模式      默认只记日志不写库，先看它判得准不准
    2. confidence 门槛  低于 0.6 直接丢
    3. 强度天花板 0.62  和规则重档持平，不给它单独把一件事顶到开口阈值的权力
    4. 锚点归一         优先复用已有锚点，避免每轮造一个新 subject 把 Registry 刷爆

## 它不许自己开口（R1）

这一层**只产 Appraisal**，和规则版走完全同一条下游：
Evaluator → Registry → Intent → Scheduler → Care Orchestrator。
CAELUM-MAP 的 R1 写死了主动开口唯一出口，这里不开例外。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable

from attention.appraisal import Appraisal, SUBJECT, TOPIC, anchored

logger = logging.getLogger(__name__)

#: 事件源/类型。`source` 仍是 chat（她说的话），但 type 和规则版分开 ——
#: `events.py` 那条约定：**加新的比改旧的安全**，改 type 会让匹配它的规则静默失效
SOURCE = "chat"
TYPE = "appraisal"

#: 低于这个置信度一律丢。
#:
#: 0.6 不是拍的：低于它的那些，人读一遍也会觉得"这是硬解读出来的"。
#: 宁可这句话什么都没留下 —— 她还会再说（模块头那条）
MIN_CONFIDENCE = 0.6

#: 🔴 强度天花板。**和规则版的重档（0.62）持平，不许更高。**
#:
#: `GENERATE_THRESHOLD` 是 0.55，0.62 已经够得着主动开口。
#: 给它更高的上限等于让一次 LLM 判断就能把一件事顶到很难被别的信号压过去，
#: 而这一层恰恰是最可能判错的一层。
CEILING = 0.62

#: 锚点最长几个字。太长的锚点说明它在描述一件事而不是命名一件事，
#: 而 subject 是**索引键** —— 描述性的 key 永远匹配不上第二次
MAX_ANCHOR = 12

#: 喂给模型的已有锚点上限。给太多它会倾向于硬套一个
MAX_KNOWN_ANCHORS = 8

#: meaning / literal 各自最长多少字。
#:
#: 🔴 这是**模型唯一能自由发挥长度**的字段，而它会一路流进
#: evidence → UnderstandingProvider → 每轮的 dynamic 块。
#: 不封顶的话，它哪天写了一段 200 字的心理分析，就会把 800 字符预算
#: 顶掉别人（`context/registry.py` 按顺序丢）——而且只留一条 warning。
#:
#: 50 够说清一句「不是字面上不想做，是觉得继续投入没有意义」
MAX_MEANING = 50

#: 只认这几种 valence，和规则版同一套词表。
#: "none" = 这句话没什么要记的，**那是常态**
_VALENCES = frozenset({"distress", "relief", "playful", "warm", "none"})

#: topic 要能被 `RelationshipState.care_weight()` 查到才有意义。
#: 不在这张表里的一律回退到规则版的 TOPIC（"心情"）——
#: 让模型自由发挥 topic 会让 care_topics 永远命中不了，关系加成静默失效
_TOPICS = frozenset({"饮食", "睡眠", "工作", "情绪", "身体", "关系", "学习"})

_PROMPT = """你在帮一个人理解他女朋友刚说的话。

他叫 Nox，她叫糖糖。你的任务**不是**回复她，是判断她这句话底下有没有一件
值得他记挂的事，以及那件事是什么。

## 你要做的是意义推断，不是关键词匹配

字面："我不想干了" → 她说她不想做了
意义："可能不是字面上不想做，是累了、失望了，或者觉得继续投入没有意义"

第二行才是你要产出的东西。写不出来就老实说没有。

## 绝大多数话都该返回 none

她一天说几十上百句，绝大多数不是心事。
**误判的代价是他去念叨一件她根本没说过的事，而她无从知道他为什么这么想。**
所以：宁可漏，不可错。拿不准就 none。

以下一律 none：
- 日常闲聊、问事情、聊技术、说安排
- 转述别人的事、评论电影书里的角色（"那个主角好惨"不是她惨）
- 语气词、单纯的玩笑

## 🔴 语气亲昵 ≠ 有心事

这是最容易判错的一条。她跟他说话本来就带着"嘛""呀""要不试一下"这种语气，
**那是她平时的说话方式，不是一件要记的事。**

以下**一律 none**，哪怕语气再亲昵、再像撒娇：
- **要求你做事**："给我点杯咖啡" "来首睡前音乐" "你今天要不试一下给我点单"
- **报告问题 / 问功能**："你的表情包没发出来，是不是要分段" "这个怎么用"
- **日常招呼**："睡觉啦 晚安" "你想聊什么呀"

判断标准很简单：**去掉语气词之后，还剩下一件"事"吗？**
剩下的是「买咖啡」「表情包坏了」→ 那是任务或 bug，不是心事，返回 none。

`playful` 只留给**她真的在逗他、在跟他闹**的时候，而且那句话本身
除了"闹"没有别的内容。拿不准就 none。

## 锚点（anchor）

这件事**是关于什么**的，2 到 6 个字的名词，比如：毕设、工作、减肥、我们、猫、失眠。

已经记着的锚点：{known}
**如果这次说的是上面某一件事，必须原样复用那个锚点**，不要造近义词
（"毕设"和"毕业设计"分成两条，他就会以为是两件事）。

## 输出

只输出 JSON，不要任何解释文字：

{{
  "valence": "distress|relief|playful|warm|none",
  "anchor": "锚点，none 时留空",
  "topic": "饮食|睡眠|工作|情绪|身体|关系|学习 之一，选最贴的",
  "literal": "她字面说了什么，一句话",
  "meaning": "这句话可能意味着什么，一句话。**推断，不要复述字面**",
  "intensity": 0.0 到 0.62 之间的数,
  "confidence": 0.0 到 1.0，你对这个判断有多确定
}}

valence 的意思：
- distress 她不好受（有事压着）
- relief   她缓过来了 / 事情解决了
- playful  她在闹他、开玩笑
- warm     她心情好
- none     没什么要记的

intensity 参考：0.62 明确而重（撑不住、崩溃）；0.45 明显但不重；
0.3 有一点。**她随口一句抱怨不该给高分。**"""

_TURN = """她说：{her}

他回：{his}"""


#: 🔴 **这些会话里的「用户消息」不是她说的话**（2026-09-07 影子日志实录）。
#:
#: 日记批注（`diary-<id>`）和共读回批注（`reading-<id>`）都是**程序拼的提示词**
#: 塞进 `/chat` 的，开头是「（系统提示：这不是聊天窗口…）」。
#: 影子模式第 12 条就把它当成她的原话推断了：
#:
#:   她说的：日记本｜distress｜原话=（系统提示：这不是聊天窗口。糖糖刚写了一篇日记…）
#:
#: 它猜的意义也许没错，但**依据是错的** —— 它读的是我们自己写的提示词。
#: 而且那条会写进 Registry 的 evidence，Care 开口时会当成「她说过的话」说出来。
#:
#: ⚠️ 按**会话前缀**判，不按字符串匹配：提示词的措辞会改，链路的身份不会
#: （`bridge/server.js` 的 `diary-${id}` / `co-reading` 的 `reading-<id>`）。
NOT_HER_WORDS = ("diary-", "reading-")


def is_injected(session_id: str) -> bool:
    """这一轮的「用户消息」是程序拼的，不是她打的字。"""
    return str(session_id or "").startswith(NOT_HER_WORDS)


def mode() -> str:
    """这一层现在是什么状态。

    ⚠️ **默认 off**，和 `NOX_CHAT_CONCERN`（默认开）相反。

    理由是这条线要花钱、而且会写 Registry。规则版已经跑了一个月、
    行为可预测；这一层没有。默认关掉，先在 shadow 里看够了再转正。

        off     不跑（默认）
        shadow  跑，只记日志，**不写 Registry**
        on      跑，写 Registry
    """
    v = os.getenv("NOX_LLM_APPRAISAL", "off").strip().lower()
    if v in ("1", "on", "true", "yes"):
        return "on"
    if v == "shadow":
        return "shadow"
    return "off"


def _clean_json(raw: str) -> str:
    """把模型可能包的 ```json 围栏剥掉。

    便宜模型很爱加围栏，哪怕你说了「只输出 JSON」。
    这不算它出错，不该因此丢掉一次判断。
    """
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


class LLMAppraiser:
    """让便宜模型读一轮对话，判断她说的话意味着什么。

    实现 `appraisal.Appraiser` 协议，所以可以直接顶替 `RuleAppraiser` ——
    但**实际接线不是顶替**（见 `api/server.py` 的 `_appraise_async`）：
    规则版留在同步路径上当反射弧（它喂促狭的滑动窗口，要求即时），
    这一层在后台线程里跑。两个一起活，各管各的那一半。
    """

    def __init__(
        self,
        adapter_ref: Any,
        *,
        min_confidence: float = MIN_CONFIDENCE,
        ceiling: float = CEILING,
    ) -> None:
        #: 🔴 传**取值函数**不是 adapter 本身 —— 对齐 `ResonanceProvider`
        #: 的 `attention_ref`：router 在 Nox 组装时才有，而这个对象
        #: 可能更早就造好了。而且模型可切换，存快照会永远用启动时那个
        self.adapter_ref = adapter_ref
        self.min_confidence = min_confidence
        self.ceiling = ceiling

    # ------------------------------------------------------------ 协议兼容

    def appraise(self, text: str) -> Appraisal | None:
        """`Appraiser` 协议要求的形状。只有她那句话，没有上下文。

        真正该走的是 `appraise_turn()` —— 后置的全部价值就在于能看到他回了什么。
        这个方法留着是为了**协议可替换性**（原则 4）：
        哪天要拿它整个顶掉规则版，接口是对得上的。
        """
        return self.appraise_turn(text, "", ())

    # ------------------------------------------------------------ 真正的入口

    def appraise_turn(
        self,
        her_text: str,
        his_reply: str = "",
        known_anchors: tuple[str, ...] = (),
    ) -> Appraisal | None:
        """读完整一轮，产出理解。认不出来返回 None —— 那是**常态**。"""
        her_text = (her_text or "").strip()
        if not her_text:
            return None

        adapter = self.adapter_ref() if callable(self.adapter_ref) else self.adapter_ref
        if adapter is None:
            #: utility 没配就什么都不做，**不要退回主模型**。
            #:
            #: ⚠️ 2026-09-06 更正：线上 utility 和 primary **是同一个模型**
            #: （都是 deepseek-v4-flash），所以「省钱」不是现在的理由。
            #: 真正的理由是**分工要稳定**：这一层每轮都跑，它该用哪个模型
            #: 由 `NOX_UTILITY_MODEL` 一处说了算。悄悄退回主模型的话，
            #: 哪天主模型换成贵的，这条线会跟着涨价而没人知道。
            #:
            #: 而且它的价值是"多一层理解"，不是"必须有" —— 缺了就不做。
            logger.info("没有可用的 utility 模型，这轮不做意义推断")
            return None

        raw = self._ask(adapter, her_text, his_reply, known_anchors)
        if raw is None:
            return None
        return self._parse(raw, her_text)

    # ------------------------------------------------------------ 内部

    def _ask(
        self, adapter: Any, her: str, his: str, known: tuple[str, ...]
    ) -> str | None:
        from agent.llm import Message

        known_txt = "、".join(known[:MAX_KNOWN_ANCHORS]) if known else "（还没有）"
        system = _PROMPT.format(known=known_txt)
        body = _TURN.format(her=her, his=(his or "（这轮他还没说话）").strip())

        try:
            turn = adapter.complete(
                [Message(role="user", text=body)],
                tools=[],
                system=system,
                #: 判断题，不需要长输出。给多了它会写解释
                depth="low",
            )
        except Exception as exc:  # noqa: BLE001
            #: 🔴 **不许静默**（docs/LOGGING.md）。这一层挂了的表现是
            #: "他好像没那么懂我了"，没有任何报错 —— 不留痕就永远查不出来
            logger.warning("意义推断调用失败：%s: %s", type(exc).__name__, exc)
            return None

        if turn.stop_reason in ("error", "refusal") or not turn.text:
            logger.warning(
                "意义推断没拿到结果：stop_reason=%s error=%s",
                turn.stop_reason, turn.error,
            )
            return None
        return turn.text

    def _parse(self, raw: str, her_text: str) -> Appraisal | None:
        """把模型的 JSON 变成 Appraisal。**任何一步不对就返回 None。**

        这里每一条校验都对应一种"它编了但看起来很像真的"的失败方式。
        """
        try:
            d = json.loads(_clean_json(raw))
        except Exception:  # noqa: BLE001
            logger.warning("意义推断返回的不是 JSON，丢弃：%.120s", raw)
            return None
        if not isinstance(d, dict):
            logger.warning("意义推断返回的不是对象，丢弃：%.120s", raw)
            return None

        valence = str(d.get("valence") or "").strip().lower()
        if valence not in _VALENCES:
            logger.warning("意义推断给了未知 valence=%r，丢弃", valence)
            return None
        if valence == "none":
            #: 绝大多数话都会走到这里。**这是成功，不是失败**，
            #: 所以用 debug 不用 warning —— 否则日志里全是它
            logger.debug("意义推断：这句话没什么要记的（%.30s）", her_text)
            return None

        try:
            confidence = float(d.get("confidence", 0.0))
        except (TypeError, ValueError):
            logger.warning("意义推断的 confidence 不是数，丢弃：%r", d.get("confidence"))
            return None
        if confidence < self.min_confidence:
            #: 够不着门槛的**不留痕**。留下来的话，
            #: 「攒够几次低置信度就算数」这种想法迟早会有人去实现，
            #: 而那正是造出似是而非的 concern 的方式
            logger.info(
                "意义推断置信度 %.2f < %.2f，丢弃（%.30s）",
                confidence, self.min_confidence, her_text,
            )
            return None

        try:
            intensity = float(d.get("intensity", 0.0))
        except (TypeError, ValueError):
            intensity = 0.0
        #: 🔴 天花板在这里落地。模型给 0.9 也只能拿到 0.62
        intensity = max(0.0, min(self.ceiling, intensity))

        #: 强度 0 = 什么都不用记（2026-09-07 影子日志第 1、10 条）。
        #: `relief` 除外 —— 它的 0 是「这份记挂可以松了」，是个真信号。
        if intensity <= 0.0 and valence != "relief":
            logger.debug("意义推断：强度 0，没什么要记的（%.30s）", her_text)
            return None

        anchor = str(d.get("anchor") or "").strip()[:MAX_ANCHOR]
        #: 🔴 **她心情好的时候不建锚点**（2026-09-07 影子日志实录）。
        #:
        #: `evaluator._decide_from_appraisal` 里 warm/playful **就地返回 ignore，
        #: 根本不进 Registry**。给它们建一个「她说的：陪伴」这样的锚点，
        #: 只会让日志看起来记下了什么 —— 实际算完就扔。
        #:
        #: ⚠️ 这不代表那些话不值得留。影子日志里
        #: 「我想早点造一个全屋智能的让你住进去」确信 0.95，
        #: 那是很该被记住的一句 —— 但**该由 OB 的 remember/grow 来存，
        #: 不是塞进「他惦记着的事」那张表**。两件事别混。
        if valence in ("playful", "warm"):
            anchor = ""
        topic = str(d.get("topic") or "").strip()
        if topic not in _TOPICS:
            #: 不在表里就回退。**不要原样采用** ——
            #: `care_weight()` 查不到的 topic 会静默拿到中性权重 0.5，
            #: 表现成"关系加成没生效"，而且完全不报错
            topic = TOPIC

        #: distress 之外的三档不需要锚点：relief 要能对上已有的那条才 weaken 得掉，
        #: playful/warm 根本不进 Registry（见 evaluator 的 _decide_from_appraisal）
        subject = anchored(anchor) if anchor else SUBJECT

        meaning = str(d.get("meaning") or "").strip()[:MAX_MEANING]
        literal = str(d.get("literal") or "").strip()[:MAX_MEANING]

        ap = Appraisal(
            subject=subject,
            topic=topic,
            valence=valence,
            intensity=intensity,
            #: cue 在规则版里是"命中的那个词"。这一层没有词，
            #: 用锚点占位 —— evidence 要回答"他凭什么这么想"，
            #: 这里的答案是"因为他理解成了这件事"
            cue=anchor or "理解",
            quote=her_text[:40],
            literal=literal,
            meaning=meaning,
            confidence=confidence,
            anchor=anchor,
        )
        logger.info(
            "意义推断：%s｜%s → %s（%.2f，确信 %.2f）",
            subject, valence, meaning or "（没给出意义）", intensity, confidence,
        )
        return ap
