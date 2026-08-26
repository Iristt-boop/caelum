"""睡眠 Source —— 把每天一条的健康数据变成「有意义的变化」。

## Temporal Filter 不是降采样，是语义压缩

原始数据是每天一条睡眠时长。如果每次 poll 都产生一个事件，
Registry 会被同一件事刷屏，Scheduler 就会天天想开口。

这里只在**状态发生变化**时产生事件：

    normal → short        产生（她开始睡不够了）
    short  → short        **不产生**（还是那件事，不是新消息）
    short  → very_short   产生（恶化了）
    short  → normal       产生 recovered（Evaluator 目前会忽略，但留着）

「她连续三天睡不好」这件事不靠重复发事件来表达，
靠的是 Attention 的强度**不再衰减** —— 那是 Registry 的职责。

## ⚠️ 阈值必须比她自己的基线，不能用通用人群的

`context/providers/health.py:25` 立过这条规矩：
`check_health_warnings` 那几个阈值（睡眠 <360 分钟之类）是通用人群的，
不是糖糖的基线。

**尤其是深睡**：CLAUDE.md 写着她「深睡偏少」——那是她的**常态**。
拿通用阈值去判，她每天都会触发一次关心，那正是我们最想避免的结果。
所以这里**只看总时长**，深睡不单独触发。

基线 432 分钟 = 7.2 小时，来源同样是 CLAUDE.md（她的平均睡眠）。

## 同一觉不重复处理

HealthProvider 的 ttl 是 6 小时，一天会被 poll 好几次，但拿到的是同一条数据。
所以要记住 `last_sleep_date` —— 同一个归属日直接跳过，
否则同一觉会反复产生事件。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol

from attention.events import ExperienceEvent

logger = logging.getLogger(__name__)

#: 她自己的平均睡眠（CLAUDE.md）。所有判断都相对这个数，不是相对 8 小时。
BASELINE_MIN = 432          # 7.2 小时

#: 相对基线的比例阈值。
SHORT_RATIO = 0.85          # ≈ 6.1 小时
VERY_SHORT_RATIO = 0.70     # ≈ 5.0 小时

#: 存 Temporal Filter 状态用的 key
STATE_KEY = "source.sleep"


class _StateStore(Protocol):
    """只要能存取一小块 JSON 就行 —— 这样测试不用真开数据库。"""

    def get_source_state(self, key: str) -> dict[str, Any] | None: ...
    def set_source_state(self, key: str, value: dict[str, Any]) -> None: ...


class _Provider(Protocol):
    def get_state(self, turn: Any = None, force_refresh: bool = False) -> dict[str, Any]: ...


def classify(sleep_min: float | None, baseline: int = BASELINE_MIN) -> str:
    """睡眠时长 → 等级。拿不到数据返回 unknown（不是 normal ——
    「不知道」和「正常」混起来会让恢复检测出错）。"""
    if not sleep_min or sleep_min <= 0:
        return "unknown"
    ratio = sleep_min / baseline
    if ratio < VERY_SHORT_RATIO:
        return "very_short"
    if ratio < SHORT_RATIO:
        return "short"
    return "normal"


class SleepSource:
    """从 HealthProvider 拉数据，产生睡眠状态变化事件。

        src = SleepSource(health_provider, store)
        event = src.poll()      # 没有变化就返回 None
        if event:
            engine.handle(event)
    """

    def __init__(self, provider: _Provider, store: _StateStore,
                 world: Any = None) -> None:
        self.provider = provider
        self.store = store
        #: World Model。可以为 None —— 没接上时这条 Source 照常工作，
        #: 只是不留历史（Attention 不该因为记账系统没起来就哑掉）
        self.world = world

    def poll(self, now: datetime | None = None) -> ExperienceEvent | None:
        """看一眼最新的睡眠数据，有变化就产生事件。"""
        try:
            state = self.provider.get_state()
        except Exception:  # noqa: BLE001
            # Provider 基类本来就会退回旧数据或标 available: False，
            # 能抛到这儿说明它自己崩了。不该让 Attention 跟着挂
            logger.exception("拉睡眠数据失败")
            return None

        if state.get("available") is False or not state.get("has_data"):
            return None

        sleep_date = state.get("sleep_date")
        sleep_min = state.get("sleep_min")
        level = classify(sleep_min)
        if level == "unknown":
            return None

        # ⚠️ **先记事实，再判断值不值得关心。顺序不能反。**
        #
        # 下面那些 `return None`（同一天、状态没变）是给 Attention 用的 ——
        # 「不是新消息」。但对 World Model 来说，**每天的数据都是事实**，
        # 哪怕平淡无奇：「她这周每天都睡 6 小时」正是靠这些「没有变化」
        # 的记录才看得出来。
        #
        # 分岔放在这儿之前的话，趋势永远建不起来 —— 这正是 evaluator
        # 至今只能看单次的原因（架构文档 10.1）。
        self._record(sleep_date, sleep_min, state, now)

        prev = self.store.get_source_state(STATE_KEY) or {}
        prev_date = prev.get("last_sleep_date")
        prev_level = prev.get("last_level")

        # 同一觉已经处理过了。ttl 6 小时意味着一天会 poll 好几次，
        # 不挡住的话同一晚会反复产生事件
        if sleep_date and sleep_date == prev_date:
            return None

        self.store.set_source_state(STATE_KEY, {
            "last_sleep_date": sleep_date,
            "last_level": level,
        })

        # 状态没变 = 不是新消息。「连续几天睡不好」由 Attention 的
        # 强度不衰减来表达，不靠重复发事件
        if level == prev_level:
            logger.debug("睡眠状态仍是 %s，不产生事件", level)
            return None

        if level == "normal":
            # 之前也正常（或者第一次跑）就没什么好说的
            if prev_level in (None, "normal"):
                return None
            subtype = "recovered"
        else:
            subtype = level

        event = ExperienceEvent(
            source="health",
            type="sleep_quality_changed",
            subtype=subtype,
            payload={
                "sleep_date": sleep_date,
                "sleep_min": sleep_min,
                "baseline_min": BASELINE_MIN,
                "level": level,
                "prev_level": prev_level,
                # 深睡只是**带上供参考**，不参与判断 —— 她深睡本来就偏少
                "deep_sleep_min": state.get("deep_sleep_min"),
            },
            timestamp=now or datetime.now().astimezone(),
        )
        logger.info("睡眠状态变化：%s → %s（%s 分钟）", prev_level, level, sleep_min)
        return event

    def _record(self, sleep_date: Any, sleep_min: Any,
                state: dict[str, Any], now: datetime | None) -> None:
        """把这一晚记进 World Model。**失败不许影响 Attention。**

        记账系统出问题不该让他哑掉 —— 关心她是主线，留档是副产品。
        所以整段包在 try 里，出错只记日志。
        """
        if self.world is None:
            return
        try:
            # 归属日就是天然的幂等键：同一晚不管 poll 多少次都只存一条
            key = f"sleep/{sleep_date}" if sleep_date else None
            self.world.observe(
                source="health",
                type="sleep_duration",
                observed={
                    "value": round(sleep_min / 60, 2),
                    "unit": "hour",
                    "minutes": sleep_min,
                    # 深睡带上供查询，但**不参与判断** —— 她深睡本来就偏少
                    "deep_sleep_min": state.get("deep_sleep_min"),
                    "sleep_date": sleep_date,
                },
                observed_at=now or datetime.now().astimezone(),
                dedup_key=key,
            )
        except Exception:  # noqa: BLE001
            logger.exception("写 World Model 失败，不影响这次判断")
