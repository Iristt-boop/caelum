"""共读 / 共听 / 共影 —— 三块共活动的事实，接进 World Model。

规格在《Topic_Pool_实事话题池链路.md》§3.1.2（2026-08-31 定稿）：
读写在 nox-core 这边收口，三块服务本身一行不改。

## 这个 Source 和别的感知源不一样：它**只记账，不开口**

sleep / metrics 是「变化驱动」：状态变了才产事件，进 Registry 吃衰减。
这里相反 —— `poll()` 永远返回 None。它只负责把事实送进 World Model
（读到哪了、看完一场、一起听完一首），「这值不值得他开口」是
Evaluator / 以后的话题池的事，别在这里越权。

## 幂等全靠 dedup_key，自己不记任何状态

心跳一轮接一轮，同一份进度会被读到几十遍。每条事实的 dedup_key 都用
**天然唯一**的键 —— 读到某位置的时间戳 / 观影会话 id / 第 N 次一起听。
撞了 `observe()` 自己会跳过，所以重启、重复 poll 都不会写重。

## 记什么、不记什么

- 共读：读到新位置就是一条事实（`lastReadAt` 变了才立得住 key），
  读完了 `complete: true`。放下很久的书不回灌。
- 共影：**一场结束才记**（≥10 分钟，滤掉误开）；还在看的那场归
  `WatchingCheck` 管，这里不掺和实时的。
- 共听：只记「一起听」（Nox 点播且她听完，`togetherCount` 那条线）。
  她自己单曲循环不进 world —— 共听的记忆是「一起」两个字。

## 读不到就跳过，不假装

三块服务哪个没配（client 是 None）或读挂了，那块这轮就跳过。
留档是副产品，不能让它带塌 Attention（同 sleep.py 的规矩）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: 在读的书只盯最近还在动的。放下超过这个窗口的不回灌 ——
#: 「她在读什么」说的是现在，半年前翻过两页不算
READING_WINDOW = timedelta(hours=48)

#: 一起听的回灌上限。更早的歌 `eryu_experience` 本来就看得到，
#: 不用在这里一次性灌几百条旧事实进 world.db
LISTENING_WINDOW = timedelta(days=14)

#: 一场观影短于这个数不算「看过」—— 打开试了两分钟就关的，不是共同经历
MIN_WATCH_MINUTES = 10


def _ts(value: Any) -> datetime | None:
    """ISO 字符串 → 带时区的 datetime。解析不了返回 None，**不抛**。

    co-reading / eryu / bridge 三边的格式不统一（有的带 Z，有的 naive），
    naive 当 UTC —— 三块服务自己都按 UTC 存，猜错时区的代价
    比直接拒收一条事实更大。
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class SharedActivitiesSource:
    """把三块共活动的事实写进 World Model。**poll() 永远返回 None。**

        src = SharedActivitiesSource(reading=..., eryu=..., bridge=..., world=...)
        src.poll()      # 没有返回值，只留档

    三个 client 任一个都可以是 None（服务没配），对应那块静默跳过。
    """

    def __init__(self, reading: Any = None, eryu: Any = None,
                 bridge: Any = None, world: Any = None) -> None:
        self.reading = reading      # co-reading 的 RestClient（:3100，Bearer）
        self.eryu = eryu            # eryu 的 RestClient
        self.bridge = bridge        # bridge 的 RestClient（core.bridge）
        self.world = world          # WorldModel；None = 整条 source 不动笔

    def poll(self, now: datetime | None = None) -> None:
        """跑一遍三块。每块各自兜底 —— 一块坏了不许带塌另外两块。"""
        now = now or datetime.now(timezone.utc)
        for name, run in (("共读", self._poll_reading),
                          ("共影", self._poll_watching),
                          ("共听", self._poll_listening)):
            try:
                run(now)
            except Exception:  # noqa: BLE001
                logger.exception("记录%s失败，这轮跳过", name)
        return None

    # ------------------------------------------------------------ 共读

    def _poll_reading(self, now: datetime) -> None:
        if self.reading is None or self.world is None:
            return
        r = self.reading.get("/api/progress")
        if not r.ok:
            logger.warning("读共读进度失败：%s", r.error)
            return
        data = r.data if isinstance(r.data, dict) else {}
        for book_id, v in data.items():
            if not isinstance(v, dict):
                continue
            status = str(v.get("status") or "")
            complete = bool(v.get("complete")) or status == "finished"
            last_read = _ts(v.get("lastReadAt"))
            if not complete:
                # 没读完的书只盯最近还在动的；读完了的不管多久都值得留一条
                if last_read is None or (now - last_read) > READING_WINDOW:
                    continue

            # key 立在 lastReadAt 上：只有真读了、进度存盘了，key 才会变。
            # 心跳 poll 一百次也是同一条，一天读三轮就是三条事实
            last_raw = str(v.get("lastReadAt") or "")
            key = f"reading/{book_id}/{last_raw or 'finished'}"
            self.world.observe(
                source="co-reading",
                type="reading_progress",
                observed={
                    "book_id": book_id,
                    "title": v.get("title") or book_id,
                    "progress": v.get("progressPercent"),
                    "chunks_read": v.get("chunksRead"),
                    "chunk_count": v.get("chunkCount"),
                    "chunk": v.get("lastChunkId"),
                    "status": status,
                    "complete": complete,
                },
                observed_at=last_read or now,
                dedup_key=key,
            )

    # ------------------------------------------------------------ 共影

    def _poll_watching(self, now: datetime) -> None:
        if self.bridge is None or self.world is None:
            return
        r = self.bridge.get("/api/watch/history", {"limit": 10})
        if not r.ok:
            logger.warning("读观影记录失败：%s", r.error)
            return
        items = (r.data or {}).get("items") if isinstance(r.data, dict) else None
        for row in items or []:
            if not isinstance(row, dict) or not row.get("ended_at"):
                continue  # 还在看的那场（ended_at 空）归 WatchingCheck 管
            started = _ts(row.get("started_at"))
            ended = _ts(row.get("ended_at")) or _ts(row.get("last_seen_at"))
            if ended is None:
                continue
            minutes = None
            if started is not None:
                minutes = int((ended - started).total_seconds() // 60)
                if minutes < MIN_WATCH_MINUTES:
                    continue  # 误开一下的，不算一场

            duration_s = row.get("duration_s")
            position_ms = row.get("position_ms")
            finished = (
                str(row.get("play_state") or "") == "ended"
                or (isinstance(duration_s, (int, float)) and duration_s > 0
                    and isinstance(position_ms, (int, float))
                    and position_ms >= duration_s * 900)
            )
            self.world.observe(
                source="bridge",
                type="watching_session",
                observed={
                    "session_id": row.get("id"),
                    "title": row.get("title"),
                    "episode": row.get("episode"),
                    "minutes": minutes,
                    "finished": bool(finished),
                },
                observed_at=ended,
                dedup_key=f"watching/{row.get('id')}",
            )

    # ------------------------------------------------------------ 共听

    def _poll_listening(self, now: datetime) -> None:
        if self.eryu is None or self.world is None:
            return
        r = self.eryu.get("/music/memory")
        if not r.ok:
            logger.warning("读歌曲记忆失败：%s", r.error)
            return
        mem = (r.data or {}).get("memories") if isinstance(r.data, dict) else None
        for song_id, e in (mem or {}).items():
            if not isinstance(e, dict):
                continue
            together = int(e.get("togetherCount") or 0)
            if together <= 0:
                continue  # 她自己听的那些不进 world —— 共听的记忆是「一起」
            last = _ts(e.get("lastListened"))
            if last is None or (now - last) > LISTENING_WINDOW:
                continue
            # key 立在次数上：count 不变就不重复记。
            # 两次 poll 之间连听两首的话只落一条（count 直接 +2），
            # 中间那次的那一刻丢了 —— 但「一起听过 N 次」这个事实没丢
            self.world.observe(
                source="eryu",
                type="listening_together",
                observed={
                    "song_id": str(song_id),
                    "title": e.get("name") or str(song_id),
                    "artist": e.get("artist"),
                    "together_count": together,
                },
                observed_at=last,
                dedup_key=f"listening/{song_id}/{together}",
            )
