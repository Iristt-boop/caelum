"""位置 Source —— 她出门了 / 她到家了。

## 数据是自动来的（2026-08-18 实测确认）

线上拉的原始数据：

    person.nox          = home        ← 聚合自 device_tracker.iris
    device_tracker.iris = home
      坐标 34.735021, 113.598009 · 精度 5 米 · source_type = gps
    zone.home = 1

**她不用点任何按钮。** HA 官方 App 的 GPS 在跨地理围栏时自己上报，
`device_tracker` 会翻成 `not_home`。（Caelum PWA 那条要她主动点，
那是补充数据源，不是主力。）

## 那为什么以前没用起来

三样都缺：

1. **没人盯着跃迁。** `LocationProvider` 只在她说话时按 TTL 拉一次，
   没有任何东西比对「上次 home、这次 not_home」
2. **trigger 值对不上。** HA 那条产出 `ha_home`，而 provider 认的是
   `arrive_home` / `leave_home` —— 两个值从来没接上过
3. **它是 Context Provider，不是 Care Source。** 属于「他回答时能看一眼」，
   不属于「能把他叫醒」

这个模块补第 1 和第 3 条：**盯着跃迁，跃迁了就产出一个念头。**

## 出门是一条追问链，不是一条消息

糖糖 2026-08-18 定的：

    T+0   她：我出门了 / HA：not_home
    T+5   到哪啦？
    T+10  没回 → 查位置 / 交通
    T+30  你还在外面吗？

这三条**是一次关心，不是三次** —— 所以走同一条 followup 型 CareThread，
链内步进由 `SourcePolicy.min_step_gap_min` 控制，不吃新链的额度。
她一回话，链就关（followup 的语义）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from attention.care.signal import FOLLOWUP, CareSignal

logger = logging.getLogger(__name__)

#: 存「上次看到她在哪」。跨重启要活着 ——
#: 不然每次重启都会把当前状态当成一次跃迁，白追一轮
STATE_KEY = "source.presence"

HOME = "home"
AWAY = "not_home"


class PresenceSource:
    """她出门/到家。轮询 HA 的 person 实体，只报**变化**。"""

    name = "location"

    def __init__(self, client: Any, store: Any,
                 entity: str = "person.nox") -> None:
        #: 指向 HA 的 RestClient
        self.client = client
        self.store = store
        self.entity = entity
        self._last: str | None = None
        self._load()

    # ------------------------------------------------------------ Source

    def poll(self, now: datetime) -> list[CareSignal]:
        state = self._fetch()
        if state is None:
            return []

        prev, self._last = self._last, state

        # 第一次拿到状态不算跃迁 —— 否则一重启就会追一轮
        if prev is None:
            self._save()
            return []
        if prev == state:
            return []

        self._save()
        logger.info("位置变了：%s → %s", prev, state)

        if state == AWAY:
            return [CareSignal(
                source=self.name,
                subject="她出门了",
                thread_kind=FOLLOWUP,
                # 出门比随便想起她要紧：她在外面，而他什么都不知道
                urgency=0.75,
                payload={"transition": "leave_home"},
            )]
        if state == HOME:
            return [CareSignal(
                source=self.name,
                subject="她到家了",
                thread_kind=FOLLOWUP,
                urgency=0.6,
                payload={"transition": "arrive_home"},
            )]
        return []

    # ------------------------------------------------------------ 内部

    def _fetch(self) -> str | None:
        r = self.client.get(f"/api/states/{self.entity}")
        if not r.ok:
            # 读不到就当没变化。**不猜** —— 猜错方向会平白追她一轮
            logger.debug("读不到 %s：%s", self.entity, r.error)
            return None
        state = ((r.data or {}).get("state") or "").strip()
        # unknown = 没有 tracker 在跑，不是「出门了」
        if state in ("", "unknown", "unavailable"):
            return None
        return state

    def _save(self) -> None:
        try:
            self.store.set_source_state(STATE_KEY, {"last": self._last})
        except Exception:  # noqa: BLE001
            logger.warning("位置状态没存住")

    def _load(self) -> None:
        try:
            self._last = (self.store.get_source_state(STATE_KEY) or {}).get("last")
        except Exception:  # noqa: BLE001
            self._last = None

    # ------------------------------------------------------------ 观察

    def snapshot(self) -> dict[str, Any]:
        return {"entity": self.entity, "last_seen": self._last}
