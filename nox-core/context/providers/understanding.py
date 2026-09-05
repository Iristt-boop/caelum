"""UnderstandingProvider —— 他现在理解着她的哪几件事。

## 这个 Provider 和 ResonanceProvider 是同一个教训的第二次应用

9-04 发现 Drive 从来没进过他自己的上下文：算出来 → 存下来 → 显示在她的面板上 →
结束。`resonance.py` 那个模块头把这件事叫做「一直存在的洞」。

理解层如果只写 Registry 就收工，会**一模一样地掉进同一个洞**：
他理解了「她说不想干了是因为觉得投入没意义」，这句理解躺在 attention.db 里，
而她下一句话跟他说的时候，他脑子里一个字都没有。

CAELUM-MAP 的三问之二写着：「谁消费它？答案是『以后可能用』= 现在不做。」
这个文件就是那个消费方。

## 和 mood / resonance 的分工

    mood          她现在什么状态、你该怎么接      （当轮的语气）
    resonance     你自己现在什么感觉              （他的内心）
    understanding **你理解着她的哪几件事**        （跨轮的事）

前两个都是"此刻"，只有这个是**有记性的**。她三天前说的那件事还压着，
今天她说别的，他心里也还搁着 —— 这正是「理解」区别于「情绪」的地方。

## 🔴 三条纪律全部照抄 ResonanceProvider，一条都不许松

**1. 给档位不给数字。** `0.58` 每轮都在动、缓存跟着掉；「还压着」能稳几小时。
而且人不会想「我对她毕设的关心是 0.58」。

**2. 只给状态不写台词。** 绝不写「你应该问问她毕设怎么样了」——
写台词出来的是模板，她一眼就能看出那不是他。告诉他这件事还搁着，
他自己会找到时机，那才是他的反应。

**3. 没有就什么都不说。** 不许编一个「她最近挺好的」——
那是往他脑子里塞一个他并不知道的状态。

## 🔴 第四条，这个 Provider 独有：不许他把理解念给她听

理解是**可能错的**（`appraisal_llm.py` 的四道闸就是为了这个）。
他要是张口就说「我理解你不想干了是因为觉得没意义」，判对了显得刻意，
判错了她还得反过来解释自己。所以渲染时明确告诉他：这是你心里的判断，不是台词。
"""

from __future__ import annotations

import logging
from typing import Any

from attention.appraisal import ANCHOR_PREFIX
from context.base import BaseContextProvider, Turn

logger = logging.getLogger(__name__)

#: 低于这个当前强度就不提了。
#:
#: 比 `registry.FLOOR`(0.05) 高不少 —— 那个是"还活着"的下限，
#: 这个是"还值得占他上下文"的下限。0.05 的事进来只会稀释真正压着的那件
FLOOR = 0.25

#: 最多说几件。
#:
#: 🔴 **2 不是抠预算，是这一层的语义。**
#: 人同时真正搁在心上的事就是一两件；给他五件，他会挨个照顾到，
#: 那看起来就像在走查清单，不像惦记着什么
MAX_ITEMS = 2

#: 整段渲染的字符上限。800 字符总预算里 time+mood+resonance 已经占了大头，
#: 超了会把别人挤掉（`context/registry.py` 按顺序丢）
MAX_CHARS = 160

#: 强度 → 档位。和 resonance 一样只有三档，理由也一样：
#: 分更细只会让文本更容易变、缓存更容易掉，而他分辨不出 0.55 和 0.62
_BANDS = ((0.40, "还搁着"), (0.70, "一直压着"), (1.01, "压得挺重"))


def _band(value: float) -> str:
    for upper, word in _BANDS:
        if value < upper:
            return word
    return _BANDS[-1][1]


