"""Attention Engine —— Nox Core 的内部模块，不是独立服务。

回答的问题是：**「什么值得 Nox 关心？」**
（Context Engine 回答的是「现在世界是什么状态」，两者不重叠。）

当前到 M2，睡眠这一条链路是通的：

    events.py        ExperienceEvent —— 所有 Source 共用的事件契约
    registry.py      Attention + AttentionRegistry —— 关心什么、多强、多久淡
    store.py         SQLite 落盘，重启不丢
    relationship.py  Relationship Model（硬编码种子，M5 才开始学）
    evaluator.py     规则版分类器：这值不值得关心、有多关心
    engine.py        把上面几件串起来的门面
    sources/sleep.py 睡眠 Source + Temporal Filter

**故意还没有的东西**（架构设计 v1.3 第 14.3 节「这一轮明确不做什么」）：

    Experience Bus 机制   现在就一个消费者，直接调。等第 3-4 个 Source
                          接进来、分支变难看时再提成 Bus（那时是机械重构，
                          因为 events.py 的契约从第一天就统一了）
    LLM Evaluator         先纯规则。上 LLM 之前要先能说出具体哪类事件判错了
    focus/curiosity/goal  只做 concern，另外三种等第二个 Source
    Intent / Scheduler    M3
    Feedback              M5

加东西之前先回去看那张表 —— 「边做边膨胀」是这份设计列在册的风险之一。
"""

from attention.engine import AttentionEngine
from attention.evaluator import AttentionDecision, AttentionEvaluator
from attention.events import ExperienceEvent
from attention.registry import Attention, AttentionRegistry, Evidence
from attention.relationship import RelationshipState
from attention.store import AttentionStore

__all__ = [
    "Attention",
    "AttentionDecision",
    "AttentionEngine",
    "AttentionEvaluator",
    "AttentionRegistry",
    "AttentionStore",
    "Evidence",
    "ExperienceEvent",
    "RelationshipState",
]
