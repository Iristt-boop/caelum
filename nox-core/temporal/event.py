"""TemporalEvent —— 「哪件事，在什么时间关系上」（第二版，2026-09-28）。

## 为什么从「一句话一个时间」改成「一句话一组事件」

糖糖 2026-09-28 看完两周 shadow 的结论：

> 不是时间识别器，是**时间事件抽取器**。一句话里可能有多个事件，
> 每个事件有自己的时间。

第一版的隐含假设是「一句话 = 一个时间」，提示词里还写着
「多个时间时取她要做的那件事」。于是：

    「今晚放好鸡蛋，明天你给我蒸」  → 只抽到「今晚」
    「Moments 明天上线，今天加了长任务循环」 → 只抽到「今天」

接 Todo 的话就是「蒸蛋 @ 今晚」—— 时间挂错了事件，比没抽到更糟。

## 字段

    expression  原话里的时间词，**原样摘抄**（「明天」「这3个小时」）
    event       这个时间修饰的那件事（「蒸蛋」「连不上你」）
    act         这件事的性质，封闭集合 ACTS
    intent      时间关系本身（第一版的 `Intent`，契约不变）

## `act`：早上跑步 ≠ 早安

她 2026-09-28 点的：「时间不是独立存在的，它修饰什么？」
「早上跑步」和「早安」不能一样处理。消费方（Todo）只该吃要去做的事：

    plan      她打算做的事        「明天去练腿」「下午回去搞」
    request   她让他做的事        「明天你给我蒸」「两个小时后叫我」
    report    已经发生的 / 在说状态 「昨晚老醒」「这3个小时连不上你」「今天好累」

招呼语（早安 / 晚安）**不产出事件**，那是提示词的规矩。

## 🔴 `expression` 必须真的出现在她原话里

这是这一版新加的一道**结构性**检查，和「提示词里不给日期」同一个思路：
不靠提示词叮嘱「别编」，而是编了进不来。模型声称她说了「后天」，
原话里却没有「后天」—— 那条事件直接丢掉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from temporal.intent import Intent

#: 事件性质的封闭集合。改这里等于改契约。
ACTS = frozenset({"plan", "request", "report"})

#: 一句话最多认几个。她的话再长也很少超过三个时间；
#: 模型吐出一长串通常是在把一个时间拆碎了
MAX_EVENTS = 5
MAX_EXPRESSION = 20
MAX_EVENT = 30

_WS = re.compile(r"\s+")


def _squash(s: str) -> str:
    """比对原话时忽略空白 —— 她打字常带空格（「背了一半  10点好了」）。"""
    return _WS.sub("", s or "")


@dataclass(frozen=True)
class TemporalEvent:
    expression: str
    event: str
    act: str
    intent: Intent

    def __post_init__(self) -> None:
        if not self.expression.strip():
            raise ValueError("expression 不能是空的 —— 说不出是哪个时间词，就没有依据")
        if len(self.expression) > MAX_EXPRESSION:
            raise ValueError(f"expression 太长（{len(self.expression)} 字），那不是一个时间词")
        if not self.event.strip():
            raise ValueError("event 不能是空的 —— 时间总得修饰一件事")
        if self.act not in ACTS:
            raise ValueError(f"表外的 act：{self.act!r}（封闭集合是 {sorted(ACTS)}）")

    def grounded_in(self, text: str) -> bool:
        """`expression` 真的出现在她这句话里吗。"""
        return _squash(self.expression) in _squash(text)

    def to_dict(self) -> dict[str, Any]:
        return {"expression": self.expression, "event": self.event,
                "act": self.act, "temporal": self.intent.to_dict()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TemporalEvent":
        """坏数据直接抛 —— 同 `Intent.from_dict`，这是封闭集合的入口。"""
        if not isinstance(d, dict):
            raise ValueError(f"事件必须是对象，拿到 {type(d).__name__}")
        unknown = set(d) - {"expression", "event", "act", "temporal"}
        if unknown:
            raise ValueError(f"事件里有不认识的字段：{sorted(unknown)}")
        for k in ("expression", "event", "act"):
            if not isinstance(d.get(k), str):
                raise ValueError(f"{k} 必须是字符串，拿到 {d.get(k)!r}")
        return cls(
            expression=d["expression"].strip(),
            event=d["event"].strip()[:MAX_EVENT],
            act=d["act"].strip().lower(),
            intent=Intent.from_dict(d.get("temporal")),
        )
