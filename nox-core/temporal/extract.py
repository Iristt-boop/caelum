"""Temporal Extraction —— 从她的话里认出时间关系。

契约见 `CAELUM-Temporal-Intent-Contract.md`。这是第二层的**另一半**：
`resolver.py` 负责「落在哪」，这里负责「她说的关系是什么」。

## 🔴 它是独立的 contract，**不挂 `appraisal_llm`**

糖糖 2026-09-14 拍的，理由不是"多一次调用"，是**职责已经不同**：

    appraisal_llm  →  「她现在是什么状态 / 这句话意味着什么」
    Temporal       →  「这句话里的时间关系是什么」

一次调用确实能同时抽两个，但现在最需要的是边界清晰、可测试、可独立演进。
而且 2026-09-14 刚遇到一次 shadow 把东西吞掉（`core.attention` 恒为 None，
理解层两道闸串着都关着）—— 第二层不该从第一天就依赖那个行为。

> 以后若发现两者输入完全相同、成本明显值得优化，可以合并成一次调用。
> ⚠️ 那是**「调用合并」，不是「语义模块合并」** —— 两个 contract 各自独立。

## 🔴 提示词里**一个日期都不给**

这是契约第二节那条红线的落地方式：不是在提示词里写「别算日期」，
是**不给它算的材料**。下面的 `_PROMPT` 里没有今天几号、没有星期几、
没有 `reference_time`，模型想填 `resolved_date` 也填不出来。

而且 `Intent` 这个结构里根本没有 `resolved_*` 字段（`intent.py`），
`Intent.from_dict()` 还会拒绝未知字段 —— 三道，都不靠提示词。

理由：关系错了看得出来（「明天」听成「后天」很显眼），**日期错了看不出来**。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from temporal.intent import Intent

logger = logging.getLogger(__name__)

#: 跟 `NOX_LLM_APPRAISAL` 同构但**独立**的开关。
#:
#:     off     不跑（默认）
#:     shadow  跑，只记日志，不产生任何副作用
#:     on      跑，接消费方
#:
#: ⚠️ 默认 off，和理解层同一个理由：它要花钱，而且行为还没被观察过。
ENV = "NOX_TEMPORAL"


def mode() -> str:
    v = os.getenv(ENV, "off").strip().lower()
    if v in ("1", "on", "true", "yes"):
        return "on"
    if v == "shadow":
        return "shadow"
    return "off"


#: 便宜模型很爱给 JSON 包 ```json 围栏，哪怕你说了只输出 JSON。
#: 这不算它出错，不该因此丢掉一次判断（同 `appraisal_llm._clean_json`）。
def _clean_json(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


#: ⚠️ 这段里**故意没有任何日期、星期、"今天是"**。见模块开头。
_PROMPT = """你要从一句中文里认出**时间关系**，只认关系，不算日期。

你不知道今天是几号，也不需要知道。永远不要输出任何具体日期。

只输出一个 JSON 对象，没有时间表达就输出 {"kind": null}。

可用的 kind（**只有这些，不许自创**）：

· day_offset   —— 今天/明天/昨天/前天/后天。字段 n：今天0、明天+1、昨天-1、前天-2、后天+2
· weekday_next —— 「**下**周三」这种带「下」字的。字段 weekday：1=周一…7=周日
· weekday_bare —— 光秃秃的「周三」「礼拜五」，没有「下」字。字段 weekday 同上
· month_end    —— 月底、这个月底。没有字段
· duration     —— 「两个小时后」「十分钟后」「三天后」。字段 days / hours / minutes（给用到的那个）
· deadline     —— 「……之前」「……前」。字段 before，里面装另一个上面的对象
· last_night   —— 「昨晚」「昨天晚上」「昨儿晚上」。没有字段。**不要用 day_offset 表示它**
· vague        —— 「一会」「待会」「回头」「晚点」「改天」「有空」。没有字段

可选修饰符 slot（一天里的哪一段），只能配 day_offset / weekday_next / weekday_bare / month_end：
morning（上午）/ afternoon（下午）/ evening（晚上）/ late_night（深夜、凌晨）

例子：
「明天去练腿」        → {"kind": "day_offset", "n": 1}
「昨天晚上没睡好」    → {"kind": "last_night"}
「昨晚老醒」          → {"kind": "last_night"}
「我一会再煎个鸡蛋」  → {"kind": "vague"}
「回头再说」          → {"kind": "vague"}
「下周三见」          → {"kind": "weekday_next", "weekday": 3}
「周五去」            → {"kind": "weekday_bare", "weekday": 5}
「月底交」            → {"kind": "month_end"}
「两个小时后叫我」    → {"kind": "duration", "hours": 2}
「下周五之前弄完」    → {"kind": "deadline", "before": {"kind": "weekday_next", "weekday": 5}}
「今天不去，明天再去」→ {"kind": "day_offset", "n": 1}
「我有点累」          → {"kind": null}

