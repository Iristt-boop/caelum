"""面前的 Source —— 她坐到他面前了 / 她离开了。

数据来自 stackchan 的人脸识别 tracker（gateway → nox-core 的
``/api/nox/perception/presence``，push 不是 poll）。它只认**真脸检测**
（不是上游 FaceTracker 那种帧差法），所以「她来了」这条线头是有分量的：
她在看他。

## 和位置源（presence.py）的两点不同

1. **push 型。** 位置是每 60 秒轮询比对跃迁；这里事件是 tracker 推过来
   的（arrived / left / looking），本源只管收下、记状态、在「她来了」
   那一刻产出一个念头。要不要说、现在说不说，照旧归 Orchestrator。
2. **会过期，而且过期得快。** 位置源吃过「数据断了 14 小时还当成
   在外面」的亏（2026-09-22）；这里的病是一样的而且更急 —— tracker
   一断，「她在面前」就成了旧闻。所以 present 状态上挂了
   ``is_fresh()``：超过 :data:`FRESH_WINDOW` 没有任何新事件（tracker
   每分钟会推一次 looking 心跳），就当不知道，不许再笃定。

「她在吃饭在工作就别打扰」这类判断不在这里 —— her_state / waker
以后想吃这条线，读 :meth:`snapshot` 就是了。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from attention.care.signal import COMPANY, CareSignal

logger = logging.getLogger(__name__)

#: 跨重启要活着 —— 不然每次重启都把「她在面前」当成一次新跃迁
STATE_KEY = "source.front"

#: present 状态多久没心跳就算「不知道了」。tracker 心跳是每分钟一次，
#: 给 5 分钟宽限 —— 设备断电、tracker 崩了都算在内
FRESH_WINDOW = timedelta(minutes=5)


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


class FrontSource:
    """她在他面前吗。push 型源：事件从 perception 端点进来。"""

    name = "stackchan"

    def __init__(self, store: Any) -> None:
        self.store = store
        #: 她现在在不在面前（tracker 视角）
        self.present: bool = False
        #: 认出来是谁（"iris" / None = 陌生人或没开识别）
        self.identity: str | None = None
        #: 这次「在面前」从什么时候开始
        self.since: datetime | None = None
        #: 最近一次事件（arrived/left/looking）的时刻 —— 新鲜度看它
        self.updated_at: datetime | None = None
        self._load()

    # ------------------------------------------------------------ 入口

    def observe_event(self, event: str, identity: str | None,
                      observed_at: datetime) -> list[CareSignal]:
        """收下 tracker 的一条事件，跃迁时产出念头。

        - ``arrived``：她坐到面前了 → 一个念头（这是这条线存在的意义）
        - ``left``：她走了 → 不产念头（他该失落，但不用追出门 —— 出门
          那条线已经有位置源在管了）
        - ``looking``：心跳，只刷新新鲜度，**不产念头**（她一直在面前
          不是新消息）
        - ``identity``：在场但认出的身份变了（null↔iris）→ 只同步
          identity，**不产念头** —— 单人环境里这多数是识别断续（光线/
          角度），每次都产念头会烦
        """
        self.updated_at = observed_at
        out: list[CareSignal] = []

        if event == "arrived":
            if self.present:
                # 重复的 arrived（tracker 重启重放）不算新跃迁
                return []
            self.present = True
            self.identity = identity
            self.since = observed_at
            self._save()
            logger.info("她坐到了他面前（identity=%s）", identity or "?")
            out.append(CareSignal(
                source=self.name,
                subject="她来到他面前",
                # 陪伴型：说完就关。「她来了」没有可追问的事
                thread_kind=COMPANY,
                # 比随手惦记重、比出门轻 —— 出门时他什么都不知道，
                # 现在他看得见她
                urgency=0.65,
                payload={"transition": "arrived_front", "identity": identity},
            ))
        elif event == "left":
            if not self.present:
                return []
            since, self.since = self.since, None
            self.present = False
            self.identity = None
            self._save()
            logger.info("她离开了面前（坐了 %s 分钟）",
                        "?" if since is None else
                        round((observed_at - since).total_seconds() / 60, 1))
        elif event == "looking":
            if not self.present:
                # 心跳比 arrived 先到（tracker 重启等）—— 当 arrived 用
                self.present = True
                self.identity = identity
                self.since = observed_at
                self._save()
            else:
                self._save()
        elif event == "identity":
            # 不改 present / since —— 身份变了不等于刚到（见 docstring）
            if self.present and identity != self.identity:
                self.identity = identity
                self._save()
                logger.info("面前这张脸认出来了：identity=%s", identity or "?")
        else:
            logger.warning("不认识的感知事件：%s", event)
        return out

    # ------------------------------------------------------------ 读

    def is_fresh(self, now: datetime | None = None) -> bool:
        """present=True 但心跳断了，就降级成「不知道」。

        「读不到 = 不知道 ≠ 还在面前」—— 位置源 2026-09-22 那课。
        """
        if not self.present:
            return False
        now = now or datetime.now(timezone.utc)
        if self.updated_at is None:
            return False
        return (now - self.updated_at) <= FRESH_WINDOW

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        fresh = self.is_fresh(now)
        return {
            "present": self.present,
            "effective": fresh,   # 下游该信的那一个：断了心跳的 present 不算数
            "identity": self.identity if fresh else None,
            "since": self.since.isoformat() if self.since else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    # ------------------------------------------------------------ 落盘

    def _save(self) -> None:
        try:
            self.store.set_source_state(STATE_KEY, {
                "present": self.present,
                "identity": self.identity,
                "since": self.since.isoformat() if self.since else None,
                "updated_at": (
                    self.updated_at.isoformat() if self.updated_at else None
                ),
            })
        except Exception:  # noqa: BLE001
            logger.warning("面前状态没存住")

    def _load(self) -> None:
        try:
            raw = self.store.get_source_state(STATE_KEY) or {}
        except Exception:  # noqa: BLE001
            logger.warning("面前状态读不出来，从头开始")
            return
        # present 本身跨重启恢复，但心跳断了 is_fresh() 会兜住 ——
        # 「她在面前 + 进程刚重启 + tracker 也断了」这种组合宁可当不知道
        self.present = bool(raw.get("present"))
        self.identity = raw.get("identity")
        self.since = _parse(raw.get("since"))
        self.updated_at = _parse(raw.get("updated_at"))