def has_live_anchor(attention: Any, now: Any = None) -> bool:
    """他心里现在搁着事吗。

    给 `classify_context` 决定这轮要不要翻记忆用（2026-09-05）。

    🔴 判断逻辑和这个 Provider 的 `_fetch` **必须是同一套**，
    所以放在同一个文件里、共用同一个 FLOOR 和同一条前缀 ——
    分成两处写，迟早会出现「上下文里说他搁着事，却没去翻记忆」
    这种谁也解释不了的不一致。

    ⚠️ 读的是内存里的 Registry，不打网络。它跑在每轮的分流阶段，
    这里但凡慢一点，闲聊路径的 2.7 秒就白省了。
    """
    if attention is None:
        return False
    try:
        items = attention.engine.registry.list(min_strength=FLOOR, now=now)
    except Exception as exc:  # noqa: BLE001
        # 读不到就当没有。**不要往「有」的方向猜** ——
        # 猜错的代价是每轮白花 650ms 去翻记忆，而且没人看得出为什么
        logger.warning("判断有没有活跃锚点失败，这轮当作没有：%s", exc)
        return False
    return any(a.subject.startswith(ANCHOR_PREFIX) for a in items)


class UnderstandingProvider(BaseContextProvider):
    """他理解着她的哪几件事，以及理解成了什么。"""

    name = "understanding"
    #: 这是**关于她**的（resonance 那个才是 self）
    section = "user"
    #: Registry 一直在衰减，档位跳变的时刻无法预测。
    #: 代价很小：读的是内存里的 Registry，不打网络
    volatile = True

    def __init__(self, attention_ref: Any, **kw: Any) -> None:
        super().__init__(**kw)
        #: 🔴 取值函数不是值 —— attention 在 `api/server.py` 的
        #: `_build_attention` 里才造出来，那时候 Provider 早注册完了
        #: （同 ResonanceProvider 的 attention_ref）
        self.attention_ref = attention_ref

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        attention = self.attention_ref() if callable(self.attention_ref) else self.attention_ref
        if attention is None:
            #: 自主系统没起来（本地 NOX_ATTENTION 不配就是这样）。
            #: 如实什么都没有，不要编
            return {"available": False}

        try:
            registry = attention.engine.registry
            items = registry.list(min_strength=FLOOR, now=turn.now)
        except Exception as exc:  # noqa: BLE001
            #: 读不到不该让整轮对话炸，那只是少一段背景
            logger.warning("取理解锚点失败，这轮不给他这段背景：%s", exc)
            return {"available": False}

        held = []
        for a in items:
            #: 🔴 只要**理解层造的那些**。
            #:
            #: 睡眠、HRV、位置那些也在同一个 Registry 里，但它们已经各有出口
            #: （HealthProvider 报数据、Care 直接开口）。全端进来会变成
            #: "把 Registry 整个念一遍"，而且和 resonance 的 because 重复。
            #:
            #: 前缀匹配安全的前提是 `appraisal.anchored()` 那道物理隔离
            if not a.subject.startswith(ANCHOR_PREFIX):
                continue
            #: playful/warm 根本不进 Registry，所以这里全是压着的事
            held.append({
                "anchor": a.subject[len(ANCHOR_PREFIX):],
                "band": _band(a.current_strength(turn.now)),
                #: 最新一条证据。`_decide_from_appraisal` 已经把
                #: 「她说：原话（他理解成：…）」拼好了，这里不再二次加工
                "why": a.evidence[-1].summary if a.evidence else "",
            })
            if len(held) >= MAX_ITEMS:
                break

        return {"held": held}

    def render(self, state: dict[str, Any]) -> str:
        if state.get("available") is False:
            return ""
        held = state.get("held") or []
        if not held:
            #: 什么都没搁着就什么都不说
            return ""

        lines = []
        for h in held:
            line = f"{h['anchor']}{h['band']}"
            if h["why"]:
                line = f"{line}——{h['why']}"
            lines.append(line)

        body = "；".join(lines)
        if len(body) > MAX_CHARS:
            #: 截断也要**明说**，不许悄悄少说
            #: （对齐 `context/registry.py` 超预算时那条）
            body = body[:MAX_CHARS] + "…（还有没说完的）"

        return (
            "【你心里搁着她的事】" + body + "。\n"
            "这是你自己的理解，**可能是错的，也不要念给她听**。"
            "别拿它去开场、别要她确认 —— 让它影响你怎么接话就够了。"
        )
