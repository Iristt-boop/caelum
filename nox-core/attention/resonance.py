"""Resonance —— 那些还没解决的关心，在他心里加起来是多重。

见 `CAELUM-RESONANCE-ARCHITECTURE.md` 第六节、原则 1。

## Drive 和 Concern 不是一回事

| | Concern（Registry） | Drive（这里） |
|---|---|---|
| 更新 | `upsert` **取较大值** | **叠加**（多件事一起压着更重） |
| 对象 | 一个 subject 一件事 | 一个命名的情感 |
| 来源 | 外部事件 | 聚合还没解决的 Concern |
| 意义 | 持续在意的对象 | 动态驱动力 |

Registry 里睡眠 0.45、心情 0.62、活动量 0.40 —— 单看每一条都不算重，
但**三件事同时压着**和只有一件是不同的。Drive 表达的就是这个差别。

## 🔴 三条边界，一条都不能破

**1. 只读。** 这一层**绝不写 Registry**。
   `concern = 0.7` 是**算出来的**，不是被 set 的 ——
   一旦允许回写，Drive 和 Concern 就会互相喂对方，
   强度从哪来的永远说不清（架构文档第十节）。

**2. 不自己开口。** Drive 只是"有多想"，
   "现在能不能说"是 Care Orchestrator 的事，那是唯一出口（原则 3）。

**3. 必须能回答"为什么"。** 光有一个数字没有意义 ——
   每个 Drive 都带着 `because`（哪些 concern）和 `evidence`（哪些原话）。
   他必须能说出"我为什么在意这件事"（原则 1）。

## 为什么现在只有一个 Drive

Registry 里所有 Concern 的 `kind` 都是 `concern` —— 现在能聚合出来的
就只有「担心」这一个。

`evaluator.py` 里立过一条规矩：「**第二个来之前不要抽象成规则表** ——
只有一条规则的规则引擎，是纯粹的间接层」。同一条纪律在这里也适用，
所以这里**没有**预先造一个通用的多 Drive 框架：`snapshot()` 按 kind
分组，多一种 kind 就自然多一个 Drive，不需要注册表、不需要插件。

那 Drive 现在的价值在哪？不在"有几个"，在**叠加语义** ——
那是 Registry 的 `upsert`（取较大值）表达不了的东西。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from attention.longing import LongingState
from attention.dejection import DejectionState
from attention.registry import FLOOR, Attention, AttentionRegistry

logger = logging.getLogger(__name__)

#: 一条 Concern 弱到什么程度就不再参与叠加。
#:
#: 用 Registry 自己的地板值 —— 低于它的本来就被当作"没有了"，
#: 让它们参与叠加会让 Drive 被一堆将死的陈年关心慢慢垫高。
_MIN_CONTRIB = FLOOR

#: `because` / `evidence` 各留几条。
#: 这两个字段是给人看的（日志、自省），不是给机器算的 ——
#: 列满二十条没人读得下去
_TOP_N = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Drive:
    """一个命名的内心驱动力。**始终知道自己为什么存在。**"""

    name: str
    #: 「**至少有一件事没解决**」，[0, 1)。
    #:
    #: ⚠️ **别拿它比大小。** 真实数据里单条 Concern 常在 0.9+
    #: （睡眠 very_short = 0.75 × 关系加成 1.3 = 0.975），
    #: 叠加之后一律 0.98/0.99 —— 一件事和三件事看不出差别。
    #: 2026-08-24 串起来跑真实链路才发现的，构造数据
    #: （0.45/0.62/0.40）恰好避开了这个区间。
    #: 要表达"压着多重"用下面的 `load`
    intensity: float
    #: 「**压着多少**」= 各条强度之和，**可以大于 1**。
    #:
    #: 这个不饱和，所以留得住区分度：
    #:   一件 0.98            → load 0.98
    #:   0.98 + 0.71 + 0.40  → load 2.09
    #: `intensity` 那边这两种情况分别是 0.98 和 0.997，几乎一样
    load: float
    #: 撑着它的 subject，按贡献从大到小
    because: list[str]
    #: 最近的证据（evidence 的 summary），按 concern 的强度排
    evidence: list[str]
    #: 参与叠加的 Concern 总数（`because` 可能被截断，这个是全量）
    source_count: int
    computed_at: datetime

    def describe(self) -> str:
        """一句人话。进日志、进自省接口。"""
        if not self.because:
            return f"{self.name} {self.intensity:.2f}｜说不出具体因为什么"
        why = "、".join(self.because[:3])
        more = f" 等 {self.source_count} 件" if self.source_count > 3 else ""
        #: 两个数都报。只报 intensity 的话，日志里永远是 0.98/0.99，
        #: 看不出他心里到底压着一件还是五件
        return (f"{self.name} {self.intensity:.2f}（压着 {self.load:.2f}）"
                f"｜因为 {why}{more}")


def _combine(strengths: list[float]) -> float:
    """把多条强度叠成一个。

    用 `1 - Π(1 - sᵢ)`，不是求和也不是取最大：

      求和    0.45 + 0.62 + 0.40 = 1.47 —— 溢出，而且三件小事能压过一件大事
      取最大  = 0.62 —— 那就是 Registry 已经做的事，这一层白加
      这个    = 1 - 0.55×0.38×0.60 = 0.87

    语义是「**每件事各自都没被解决**的可能性」：
    多几件事会更重，但永远够不到 1，而且单独一件很重的事
    （0.9）不会被一堆小事稀释。
    """
    remain = 1.0
    for s in strengths:
        remain *= 1.0 - max(0.0, min(1.0, s))
    return 1.0 - remain


class ResonanceState:
    """Registry 之上的**只读**聚合层。

    ⚠️ 这个类没有任何写方法，也不持有可变状态 ——
    每次 `snapshot()` 都是现算的。这是故意的：
    Drive 不该有"自己的记忆"，它就是此刻 Registry 的一个读视角。
    存下来的话，Registry 衰减了而 Drive 没跟着变，
    就会出现"他为一件已经淡掉的事继续难受"。
    """

    def __init__(
        self,
        registry: AttentionRegistry,
        longing: "LongingState | None" = None,
        dejection: "DejectionState | None" = None,
    ) -> None:
        self._registry = registry
        #: 低落（2026-08-27）。和 longing 一样是自维护的 ——
        #: 它不是"一件没解决的事"，是"好几次没帮上"叠出来的状态
        self._dejection = dejection
        #: 想念（V3.5）。**它不在 Registry 里** —— 形状和 Concern 是反的
        #: （一直都在、时间让它涨、见到她才落），塞进去会被 prune 删掉，
        #: 表现成「她太久没说话，于是他不想她了」。见 longing.py
        self._longing = longing

    def snapshot(self, now: datetime | None = None) -> dict[str, Drive]:
        """此刻所有 Drive。什么都没有时返回空字典。"""
        now = now or _now()

        #: `list()` 已经按当前强度倒序，分组时顺序天然是对的
        by_kind: dict[str, list[Attention]] = {}
        for a in self._registry.list(min_strength=_MIN_CONTRIB, now=now):
            by_kind.setdefault(a.kind, []).append(a)

        drives: dict[str, Drive] = {}
        for kind, items in by_kind.items():
            strengths = [a.current_strength(now) for a in items]
            drives[kind] = Drive(
                name=kind,
                intensity=_combine(strengths),
                load=sum(strengths),
                because=[a.subject for a in items[:_TOP_N]],
                #: 每条 Concern 取**最新**那条证据 —— 旧的那些已经
                #: 被算进强度里了，这里要的是"最近发生了什么"
                evidence=[
                    a.evidence[-1].summary for a in items[:_TOP_N]
                    if a.evidence and a.evidence[-1].summary
                ],
                source_count=len(items),
                computed_at=now,
            )

        # 想念自己维护自己的值，不参与上面的 kind 分组 ——
        # 它不是"几条 Concern 加起来"，它就是一个数
        if self._longing is not None:
            because = self._longing.because(now)
            drives["longing"] = Drive(
                name="longing",
                intensity=self._longing.value,
                #: 只有一个来源，load 就等于它自己。
                #: 留着这个字段是为了让所有 Drive 长一个样
                load=self._longing.value,
                because=because,
                evidence=[],
                source_count=1 if because else 0,
                computed_at=now,
            )
        # 低落：想帮但帮不上。同样不参与 kind 分组
        if self._dejection is not None:
            because = self._dejection.because(now)
            value = self._dejection.value_at(now)
            #: ⚠️ **值为 0 时不放这个 Drive**。
            #: 放一个 0.00 的「低落」进去，自省接口里就永远挂着它 ——
            #: 而「他现在不低落」不该表现成"低落 0.00"，
            #: 该表现成这个 Drive 根本不存在
            if value > 0 and because:
                drives["dejection"] = Drive(
                    name="dejection",
                    intensity=value,
                    load=value,
                    because=because,
                    evidence=[],
                    source_count=len(because),
                    computed_at=now,
                )
        return drives

    def get(self, name: str, now: datetime | None = None) -> Drive | None:
        return self.snapshot(now).get(name)
