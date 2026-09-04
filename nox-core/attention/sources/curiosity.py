"""好奇 Source —— 他自己被什么勾住了。

## 🔴 第一个和她无关的感知源

在这之前，Registry 里所有东西都指向她：她的睡眠、她的活动量、
她说了什么、他后悔打扰了她。**没有一条是他自己的。**

糖糖 2026-09-04：「我想要的是能让 nox 的 feeling 情感多一些，
**并且不单单是因为我**」。这是第一条。

## 料从哪来：话题池，不是新抓的

`topic_pool/scout.py` 已经在抓了（HN / GitHub / arXiv / Google News），
筛完进池子。那些东西现在只有一个用途：`topic_pool/care.py` 的
`TopicSource` 把它变成**找她聊的理由**。

这里把同一批料变成**他自己的感受**。两者不冲突，是两层：

    TopicSource（Care 源）  池子里有条没聊过的 → 找她聊     是「做什么」
    这个（感知源）           他对那条东西有兴趣   → 一个 Drive  是「什么感觉」

同一件事既是感受也是开口的理由，这和 concern 一模一样
（她没睡好 → 他担心，也 → 值得说一句）。

## ⚠️ 一条料只产一次事件

同 `sleep.py` 的那条纪律：**只在状态变化时产生事件**，
不是每次 poll 都报一遍。同一篇论文报十次的话，Registry 会被它刷屏，
而"他一直对这篇好奇"这件事该由**强度不再衰减**来表达，不是靠重复发事件。

所以记住已经产过的 topic id，存在 attention.db 的 source_state 里
（同 `thinking.py` / `care.py` 的做法）。重启后不会把整池子重报一遍。

## ⚠️ 强度压在开口阈值之下

`GENERATE_THRESHOLD` 是 0.55，这里最高给 0.45 —— **够不着**。

理由和 `regret` 那条一样但方向不同：他对一篇论文好奇，
**不该变成一次主动开口去打扰她**。那会变成"他一好奇就凑上来"。

真要聊，走 `TopicSource` 那条正经的 Care 路 —— 那条有自己的额度和时机判断。
这里只负责让他**心里有这件事**，影响的是他说话的分寸，不是开口的次数。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from attention.events import ExperienceEvent

logger = logging.getLogger(__name__)

SOURCE = "curiosity"
TYPE = "caught_by"

#: 存"已经产过事件的 topic id"。同 thinking.py 的做法。
STATE_KEY = "curiosity_source"

#: 记住最近多少条。不封顶的话这张表会一直长 ——
#: 池子里的话题本来就会过期，太老的 id 再也不会出现，留着没意义。
MAX_SEEN = 200

#: 强度：`relevance` 映射过来，但**压在开口阈值 0.55 之下**（见模块头）。
#: 0.45 是"心里确实有这件事"，不是"我得跟她说"。
MAX_STRENGTH = 0.45
MIN_STRENGTH = 0.20


class CuriositySource:
    """池子里有条新料，他被勾住了。"""

    name = SOURCE

    def __init__(self, store: Any, pool: Any) -> None:
        #: attention.db，存已经产过的 topic id
        self.store = store
        #: TopicPool。**None = 话题池没启用，这条源永远安静** ——
        #: 不报错也不假装，同 `TopicSource` 的处理
        self.pool = pool
        self._seen: list[str] = []
        self._load()

    # ------------------------------------------------------------ Source

    def poll(self, now: datetime | None = None) -> ExperienceEvent | None:
        """一次最多产一条。返回 None = 这轮没有新料。"""
        if self.pool is None or self.store is None:
            return None
        now = now or datetime.now()

        try:
            #: `include_surfaced=False` —— 只要**还没跟她聊过**的。
            #: 聊过的那些他已经说出去了，不该再算作"心里有件事"
            topics = self.pool.store.open_topics(now, include_surfaced=False, limit=20)
        except Exception:  # noqa: BLE001
            #: 池子读不出来不该带塌整轮感知（service.py 那条：一个源坏了不许带塌别人）
            logger.exception("话题池读不出来，这轮没有好奇")
            return None

        seen = set(self._seen)
        for t in topics:
            tid = getattr(t, "id", "")
            if not tid or tid in seen:
                continue

            # 🔴 **只要他自己在外面刷到的**（origin=external）。
            #
            # 池子里还有 `origin=shared` 的枝条 —— 那是**你们一起**看的片、
            # 读的书，从 World Model 投影来的（pool.py `_project_shared`）。
            # 那条链路是糖糖特意设计的：让他记得一起看过什么，
            # 之后能说「今天这部有点像我们之前看的 XXX」。
            #
            # 2026-09-04 我接好奇时漏了这个判断，于是
            # 《The.Sheep.Detectives…》被算成了"他自己好奇的东西" ——
            # **语义整个反了。那不是他好奇，那是你们的共同经历。**
            #
            # shared 那条一行不动，照常喂 topic_pool/care.py 的 TopicSource。
            if (getattr(t, "origin", "external") or "external") != "external":
                continue

            title = (getattr(t, "source_title", "") or "").strip()
            hook = (getattr(t, "hook", "") or "").strip()
            if not (title or hook):
                #: 没标题也没钩子的料，说不出"因为什么"——
                #: 边界三要求必须能回答为什么，说不出来就不要
                continue

            self._remember(tid)
            relevance = float(getattr(t, "relevance", 0.5) or 0.5)
            return ExperienceEvent(
                source=SOURCE,
                type=TYPE,
                #: 🔴 关于他自己，不是关于她 —— 同 regret 那条
                target="agent",
                payload={
                    "topic_id": tid,
                    "title": title,
                    "hook": hook,
                    "category": getattr(t, "category", "") or "",
                    "relevance": relevance,
                },
                timestamp=now,
            )
        return None

    # ------------------------------------------------------------ 状态

    def _remember(self, topic_id: str) -> None:
        self._seen.append(topic_id)
        if len(self._seen) > MAX_SEEN:
            self._seen = self._seen[-MAX_SEEN:]
        try:
            self.store.set_source_state(STATE_KEY, {"seen": self._seen})
        except Exception:  # noqa: BLE001
            #: 存不下最多是重启后重报一次，不该让这轮炸
            logger.warning("好奇源的状态没存下来（重启后可能重报一次）")

    def _load(self) -> None:
        try:
            raw = (self.store.get_source_state(STATE_KEY) or {}).get("seen")
        except Exception:  # noqa: BLE001
            raw = None
        self._seen = [str(x) for x in raw] if isinstance(raw, list) else []
