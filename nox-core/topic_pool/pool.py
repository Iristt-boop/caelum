"""TopicPool 门面 —— 把 Scout → Cache → Filter → 池子串成一轮。

一轮（`run_cycle`）做的事，按文档 §1 的链路：

    抽 2~3 个方向抓候选 → 逐条落候选缓存 → Filter 筛 0~3 条 →
    进池子 → shared 枝条投影 → 到期的转 expired

**filter 出空不是故障**（没有好内容很正常）；**某个抓取器挂了只跳过**
（scout.py 的规矩）；整轮失败由 `run_topic_loop` 兜住，下一轮再来。

shared 枝条（origin=shared）从 World Model 直接投影，不过网络：
一起看完一场、读完一本书。共听**不投影** —— 一起听过的歌 eryu 的
记忆里本来就有账（togetherCount），每首都投影会把池子灌满水；
等哪天有「一起听完了整张专辑」这样的信号再说。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from topic_pool import scout
from topic_pool.filter import run_filter
from topic_pool.store import SHARED_TTL_HOURS, Candidate, Topic, TopicStore, ttl_for

logger = logging.getLogger(__name__)

#: 默认 6 小时一轮（文档 §概述）。NOX_TOPIC_SCOUT_INTERVAL_S 可改
DEFAULT_SCOUT_INTERVAL_S = 6 * 3600
#: 服务起来先等两分钟跑头一轮 —— 别让她部署完干等 6 小时才见第一批
FIRST_RUN_DELAY_S = 120
#: 候选缓存留 48 小时：足够 Filter 挂掉后重筛，再老就该让位了
CANDIDATE_CACHE_AGE = timedelta(hours=48)


def _url_dedup(url: str) -> str:
    return "url:" + hashlib.sha1(url.encode()).hexdigest()[:16]


class TopicPool:
    """池子本体。store 是必需的，world / adapter 缺了各自降级。"""

    def __init__(self, db_path: str | Path, world: Any = None,
                 adapter: Any = None) -> None:
        self.store = TopicStore(db_path)
        self.world = world
        self.adapter = adapter

    # ------------------------------------------------------------ 一轮

    def run_cycle(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)

        # 1. 抽方向（文档：每轮 2~3 个，随机，不重复）
        k = random.choice((2, 2, 3))
        cats = random.sample(sorted(scout.DIRECTIONS), k)

        # 2. 抓候选：抽中的方向 + 世界钩子（不随机，有货就来）
        candidates: list[Candidate] = []
        for cat in cats:
            candidates.extend(scout.fetch_direction(cat))
        hook_candidates, hooks = scout.fetch_world_hooks(self.world, now)
        candidates.extend(hook_candidates)

        # 3. 候选缓存：本轮的逐条落（撞了 = 抓过，False 不是错误）
        fresh = [c for c in candidates if self.store.add_candidate(c)]
        self.store.prune_candidates(CANDIDATE_CACHE_AGE)

        # 4. Filter → 池子。origin=external，title/url 用我们抓的那份
        added = 0
        for t in run_filter(self.adapter, candidates, now):
            cand = next(c for c in candidates if c.source_id == t["source_id"])
            if self.store.add_topic(
                Topic(
                    hook=t["hook"],
                    source_title=cand.title,
                    source_url=cand.url,
                    category=t["category"],
                    origin="external",
                    why_this=t["why_this"],
                    relevance=t["relevance"],
                    observed_at=cand.published_at or now,
                ),
                dedup_key=_url_dedup(cand.url),
            ):
                added += 1

        # 5. shared 枝条投影 + 6. 到期的转 expired
        shared = self.project_shared(now)
        expired = self.store.expire(now)

        return {
            "directions": cats,
            "candidates": len(candidates),
            "new_candidates": len(fresh),
            "world_hooks": hooks,
            "external_topics": added,
            "shared_topics": shared,
            "expired": expired,
            "pool": self.store.stats(),
        }

    # ------------------------------------------------------------ shared 投影

    def project_shared(self, now: datetime | None = None) -> int:
        """World Model 里的共同经历 → origin=shared 的枝条。

        只投影窗口内的：刚看完一场（3 天）、刚读完一本（14 天）。
        dedup 立在经历本身上（会话 id / 书 id），投影过就不再来。
        """
        if self.world is None:
            return 0
        now = now or datetime.now(timezone.utc)
        added = 0

        try:
            sessions = self.world.query("watching_session", days=3)
        except Exception:  # noqa: BLE001
            logger.exception("读观影事实失败，这轮不投影 shared")
            sessions = []
        for e in sessions:
            raw = e.raw or {}
            title = str(raw.get("title") or "").strip()
            session_id = raw.get("session_id")
            if not title or not session_id:
                continue
            ep = str(raw.get("episode") or "").strip()
            minutes = raw.get("minutes")
            span = f"（{minutes} 分钟）" if minutes else ""
            if raw.get("finished"):
                hook = f"一起看完了《{title}》{ep}{span}，还没聊过——结局值得说说。"
            else:
                hook = f"一起看了《{title}》{ep}{span}，中途放下，也许还想接着看。"
            if self.store.add_topic(
                Topic(hook=hook, source_title=f"《{title}》{ep}".strip(),
                      category="film", origin="shared",
                      why_this=["来自共影的观影记录"],
                      relevance=0.8, observed_at=e.observed_at),
                dedup_key=f"shared/watch/{session_id}",
            ):
                added += 1

        try:
            books = self.world.query("reading_progress", days=14)
        except Exception:  # noqa: BLE001
            logger.exception("读共读事实失败，这轮不投影 shared")
            books = []
        for e in books:
            raw = e.raw or {}
            if not raw.get("complete"):
                continue
            title = str(raw.get("title") or "").strip()
            book_id = raw.get("book_id")
            if not title or not book_id:
                continue
            chunks = raw.get("chunks_read"), raw.get("chunk_count")
            span = (f"（{chunks[0]}/{chunks[1]} 章）"
                    if isinstance(chunks[0], int) and isinstance(chunks[1], int) else "")
            hook = f"《{title}》读完了{span}——刚合上的书最适合聊聊。"
            if self.store.add_topic(
                Topic(hook=hook, source_title=f"《{title}》",
                      category="books", origin="shared",
                      why_this=["来自共读的进度记录"],
                      relevance=0.9, observed_at=e.observed_at),
                dedup_key=f"shared/reading-finished/{book_id}",
            ):
                added += 1

        return added

    # ------------------------------------------------------------ 读

    def topics_for_ui(self, now: datetime | None = None) -> list[dict]:
        """给前端 / hook 的活话题。observed_at / expires_at 已转 ISO。"""
        now = now or datetime.now(timezone.utc)
        return [t.to_dict(now) for t in self.store.open_topics(now)]


async def run_topic_loop(pool: TopicPool,
                         interval_s: int = DEFAULT_SCOUT_INTERVAL_S) -> None:
    """lifespan 用的后台循环。阻塞的网络和 LLM 调用都丢线程池。

    先等 `FIRST_RUN_DELAY_S` 跑头一轮，之后每 `interval_s` 一轮。
    一轮失败不许死循环 —— 它死了的表现是池子悄悄变空，没人会发现。
    """
    logger.info("话题池启动：先 %d 秒后跑头一轮，之后每 %d 小时一轮",
                FIRST_RUN_DELAY_S, interval_s // 3600)
    while True:
        try:
            await asyncio.sleep(FIRST_RUN_DELAY_S)
            stats = await asyncio.to_thread(pool.run_cycle)
            logger.info("话题池一轮：%s", stats)
            await asyncio.sleep(max(60, interval_s - FIRST_RUN_DELAY_S))
        except asyncio.CancelledError:
            logger.info("话题池停止")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("话题池一轮出错，下一轮继续")
