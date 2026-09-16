"""Attention Engine —— 把事件变成关心。

## 一个事件走完的全程

    Source.poll()            「发生了什么」（Temporal Filter 在这一步）
         ↓ ExperienceEvent
    Engine.handle()
         ↓
    Evaluator.evaluate()     「这值不值得关心、有多关心」
         ↓ AttentionDecision
    Registry.upsert()        「记下来」
         ↓
    Store.save()             「重启也不忘」

## 这里就是文档里那个 notify()

架构设计 v1.3 第 3.4 节说：这一轮不建 Bus，因为只有一个消费者。
所以「分发」就是直接调 `engine.handle(event)`，没有中间层。

等第三、第四个 Source 接进来，需要「这个事件发给 A 和 C 但不发给 B」时，
再把这行调用提成真正的 Bus —— 那时是机械重构，
因为 `events.py` 的契约从第一天就是统一的。

## 每次变更都落盘

Attention 的变更频率很低（一天几次），SQLite 全量重写是毫秒级。
攒着批量写省不下什么，却要多担一个「崩在攒着的时候就丢了」的风险。
"""

from __future__ import annotations

import logging
from datetime import datetime

from attention.evaluator import AttentionDecision, AttentionEvaluator
from attention.events import ExperienceEvent
from attention.registry import AttentionRegistry
from attention.relationship import RelationshipState
from attention.store import AttentionStore

logger = logging.getLogger(__name__)


class AttentionEngine:
    """Registry + Evaluator + Store 的门面。

        engine = AttentionEngine.bootstrap(store, RelationshipState())
        engine.handle(event)
        engine.registry.list(min_strength=0.5)
    """

    def __init__(
        self,
        registry: AttentionRegistry,
        evaluator: AttentionEvaluator,
        store: AttentionStore | None = None,
    ) -> None:
        self.registry = registry
        self.evaluator = evaluator
        self.store = store

    @classmethod
    def bootstrap(
        cls,
        store: AttentionStore,
        relationship: RelationshipState | None = None,
        world: object | None = None,
    ) -> AttentionEngine:
        """从库里把上次的关心读回来，接着关心。

        这是「重启不失忆」真正落地的地方 —— 前面 store 那些代码
        都是为了这一行。

        `world` 是 World Model，给 Evaluator 反查趋势用（「连着几天没睡够」）。
        **可以为 None** —— 那样退回单次判断，行为和接入之前完全一样。
        """
        registry = store.load()
        return cls(
            registry=registry,
            evaluator=AttentionEvaluator(relationship or RelationshipState(), world=world),
            store=store,
        )

    # ------------------------------------------------------------ 入口

    def handle(
        self, event: ExperienceEvent, now: datetime | None = None
    ) -> AttentionDecision:
        """处理一个事件。返回决定（含 ignore），方便日志和测试。"""
        decision = self.evaluator.evaluate(event, self.registry, now)

        if not decision.should_apply:
            # 🔴 **这一行是「他为什么没有开口」的唯一证据**（审计 1.3）。
            #
            # 原来写死在 DEBUG，而两个入口的 `basicConfig` 又写死 INFO ——
            # 等于永远看不见。查一次"他今天怎么一声不吭"要先改代码、再发一次版。
            #
            # 现在分两档（`AttentionDecision.routine`）：
            #   routine=True   她说了句普通的话、指标正常 —— 每天几百次，留在 DEBUG
            #   routine=False  她说过别问这个、开关被关掉、事件没人接 —— 走 INFO
            #
            # 分档不是洁癖：全提到 INFO 的话，真正的那几条会被
            # "这句话没有需要记挂的信号" 淹掉，等于换了个方式看不见。
            logger.log(
                logging.DEBUG if decision.routine else logging.INFO,
                "Attention 不关心 %s：%s", event, decision.reason,
            )
            return decision

        if decision.action == "weaken":
            # 关心的对象好转了 —— 松一口气，不是慢慢忘
            #
            # ⚠️ upsert 那几条在 `evaluator.py` 里各自打了 INFO，**唯独
            # weaken 没有** —— 于是"他昨天还惦记着，今天怎么不提了"
            # 在日志里是一片空白。补在这里而不是 evaluator：那边有 4 个
            # weaken 返回点，补 4 次迟早漏一个（2026-09-13，审计 1.3）。
            logger.info(
                "Attention 松开 %s ×%.1f：%s",
                decision.subject, decision.factor, decision.reason,
            )
            self.registry.weaken(decision.subject, decision.factor, now)
        else:
            self.registry.upsert(
                decision.subject,
                decision.strength,
                kind=decision.kind,
                decay=decision.decay,
                event=event,
                summary=decision.summary,
                now=now,
            )
        self._persist()
        return decision

    def prune(self, now: datetime | None = None) -> list[str]:
        """清掉已经淡到地板以下的关心，并落盘。"""
        dead = self.registry.prune(now)
        if dead:
            self._persist()
        return dead

    # ------------------------------------------------------------ 内部

    def _persist(self) -> None:
        if self.store is None:
            return
        try:
            self.store.save(self.registry)
        except Exception:  # noqa: BLE001
            # 落盘失败不该让这一轮处理失败 —— 内存里的 Registry 还是对的，
            # 最坏是重启后丢掉这次变更。但必须吼出来
            logger.exception("Attention 落盘失败")
