"""World Model 对外的三个接口：`observe` / `get_state` / `query`。

设计上刻意很薄 —— 它**不理解**任何东西，只负责收下事实、
按时效标注状态、按来源返回证据。判断归 Attention，表达归 Core。

## 为什么 `observe()` 要和「值不值得关心」分开

`attention/sources/sleep.py` 现在把两件事混在一个 `poll()` 里：
状态没变就 `return None`，整条数据一起扔了。于是「她这周每天都睡 6 小时」
这个事实**一条都没留下** —— 因为它「没有变化」。

World Model 的规矩相反：**每天的事实都收，哪怕它平淡无奇。**
平淡本身就是趋势的一部分。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from world_model.store import WorldStore
from world_model.types import (
    FRESH,
    STALE,
    UNKNOWN,
    Evidence,
    Observation,
    State,
)

logger = logging.getLogger(__name__)

#: 各类事实多久算过期。超了就是 stale —— 保留，但不许当成当前值说出去。
#:
#: 睡眠给 36 小时：数据一天一条，她偶尔晚起或者没戴表会漏，
#: 给一天半的宽限；再久就该说「最近没有新数据」而不是报旧数。
DEFAULT_TTL = timedelta(hours=36)
TTL: dict[str, timedelta] = {
    "sleep_duration": timedelta(hours=36),
    # ---- 三块共活动（Topic_Pool §3.1.2，2026-08-31）----
    # 她不是天天读书：几天没动才算「停了」，36 小时太紧。
    # 过期只是状态变 stale（「最后一次记录是 X 月 X 日」），事实永远在
    "reading_progress": timedelta(days=4),
    # 「正在看」是个很短的状态 —— 关掉播放器俩小时就不该再算
    "watching_session": timedelta(hours=2),
    # 「昨晚一起听的那首」过一天就成回忆了
    "listening_together": timedelta(hours=36),
}


class WorldModel:
    """事实的收口和出口。纯同步（`context/base.py:25`）。"""

    def __init__(self, path: str | Path) -> None:
        self.store = WorldStore(path)

    # ------------------------------------------------------------ 写

    def observe(self, source: str, type: str, observed: dict[str, Any],
                observed_at: datetime, *, confidence: float = 1.0,
                dedup_key: str | None = None) -> Observation | None:
        """收下一条事实，顺带刷新对应的 State。

        返回 None 表示 `dedup_key` 撞了（同一件事已经存过）——
        **这不是错误**，是幂等生效了。Attention 心跳一天会跑几十次，
        没有这个每次都会重复写。
        """
        obs = Observation(
            source=source, type=type, observed=observed,
            observed_at=observed_at, confidence=confidence,
            dedup_key=dedup_key,
        )
        if not self.store.add_observation(obs):
            logger.debug("事实已存在，跳过：%s / %s", type, dedup_key)
            return None

        # State 是派生的：拿最新那条观察刷新它。
        # ⚠️ 只在「更新」时刷 —— 补录一条旧数据不该把当前状态改回去
        cur = self.store.get_state_row(type)
        if cur is None or obs.observed_at >= cur.observed_at:
            self.store.put_state(State.from_observation(obs, self._ttl(type)))
        logger.info("记下事实：%s = %s（%s）", type, observed, source)
        return obs

    # ------------------------------------------------------------ 读

    def get_state(self, type: str, now: datetime | None = None) -> State:
        """现在是什么样。**永远返回 State，不返回 None** ——
        「不知道」也是一种明确的状态（`unknown`），比 None 好用：
        调用方不用到处写 if，而且 `describe()` 会说出「我不知道」，
        不会把没有数据说成正常。
        """
        now = now or datetime.now(timezone.utc)
        st = self.store.get_state_row(type)
        if st is None:
            return State(type=type, value={}, observed_at=now, status=UNKNOWN)
        st.status = FRESH if (now - st.observed_at) <= self._ttl(type) else STALE
        return st

    def query(self, type: str, *, days: int | None = None,
              limit: int = 30, now: datetime | None = None) -> list[Evidence]:
        """历史事实，**新的在前**，每条都带来源。

        趋势判断（「连续 7 天下降」）就是拿它做的 —— 这也是 World Model
        存在的主要理由：`evaluator.py` 原来只看单次，因为它无处可查。
        """
        now = now or datetime.now(timezone.utc)
        since = now - timedelta(days=days) if days else None
        out: list[Evidence] = []
        for obs in self.store.recent(type, limit=limit, since=since):
            out.append(Evidence(
                content=self._describe(obs),
                kind="observed",
                source=obs.source,
                observed_at=obs.observed_at,
                confidence=obs.confidence,
                reference=f"{obs.source}://{obs.type}/{obs.dedup_key or obs.id}",
                raw=obs.observed,
            ))
        return out

    def stats(self) -> dict:
        return self.store.stats()

    # ------------------------------------------------------------ 内部

    def _ttl(self, type: str) -> timedelta:
        return TTL.get(type, DEFAULT_TTL)

    @staticmethod
    def _describe(obs: Observation) -> str:
        v = obs.observed.get("value")
        u = obs.observed.get("unit", "")
        when = obs.observed_at.astimezone().strftime("%m-%d")
        return f"{when} {obs.type} {v}{u}".strip()
