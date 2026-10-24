"""室内温湿度 —— 三个房间的米家温湿度计，写进 World Model（embodied v0.2，2026-10-24）。

只收事实，不出念头（poll 永远返回 []）：判断归闸门（`embodied/validator.py`），
表达归 Core。挂在 Care 快循环上（一分钟一跳），自己节流到 10 分钟读一次。

为什么不是「他想起来才查」：电热毯事故的根因就是动作从非家居对话里长出来，
环境没在场。温度得在他动手**之前**就躺在 World Model 里。

数据走小米云（HA 的 xiaomi_miot 集成轮询），不是本地蓝牙：
  · 读到 unavailable / unknown / 不是数字 → 那个房间这次不写，**不拿旧值顶**
  · 一个房间都没读到 → 这次不写；闸门那边超过 30 分钟没新值就当不知道
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from embodied.device_model import ROOM_SENSORS
from embodied.validator import INDOOR_TYPE

logger = logging.getLogger(__name__)

#: 多久读一次。温度变化慢，小米云那头本来也就几分钟才更新一次
EVERY = timedelta(minutes=10)


def _num(state: Any) -> float | None:
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


class IndoorClimateSource:
    """读 HA 的温湿度计 → `world.observe("ha", "indoor_climate", {"rooms": {...}})`。"""

    name = "indoor_climate"

    def __init__(self, client: Any, world: Any,
                 sensors: dict[str, dict[str, str]] | None = None) -> None:
        #: 指向 HA 的 RestClient（同 PresenceSource）
        self.client = client
        self.world = world
        self.sensors = sensors if sensors is not None else ROOM_SENSORS
        self._last: datetime | None = None
        #: 上一轮缺了哪些房间 —— 只在变化时打日志，一分钟一跳不能刷屏
        self._missing: tuple[str, ...] = ()

    def poll(self, now: datetime) -> list:
        if self.world is None:
            return []
        if self._last is not None and now - self._last < EVERY:
            return []
        self._last = now
        rooms: dict[str, dict[str, float]] = {}
        for room, eids in self.sensors.items():
            # 温度读不到就不读湿度了：HA 断了时一个房间少等一次 8 秒超时
            #（这跳和醋意 / 位置共用一条快循环）
            temp = self._read(eids["temp"])
            if temp is None:
                continue
            rooms[room] = {"temp": temp}
            hum = self._read(eids["humidity"]) if eids.get("humidity") else None
            if hum is not None:
                rooms[room]["humidity"] = hum
        missing = tuple(r for r in self.sensors if r not in rooms)
        if missing != self._missing:
            if missing:
                logger.warning("室内温湿度：这次读不到 %s（不写旧值，闸门会当不知道）",
                               "、".join(missing))
            else:
                logger.info("室内温湿度：%d 个房间都读到了", len(rooms))
            self._missing = missing
        if not rooms:
            return []
        try:
            self.world.observe(
                source="ha", type=INDOOR_TYPE, observed={"rooms": rooms},
                observed_at=now,
                # 一个十分钟格子只存一条：重启后立刻再读也不重复
                dedup_key=f"{INDOOR_TYPE}/{now:%Y%m%d%H}{now.minute // 10}",
            )
        except Exception:  # noqa: BLE001
            logger.exception("室内温湿度写 World Model 失败，这轮跳过")
        return []

    def _read(self, eid: str) -> float | None:
        r = self.client.get(f"/api/states/{eid}")
        if not r.ok:
            logger.debug("室内温湿度：读 %s 失败：%s", eid, r.error)
            return None
        return _num((r.data or {}).get("state"))
