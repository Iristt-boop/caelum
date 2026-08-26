"""日度指标 Source —— 步数、HRV。

和 `SleepSource` 是同一套结构（Temporal Filter + 先记事实再判断），
所以抽成一个通用类，两个指标只是配置不同。

## 为什么要有第二、第三个感知源（糖糖 2026-08-19）

在这之前 Attention 只有睡眠一个**感知型**源 —— 另外几条（固定时间、
纸条、待办到点）都是时钟驱动的，不算他在观察她。
World Model 也因此只有一个写入者，「事实的收口」名不副实。

## ⚠️ 阈值一律相对**她自己的基线**，不是通用人群

`context/providers/health.py:25` 和 `sleep.py` 都立过这条。这里再犯一次
的代价更大：她的日均步数只有两千多，拿「每天一万步」那套去判，
**他会天天说她不动** —— 那正是最该避免的结果。

基线是从她自己最近 14 天的数据量出来的（2026-08-19 实测）：

    步数   2252 / 2358 / 1091 / 2360 / 391 …   → 基线 2300
    HRV    79 / 58 / 89 / 79 …                 → 基线 78

这两个数**会过时**。她的生活变了（比如开始规律健身），就该重算 ——
`BASELINE` 那两行就是给人改的，不是常量表。

## 没接的两个指标，和为什么

    体重      health.db 里 14 天**一条都没有**，HealthKit 那条同步是坏的
    静息心率  只有 8/14，而且 49→66→51→50 这种跳法不像生理变化，
              像采样口径不一致。拿它当「担心你身体」的依据会误报

体重和经期改走「他主动记」那条路（`tools/record.py`），不在这里。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from attention.events import ExperienceEvent

logger = logging.getLogger(__name__)


class _StateStore(Protocol):
    def get_source_state(self, key: str) -> dict[str, Any] | None: ...
    def set_source_state(self, key: str, value: dict[str, Any]) -> None: ...


class _Provider(Protocol):
    def get_state(self, turn: Any = None, force_refresh: bool = False) -> dict[str, Any]: ...


@dataclass(frozen=True)
class MetricSpec:
    """一个日度指标的全部配置。"""

    #: Source 的名字，也是 source_state 的 key 后缀
    name: str
    #: HealthProvider 端出来的字段名
    field: str
    #: 事件类型（进 Registry 的话题就是它）
    event_type: str
    #: World Model 里的事实类型
    fact_type: str
    #: **她自己的**基线
    baseline: float
    #: 低 / 很低的比例阈值
    low_ratio: float
    very_low_ratio: float
    unit: str
    #: 高于基线多少算「明显偏高」。None = 不关心偏高
    #: （步数高是好事不用管；HRV 高也是好事）
    high_ratio: float | None = None


#: 步数。基线 2300 来自她最近 14 天（见模块开头）。
#: 只判「偏低」—— 走得多是好事，不需要他操心
STEPS = MetricSpec(
    name="steps", field="steps",
    event_type="activity_changed", fact_type="daily_steps",
    baseline=2300, low_ratio=0.60, very_low_ratio=0.30, unit="step",
)

#: HRV。基线 78 ms 来自她最近 14 天。
#: HRV 走低 = 压力大 / 没恢复过来，比静息心率可靠得多
HRV = MetricSpec(
    name="hrv", field="hrv_ms",
    event_type="recovery_changed", fact_type="hrv",
    baseline=78, low_ratio=0.75, very_low_ratio=0.60, unit="ms",
)


def classify(value: float | None, spec: MetricSpec) -> str:
    """数值 → 等级。

    拿不到数据返回 `unknown`，**不是 normal** ——
    「不知道」和「正常」混起来会让恢复检测出错（同 sleep.py）。
    """
    if not value or value <= 0:
        return "unknown"
    ratio = value / spec.baseline
    if ratio < spec.very_low_ratio:
        return "very_low"
    if ratio < spec.low_ratio:
        return "low"
    if spec.high_ratio is not None and ratio > spec.high_ratio:
        return "high"
    return "normal"


class DailyMetricSource:
    """一个日度指标的 Temporal Filter。"""

    def __init__(self, spec: MetricSpec, provider: _Provider,
                 store: _StateStore, world: Any = None) -> None:
        self.spec = spec
        self.provider = provider
        self.store = store
        self.world = world

    @property
    def state_key(self) -> str:
        return f"source.{self.spec.name}"

    def poll(self, now: datetime | None = None) -> ExperienceEvent | None:
        try:
            state = self.provider.get_state()
        except Exception:  # noqa: BLE001
            # Provider 基类本来就会退回旧数据或标 available:False，
            # 能抛到这儿说明它自己崩了。不该让 Attention 跟着挂
            logger.exception("拉健康数据失败（%s）", self.spec.name)
            return None

        if state.get("available") is False or not state.get("has_data"):
            return None

        # 活动数据的自然日是 `date`（不是 sleep_date —— 那两个差一天，
        # 见 health.py 的模块注释）
        day = state.get("date")
        value = state.get(self.spec.field)
        level = classify(value, self.spec)
        if level == "unknown":
            return None

        # ⚠️ **先记事实，再判断值不值得关心。顺序不能反。**
        # 下面那些 return None 是给 Attention 的（「不是新消息」），
        # 但对 World Model 来说每天的数据都是事实 ——
        # 「她这周每天都只走一千步」正是靠这些「没有变化」的记录才看得出来
        self._record(day, value, now)

        prev = self.store.get_source_state(self.state_key) or {}
        # 同一天已经处理过。HealthProvider 的 ttl 是 6 小时，
        # 一天会 poll 好几次，不挡住就会反复产生事件
        if day and day == prev.get("last_date"):
            return None

        prev_level = prev.get("last_level")
        self.store.set_source_state(self.state_key,
                                    {"last_date": day, "last_level": level})

        # 状态没变 = 不是新消息。「连续几天不动」由 Attention 的强度
        # 不衰减来表达，不靠重复发事件
        if level == prev_level:
            logger.debug("%s 仍是 %s，不产生事件", self.spec.name, level)
            return None

        if level == "normal":
            if prev_level in (None, "normal"):
                return None
            subtype = "recovered"
        else:
            subtype = level

        logger.info("%s 变化：%s → %s（%s %s）",
                    self.spec.name, prev_level, level, value, self.spec.unit)
        return ExperienceEvent(
            source="health",
            type=self.spec.event_type,
            subtype=subtype,
            payload={
                "date": day,
                "value": value,
                "baseline": self.spec.baseline,
                "unit": self.spec.unit,
                "level": level,
                "prev_level": prev_level,
            },
            timestamp=now or datetime.now().astimezone(),
        )

    def _record(self, day: Any, value: Any, now: datetime | None) -> None:
        """记进 World Model。**失败不许影响 Attention** —— 关心她是主线，
        留档是副产品（同 sleep.py）。"""
        if self.world is None:
            return
        try:
            self.world.observe(
                source="health",
                type=self.spec.fact_type,
                observed={"value": value, "unit": self.spec.unit, "date": day},
                observed_at=now or datetime.now().astimezone(),
                # 自然日就是天然的幂等键：同一天 poll 多少次都只存一条
                dedup_key=f"{self.spec.fact_type}/{day}" if day else None,
            )
        except Exception:  # noqa: BLE001
            logger.exception("写 World Model 失败（%s），不影响这次判断", self.spec.name)


def build_all(provider: _Provider, store: _StateStore,
              world: Any = None) -> list[DailyMetricSource]:
    """步数 + HRV。要加第三个指标，在这里多写一行。"""
    return [DailyMetricSource(spec, provider, store, world) for spec in (STEPS, HRV)]
