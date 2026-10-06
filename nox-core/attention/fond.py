"""想起开心的旧事 —— 「想逗她」的第二个来源（V4.5「促狭的机会机制」，她 2026-10-06 拍板）。

## 为什么要有它

原来的促狭只有一个来源：**她正在闹**（playfulness.py，最近 20 分钟的气氛）。
可他主动找她的时候她多半不在聊天，所以那一刻促狭永远是 0 ——
09-29→10-06 他主动开口 185 次，心情里「想逗她」**一次都没有**。

设计稿里她给的方向：

> 人的快乐很多时候不是检测来的：看到猫视频、想起你喜欢的东西、翻到老照片、聊到一个梗。
> 第五帖该是「突然看到你之前发的小猫照片，想起你当时开心的样子」。

## 她定的三件事（10-06）

- **来源**：记忆库里开心的记忆 + Gallery 收藏的照片
- **只换心情，不加次数**：他本来就要想起她的那一次里，多一个可以抽中的心情；每天 8 次上限不变
- 快涨快散：同一件旧事 14 天内不再拿出来

## 分寸

她不舒服的时候不逗（调用方看担心，见 `service._think_of_her`）。
这里只负责「有没有一件开心的旧事可以想起」，不判断该不该说。
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from attention.dream import _as_list, _created_of, _is_locked, _load_buckets
from temporal import CST

logger = logging.getLogger(__name__)

#: 多开心才算（记忆桶的 valence）。0.8 以上 3~60 天前的约 74 条（10-06 线上）
MIN_VALENCE = 0.8
#: 「之前」的意思：太新的是「刚才」，不是「想起」；太老的已经远了
MIN_AGE = timedelta(days=3)
MAX_AGE = timedelta(days=60)
#: 生活里的事才算。修代码、上班的开心不是她想被想起的那种
WARM_DOMAINS = frozenset({"恋爱", "友谊", "影视", "游戏", "饮食", "宠物", "日常", "手工", "社交",
                          "家庭", "出行", "居家", "情绪", "阅读", "内心", "梦境", "睡眠"})
COLD_DOMAINS = frozenset({"编程", "工作"})
#: 同一件旧事多久内不再拿出来
REUSE_GAP = timedelta(days=14)
STATE_KEY = "fond.used"

_BARE_ID = re.compile(r"^[0-9a-f]{8,}$")


@dataclass(frozen=True)
class Fond:
    """一件可以想起来的开心旧事。"""

    kind: str          # memory / photo
    key: str           # 去重用：桶 id / 照片 id
    title: str         # 短名，进 because
    detail: str        # 给他看的那段
    age_days: int
    #: 照片才有：给 send_gallery_image 的 query（按描述匹配）
    query: str = ""


class FondSource:
    """从记忆库和 Gallery 收藏里挑一件开心的旧事。

    `buckets_dir` 读记忆桶（同 dream.py 的读法）；`bridge` 读 Gallery 收藏；
    `store` 记哪些最近用过（source_state）。哪一路读不到就只用另一路，**读挂了不抛**。
    """

    def __init__(self, *, store: Any, buckets_dir: str | None, bridge: Any = None,
                 rng: Any = None) -> None:
        self.store, self.buckets_dir, self.bridge = store, buckets_dir, bridge
        self.rng = rng or random

    # ------------------------------------------------------------ 候选

    def _memories(self, now: datetime) -> list[tuple[float, Fond]]:
        if not self.buckets_dir:
            return []
        try:
            buckets = _load_buckets(Path(self.buckets_dir))
        except Exception:  # noqa: BLE001
            logger.warning("读记忆桶失败，这次只从照片里想", exc_info=True)
            return []
        out = []
        for b in buckets:
            m = b["meta"]
            try:
                valence = float(m.get("valence"))
            except (TypeError, ValueError):
                continue
            created = _created_of(m)
            name = str(m.get("name") or "").strip()
            domains = set(_as_list(m.get("domain")))
            if (valence < MIN_VALENCE or not created or not (MIN_AGE <= now - created <= MAX_AGE)
                    or _is_locked(m) or str(m.get("type", "")).lower() in ("feel", "archive", "archived")
                    or not name or _BARE_ID.match(name)
                    or not (domains & WARM_DOMAINS) or (domains & COLD_DOMAINS)):
                continue
            out.append((valence, Fond(kind="memory", key=str(m.get("id")), title=name,
                                      detail=f"{name}：{b['body'][:200]}",
                                      age_days=(now - created).days)))
        return out

    def _photos(self, now: datetime) -> list[tuple[float, Fond]]:
        if self.bridge is None:
            return []
        r = self.bridge.get("/api/gallery/list?filter=favorites")
        if not getattr(r, "ok", False):
            logger.warning("读 Gallery 收藏失败（%s），这次只从记忆里想", getattr(r, "error", "?"))
            return []
        data = getattr(r, "data", None)
        items = data.get("items") if isinstance(data, dict) else data
        out = []
        for p in items or []:
            desc = str(p.get("description") or "").strip()
            try:
                created = datetime.fromisoformat(str(p.get("created_at")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=CST)   # 没带时区的按她那边算（同 dream._created_of）
            if not desc or now - created < MIN_AGE:
                continue
            out.append((0.9, Fond(kind="photo", key=str(p.get("id")), title=f"那张照片（{desc[:16]}）",
                                  detail=f"她收藏的一张照片：{desc[:200]}", age_days=(now - created).days,
                                  query=desc[:12])))
        return out

    # ------------------------------------------------------------ 挑 / 记

    def _used(self) -> dict[str, str]:
        try:
            return dict((self.store.get_source_state(STATE_KEY) or {}).get("items") or {})
        except Exception:  # noqa: BLE001
            return {}

    def pick(self, now: datetime) -> Fond | None:
        """挑一件。按开心程度加权随机；14 天内用过的不挑。没有就 None。"""
        used = self._used()
        fresh = []
        for w, f in self._memories(now) + self._photos(now):
            last = used.get(f"{f.kind}:{f.key}")
            if last and now - datetime.fromisoformat(last) < REUSE_GAP:
                continue
            fresh.append((w, f))
        if not fresh:
            return None
        total = sum(w for w, _ in fresh)
        r = self.rng.uniform(0, total)
        acc = 0.0
        for w, f in fresh:
            acc += w
            if r <= acc:
                return f
        return fresh[-1][1]

    def mark_used(self, fond: Fond, now: datetime) -> None:
        """他真的从这件事说起了才记（[SKIP] 掉的不算）。"""
        used = self._used()
        used[f"{fond.kind}:{fond.key}"] = now.isoformat()
        cutoff = now - REUSE_GAP
        used = {k: v for k, v in used.items() if datetime.fromisoformat(v) >= cutoff}
        try:
            self.store.set_source_state(STATE_KEY, {"items": used})
        except Exception:  # noqa: BLE001
            logger.warning("「想起过哪件旧事」没存住，可能两周内会再想起一次", exc_info=True)
