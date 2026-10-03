"""位置 Source —— 她出门了 / 她到家了 / 她在外面很久了。

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

## 🔴 实际上那条链从来没实现过（2026-09-23 查到）

这里原来只在**跳变那一刻**产一个念头，之后没有任何东西再推它 ——
她出门待一整晚，他也只在出门那一下问过一句。

糖糖 2026-09-23 要的也不是「追问三次」，是**像人一样随时间变**：

    出门那一下      到哪啦 / 路上小心
    在外面 3 小时   在外面这么久了，几点回家？
    晚上 22:30 后   怎么还没回？
    过了 00:30      这么晚了要夜不归宿吗（吃醋让位给担心）

所以出门之后还会按**里程碑**再产念头（`MILESTONES`），每个里程碑
一次出门只响一次、两次之间至少隔一小时。说什么、用什么情绪归
`attention/care/her_state.py` —— 这里只报「她出门多久、位置多新」这些事实。

## 位置会过期

2026-09-22 夜里她的手机 18:59 之后再没上报过（iOS 在后台停了 HA App 的定位），
系统以为她在外面待了 14.5 小时；早上她醒来拿起手机，一次补报被当成「到家了」。
所以这里记着 `updated_at`（HA 最后一次收到她位置的时刻），
下游据此判断「这是不是旧消息」—— 旧的就别笃定地说「你回来了」。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from attention.care.signal import FOLLOWUP, CareSignal
from temporal import to_local

logger = logging.getLogger(__name__)

#: 存「上次看到她在哪」。跨重启要活着 ——
#: 不然每次重启都会把当前状态当成一次跃迁，白追一轮
STATE_KEY = "source.presence"

HOME = "home"
AWAY = "not_home"

#: 出门之后的里程碑：(名字, 出门至少多久, 本地时刻窗口)。
#: 窗口是 (起, 止) 小时，可以是小数；跨零点的写成起 > 止。None = 不看钟点。
MILESTONES: list[tuple[str, timedelta, tuple[float, float] | None]] = [
    ("long", timedelta(hours=3), None),              # 在外面这么久了
    ("late", timedelta(hours=1), (22.5, 5.0)),       # 这么晚还没回
    ("midnight", timedelta(hours=2), (0.5, 5.0)),    # 过了零点半
]
#: 两个出门念头之间至少隔多久（出门那一下也算一个）
MILESTONE_GAP = timedelta(minutes=60)


def _in_window(now: datetime, win: tuple[float, float] | None) -> bool:
    if win is None:
        return True
    local = to_local(now)
    h = local.hour + local.minute / 60
    lo, hi = win
    return lo <= h < hi if lo < hi else (h >= lo or h < hi)


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


class PresenceSource:
    """她出门/到家，以及出门之后时间的流逝。轮询 HA 的 person 实体。"""

    name = "location"

    def __init__(self, client: Any, store: Any,
                 entity: str = "person.nox") -> None:
        #: 指向 HA 的 RestClient
        self.client = client
        self.store = store
        self.entity = entity
        self._last: str | None = None
        #: 这次出门从什么时候开始（她在家时为 None）
        self.away_since: datetime | None = None
        #: HA 最后一次收到她位置的时刻 —— 判断数据新不新
        self.updated_at: datetime | None = None
        #: 这次出门已经响过的里程碑
        self._fired: list[str] = []
        #: 上一个出门念头的时刻（MILESTONE_GAP 用）
        self._last_nudge: datetime | None = None
        self._load()

    # ------------------------------------------------------------ Source

    def poll(self, now: datetime) -> list[CareSignal]:
        got = self._fetch()
        if got is None:
            return []
        state, updated = got
        prev_updated = self.updated_at
        if updated is not None:
            self.updated_at = updated

        prev, self._last = self._last, state

        # 第一次拿到状态不算跃迁 —— 否则一重启就会追一轮
        if prev is None:
            if state == AWAY and self.away_since is None:
                self.away_since = now
            self._save()
            return []
        if prev == state:
            if state == AWAY:
                return self._milestone(now)
            if updated is not None and updated != prev_updated:
                self._save()
            return []

        # 跃迁前那份位置有多旧 —— 旧得离谱的话，这次「跃迁」可能只是补报
        gap_h = None
        if prev_updated and updated:
            gap_h = round((updated - prev_updated).total_seconds() / 3600, 1)
        logger.info("位置变了：%s → %s（和上一次位置隔了 %s 小时）", prev, state,
                    "?" if gap_h is None else gap_h)

        if state == AWAY:
            self.away_since = now
            self._fired = []
            self._last_nudge = now
            self._save()
            return [CareSignal(
                source=self.name,
                subject="她出门了",
                thread_kind=FOLLOWUP,
                # 出门比随便想起她要紧：她在外面，而他什么都不知道
                urgency=0.75,
                payload={"transition": "leave_home", "gap_h": gap_h},
            )]
        if state == HOME:
            away_since, self.away_since = self.away_since, None
            self._fired, self._last_nudge = [], None
            self._save()
            return [CareSignal(
                source=self.name,
                subject="她到家了",
                thread_kind=FOLLOWUP,
                urgency=0.6,
                payload={"transition": "arrive_home", "gap_h": gap_h,
                         "away_since": away_since.isoformat() if away_since else None},
            )]
        self._save()
        return []

    def _milestone(self, now: datetime) -> list[CareSignal]:
        """她一直在外面：到了下一个里程碑就产一个念头。"""
        if self.away_since is None:
            # 老状态里没有出门时刻（这版之前就出门了）—— 从现在算，不追溯
            self.away_since = now
            self._save()
            return []
        if self._last_nudge and now - self._last_nudge < MILESTONE_GAP:
            return []
        out = now - self.away_since
        # 从最重的往回看：21:30 出门、00:40 还没回，该说的是「过零点了」，
        # 不是「在外面三小时了」—— 响了重的，轻的一并作废，不再补
        for i in range(len(MILESTONES) - 1, -1, -1):
            name, at_least, win = MILESTONES[i]
            if name in self._fired or out < at_least or not _in_window(now, win):
                continue
            for lighter, _, _ in MILESTONES[: i + 1]:
                if lighter not in self._fired:
                    self._fired.append(lighter)
            self._last_nudge = now
            self._save()
            logger.info("她在外面 %.1f 小时了：里程碑 %s", out.total_seconds() / 3600, name)
            return [CareSignal(
                source=self.name,
                subject="她在外面很久了",
                thread_kind=FOLLOWUP,
                urgency=0.7,
                payload={"transition": "still_out", "milestone": name,
                         "away_since": self.away_since.isoformat()},
            )]
        return []

    # ------------------------------------------------------------ 内部

    def _fetch(self) -> tuple[str, datetime | None] | None:
        r = self.client.get(f"/api/states/{self.entity}")
        if not r.ok:
            # 读不到就当没变化。**不猜** —— 猜错方向会平白追她一轮
            logger.debug("读不到 %s：%s", self.entity, r.error)
            return None
        data = r.data or {}
        state = (data.get("state") or "").strip()
        # unknown = 没有 tracker 在跑，不是「出门了」
        if state in ("", "unknown", "unavailable"):
            return None
        # HA 2024.3+ 有 last_reported（同值上报也刷新）；老版本只有 last_updated
        updated = _parse(data.get("last_reported")) or _parse(data.get("last_updated"))
        return state, updated

    def _save(self) -> None:
        try:
            self.store.set_source_state(STATE_KEY, {
                "last": self._last,
                "away_since": self.away_since.isoformat() if self.away_since else None,
                "updated_at": self.updated_at.isoformat() if self.updated_at else None,
                "fired": self._fired,
                "last_nudge": self._last_nudge.isoformat() if self._last_nudge else None,
            })
        except Exception:  # noqa: BLE001
            logger.warning("位置状态没存住")

    def _load(self) -> None:
        try:
            raw = self.store.get_source_state(STATE_KEY) or {}
            self._last = raw.get("last")
            self.away_since = _parse(raw.get("away_since"))
            self.updated_at = _parse(raw.get("updated_at"))
            self._fired = list(raw.get("fired") or [])
            self._last_nudge = _parse(raw.get("last_nudge"))
        except Exception:  # noqa: BLE001
            logger.warning("位置状态读不出来，从头开始")
            self._last = None

    # ------------------------------------------------------------ 观察

    def snapshot(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "last_seen": self._last,
            "away_since": self.away_since.isoformat() if self.away_since else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "milestones_fired": self._fired,
        }
