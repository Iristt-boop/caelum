"""TemporalResult —— 一次时间理解的完整记录，以及 shadow 的出口。

契约见 `CAELUM-Temporal-Intent-Contract.md` 第六节。

## 🔴 shadow 必须有出口，否则它不是实验，是黑洞

糖糖 2026-09-14 的原话。这条是当天刚付过学费的：
理解层跑了一周多 shadow，回头看那 9 条被丢掉的记录，
**日志里只有她的原话和一个数字，模型推断了什么一个字都没留** ——
于是那一周的观察给不出任何结论。

所以日志**必须三段齐全**：

    ① 模型说了什么      原始 intent          —— 认错和算错要分得开
    ② Resolver 算了什么  resolution + 锚点    —— 没有锚点就没法复算
    ③ 为什么没接下游     why_not_applied      —— 决定什么时候能打开真实写入

③ 最容易被省掉，而它恰恰是那个信息。只记①②的话，几天后看到一堆
漂亮的解析结果，仍然不知道**真接上会发生什么**。

## 做成「缺一段就构造不出来」

`why_not_applied` 在 `applied=False` 时是**必填**，`__post_init__` 拦。
靠自觉写日志的话，迟早有人为了图省事传个空字符串。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from temporal.intent import Intent
from temporal.resolver import Resolution

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TemporalResult:
    """一次时间理解的全过程。

    `intent` 和 `resolution` **是两个对象**（契约第三节），
    这里只是把它们和锚点装在一起，不是把它们合并。
    """

    #: 她原话（截断存日志用）
    text: str
    #: ① 模型说了什么
    intent: Intent
    #: ② Resolver 算了什么
    resolution: Resolution
    #: 解析用的锚点 —— **必须是 message_time**，没有它没法复算
    reference_time: datetime
    #: 有没有真的接到下游
    applied: bool = False
    #: ③ 没接的话，为什么。`applied=False` 时**必填**
    why_not_applied: str | None = None
    #: 接了的话，接给谁、干了什么（给审计看）
    applied_to: str | None = None

    def __post_init__(self) -> None:
        if not self.applied and not self.why_not_applied:
            raise ValueError(
                "没接下游就必须说明为什么 —— 少了这一段，shadow 就是个黑洞")
        if self.applied and not self.applied_to:
            raise ValueError("接了下游就要说明接给谁")

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text[:60],
            "intent": self.intent.to_dict(),
            "resolution": self.resolution.to_dict(),
            "reference_time": self.reference_time.isoformat(),
            "applied": self.applied,
            "why_not_applied": self.why_not_applied,
            "applied_to": self.applied_to,
        }

    # ------------------------------------------------------------ 出口

    def log(self) -> None:
        """shadow 的出口。**三段一行打完**，别分三条 —— 分开的话
        journalctl 里它们会被别的日志冲散，拼不回来。
        """
        r = self.resolution
        landed = (
            r.to_dict() if r.ok
            else f"未解析（{r.unresolved_reason}）"
        )
        logger.info(
            "时间理解｜她说「%.40s」\n"
            "  ① 模型认成：%s\n"
            "  ② 锚点 %s → %s\n"
            "  ③ %s",
            self.text,
            self.intent.to_dict(),
            self.reference_time.isoformat(),
            landed,
            f"已接 {self.applied_to}" if self.applied else f"未接：{self.why_not_applied}",
        )
