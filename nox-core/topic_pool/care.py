"""Topic 线头源 —— 池子里的料，作为第七种念头进 Care。

规格：《Topic_Pool_实事话题池链路.md》§4.1（2026-08-31 定稿：
**决策丢给 Care，hook 不开口**）。

## 它和惦记（ThinkingSource）是同一物种

    惦记：  「我想起她了」        → 20–90 分钟一次
    topic：  「池子里有条没聊过的」 → 3–8 小时一次，而且只在有货的时候

这个 Source **只产生念头，不许自己开口** —— signal.py 里写着的那条边界，
就是防着加第七个源的人图省事直接接到 push 上去。要不要说、现在说不说，
全部由 Orchestrator 决定：吃 DailyGate、吃「一小时一条新链」的额度、
每一笔进 CareLedger。池子里囤一百条，Care 不开口它就不吭声。

## 比惦记更稀有、更不急

外部材料不该挤掉「想起她」——urgency 0.3（惦记是 0.4，身体信号更高），
节奏 3–8 小时一次。池子空了就 1 小时后再看，**不产出念头**——
空池子不进账本，那不是「想了想说不出」，是根本没什么可想。
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from attention.care.signal import COMPANY, CareSignal

logger = logging.getLogger(__name__)

#: 存下次几点翻池子。跨重启要活着，否则每次重启重新摇，分布就偏了
STATE_KEY = "source.topic"

#: 翻池子的节奏。3–8 小时：外部材料一天想起一两次就够了，
#: 它的对手不是「忘了她」，是「没什么非说不可的」
MIN_GAP_H = 3
MAX_GAP_H = 8

#: 池子空时的回看间隔。空不是错误，别按错误刷
EMPTY_BACKOFF_MIN = 60


class TopicSource:
    """池子里有条没聊过的。第七个 Care 源，喂料的，不开口。"""

    name = "topic"

    def __init__(self, store: Any, pool: Any) -> None:
        #: attention.db，存下次翻池子的时间（同 ThinkingSource）
        self.store = store
        #: TopicPool。None = 话题池没启用，这条源永远安静
        self.pool = pool
        self._next_at: datetime | None = None
        self._load()

    # ------------------------------------------------------------ Source

    def poll(self, now: datetime) -> list[CareSignal]:
        if self.pool is None:
            return []

        if self._next_at is None:
            self._schedule(now)
            return []

        if now < self._next_at:
            return []

        # 到点了。只看 open——surfaced 的是推过/聊过的，不再循环（§2.3/§4.1）
        try:
            topics = self.pool.store.open_topics(
                now, include_surfaced=False, limit=1)
        except Exception:  # noqa: BLE001
            logger.exception("翻话题池失败，这轮跳过")
            self._schedule(now)
            return []

        if not topics:
            # 池子空：过一小时再看。**不产出念头** —— 空池子不进账本
            self._next_at = now + timedelta(minutes=EMPTY_BACKOFF_MIN)
            self._save()
            logger.debug("池子空，%d 分钟后再看", EMPTY_BACKOFF_MIN)
            return []

        t = topics[0]
        self._schedule(now)
        logger.info("池子里有条没聊过的（下次 %s）",
                    self._next_at.astimezone().strftime("%m-%d %H:%M"))
        return [CareSignal(
            source=self.name,
            subject=f"池子里没聊过的：{(t.source_title or t.hook)[:24]}",
            thread_kind=COMPANY,
            urgency=0.3,
            payload={
                "topic_id": t.id,
                "hook": t.hook,
                "source_title": t.source_title,
                "source_url": t.source_url,
                "category": t.category,
            },
        )]

    # ------------------------------------------------------------ 内部

    def _schedule(self, anchor: datetime) -> None:
        """摇下一个点。秒级精度不取整 —— 取整就有节拍（同 ThinkingSource）。"""
        seconds = random.uniform(MIN_GAP_H * 3600, MAX_GAP_H * 3600)
        self._next_at = anchor + timedelta(seconds=seconds)
        self._save()

    def _save(self) -> None:
        try:
            self.store.set_source_state(
                STATE_KEY, {"next_at": self._next_at.isoformat()})
        except Exception:  # noqa: BLE001
            logger.warning("topic 线头的下一次时间没存住")

    def _load(self) -> None:
        try:
            raw = (self.store.get_source_state(STATE_KEY) or {}).get("next_at")
            if raw:
                self._next_at = datetime.fromisoformat(raw)
        except Exception:  # noqa: BLE001
            self._next_at = None

    # ------------------------------------------------------------ 观察

    def snapshot(self) -> dict[str, Any]:
        return {
            "next_at": self._next_at.isoformat() if self._next_at else None,
            "window_h": [MIN_GAP_H, MAX_GAP_H],
        }