⚠️ 分不清「下周三」和「周三」时，按她原话有没有「下」字来判，不要猜。
⚠️ 一句话里有多个时间时，取**她要做的那件事**的时间
（「今天不去，明天再去」取「明天」）。

🔴 **「一会」「回头」「晚点」绝对不要折算成 duration。**
它们不是「30 分钟后」，是「可能根本不做」—— 一种拖延，不一定是要做的意思。
给一个具体分钟数就是把模糊伪装成精确。一律 vague。

🔴 **「昨晚」用 last_night，不要写成 day_offset -1 + evening。**
那一夜跨午夜，而睡眠数据按醒来那天归档，当成日历日会差一天。

⚠️ 招呼语里的时间词不算时间表达：
「早安」「晚安」「早上好」是打招呼，不是在说「今天早上」→ {"kind": null}。"""

_TURN = "她说：{text}"


class TemporalExtractor:
    """把一句话变成 `Intent`。认不出来返回 None —— **那是常态**。"""

    def __init__(self, adapter_ref: Any) -> None:
        #: 传取值函数不是 adapter 本身（同 `appraisal_llm` 的理由）：
        #: router 在 Nox 组装时才有，而这个对象可能更早造好；
        #: 而且模型可切换，存快照会永远用启动时那个
        self.adapter_ref = adapter_ref

    def extract(self, text: str) -> Intent | None:
        text = (text or "").strip()
        if not text:
            return None

        adapter = self.adapter_ref() if callable(self.adapter_ref) else self.adapter_ref
        if adapter is None:
            #: utility 没配就不做，**不要退回主模型** —— 分工要稳定，
            #: 悄悄退回的话哪天主模型换成贵的，这条线会跟着涨价而没人知道
            logger.info("没有可用的 utility 模型，这轮不抽时间关系")
            return None

        raw = self._ask(adapter, text)
        if raw is None:
            return None
        return self._parse(raw, text)

    # ------------------------------------------------------------ 调模型

    def _ask(self, adapter: Any, text: str) -> str | None:
        from agent.llm import Message

        try:
            turn = adapter.complete(
                [Message(role="user", text=_TURN.format(text=text))],
                tools=[], system=_PROMPT,
                #: 判断题，不需要长输出。给多了它会写解释
                depth="low",
            )
        except Exception as exc:  # noqa: BLE001
            #: 🔴 不许静默（docs/LOGGING.md）。这一层挂了的表现是
            #: 「他又开始搞不清时间了」，没有任何报错
            logger.warning("时间关系抽取调用失败：%s: %s", type(exc).__name__, exc)
            return None

        if turn.stop_reason in ("error", "refusal") or not turn.text:
            logger.warning("时间关系抽取没拿到结果：stop_reason=%s error=%s",
                           turn.stop_reason, turn.error)
            return None
        return turn.text

    # ------------------------------------------------------------ 解析

    def _parse(self, raw: str, text: str) -> Intent | None:
        """模型的 JSON → `Intent`。

        🔴 **这是模型输出进 Core 的唯一入口。**
        `Intent.from_dict` 会拒绝表外的 kind、拒绝未知字段、验字段组合 ——
        在这里放行的东西下游全会当真，所以宁可丢掉也不放行。
        糖糖 2026-09-14 判它「结构正确」：它把「模型可以自由生成 JSON」
        和「系统允许什么进入 Core」切开了。
        """
        try:
            d = json.loads(_clean_json(raw))
        except json.JSONDecodeError:
            logger.warning("时间关系返回的不是 JSON，丢弃：%.120s", raw)
            return None
        if not isinstance(d, dict):
            logger.warning("时间关系返回的不是对象，丢弃：%.120s", raw)
            return None

        if d.get("kind") is None:
            #: 绝大多数话都走这里。**这是成功不是失败** ——
            #: 「她没说时间」和「说了但认不出」是两回事，前者根本不产出 intent
            logger.debug("这句话里没有时间关系（%.30s）", text)
            return None

        try:
            return Intent.from_dict(d)
        except ValueError as exc:
            #: 模型编了个表外的 kind，或者字段组合不合法。
            #: **丢掉**，但要留痕 —— 反复出现说明提示词该改了
            logger.warning("时间关系不合契约，丢弃（%.30s）：%s｜原始 %.80s",
                           text, exc, raw)
            return None
