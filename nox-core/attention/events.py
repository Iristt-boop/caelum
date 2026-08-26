"""Experience 事件契约。

## 为什么第一件事是定这个

「Experience Bus」这个词里打包了两样东西，改动成本差一个数量级：

    契约（这个文件）   改了要动**所有** Source —— 贵
    机制（fan-out）    改了只动一处 —— 便宜

所以先付贵的那笔。哪怕现在只有一个 Source（睡眠），也让它从第一天就说
这套话；等第三、第四个 Source 接进来要提成真正的 Bus 时，那是一次
机械重构，没有任何 Source 需要改。

（架构设计 v1.3 第 3.4 节。`context/__init__.py:9` 早就立过同一条规矩：
「不要现在就拆独立服务、独立数据库、消息队列。」）

## 为什么是同步的

`nox-core` 从 `loop.run()` 到 `tool.handler()` 到 `run_stream()` 全是同步的，
而且这套代码在异步/同步边界上有前科（contextvars 跨同步生成器，
PROJECT.md 第十九节第 13 条）。Attention 这一层只做规则计算、不打网络，
没有任何理由 async —— 详见 `context/base.py:25` 的第二条硬约束。

只有最外层的事件入口和 Scheduler 心跳允许 async，边界画在 FastAPI 那层。

## frozen 的理由

事件是**已经发生的事实**，不该被下游改。Attention Evaluator 拿到之后
如果能改 payload，那「同一个事件为什么在两个消费者眼里不一样」这种问题
根本没法查。要衍生就造新对象。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class ExperienceEvent:
    """一件「发生了的事」。

    所有 Source 都产出这个类型，不许各造各的。

        ExperienceEvent(
            source="health",
            type="sleep_quality_changed",
            subtype="poor_sleep",
            payload={"hours": 5.2, "quality": "poor", "deep_sleep_percent": 12},
        )

    `source` + `type` 是给规则匹配用的，**要稳定**。改一个字符就等于
    让所有匹配它的规则静默失效 —— 加新的比改旧的安全。
    """

    source: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_now)
    id: str = field(default_factory=_new_id)
    subtype: str | None = None
    #: "low" / "normal" / "high"。high 目前只用来标健康异常这类
    #: 允许突破 Scheduler 冷却的事件，其余一律 normal
    priority: str = "normal"
    #: 事件产生时的来龙去脉（book_id / session_id / chunk_id 之类），
    #: 只用来事后追查，不参与任何判断逻辑
    origin_context: dict[str, Any] | None = None
    #: 🔴 **这件事发生在谁身上**（Resonance V3.6）。
    #:
    #:     "user"   她 —— 她没睡好、她说难受   → 他凑过去（心疼、想念）
    #:     "agent"  他 —— 他挑错了时机开口     → 他收回来（后悔、下次晚点说）
    #:
    #: 思路来自 emoai-affect-engine 的 `eventNeuroTargetOverrides`：
    #: 同一件事，发生在用户身上和发生在 agent 身上，意义是相反的。
    #: 它用 oxytocin 的正负号表达 —— 她受威胁 +0.055（共情），
    #: 他自己受威胁 -0.025（防御）。原注释：
    #: 「这些覆盖让共情性的关注和 agent 自身的防御反应保持区分。」
    #:
    #: ⚠️ 现有的事件（health / chat）**全是关于她的**，所以默认 `user`，
    #: 一处都不用改。这个字段是跟着「后悔」一起来的 ——
    #: 在那之前它只有一个取值，加了也是间接层
    target: str = "user"

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("ExperienceEvent.source 不能为空")
        if not self.type:
            raise ValueError("ExperienceEvent.type 不能为空")
        if self.priority not in ("low", "normal", "high"):
            raise ValueError(f"priority 只能是 low/normal/high，拿到 {self.priority!r}")
        # 没带时区的时间存进 SQLite 再读出来就分不清是 UTC 还是本地时间了，
        # 而 Attention 的衰减、Scheduler 的冷却全靠时间差算 —— 这里卡死
        if self.timestamp.tzinfo is None:
            raise ValueError("ExperienceEvent.timestamp 必须带时区")

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        """给持久化和日志用。时间统一 ISO 8601 带时区。"""
        return {
            "id": self.id,
            "source": self.source,
            "type": self.type,
            "subtype": self.subtype,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "priority": self.priority,
            "origin_context": self.origin_context,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ExperienceEvent:
        return cls(
            id=d["id"],
            source=d["source"],
            type=d["type"],
            subtype=d.get("subtype"),
            payload=d.get("payload") or {},
            timestamp=datetime.fromisoformat(d["timestamp"]),
            priority=d.get("priority", "normal"),
            origin_context=d.get("origin_context"),
        )

    def __str__(self) -> str:  # pragma: no cover - 日志用
        sub = f"/{self.subtype}" if self.subtype else ""
        return f"{self.source}.{self.type}{sub}"
