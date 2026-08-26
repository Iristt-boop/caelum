"""Context 缓存 —— 跨 Provider 的唯一存储。

## 为什么缓存不放在 Provider 自己身上

架构文档的基类示例里，Provider 自带 `_cache` / `_cached_at`，同时另有一个
`ContextCache`。两套存储并存迟早不一致，而且没法统一做两件事：
一是 `invalidate` 全清，二是**量命中率**。

命中率不是可选项：Context Engine 唯一可能造成的严重后果是
「不报错但很贵」—— 服务照常、回答照常，只有账单翻 20 倍，
从任何监控上都看不出来（PROJECT.md 第十九节第 9 条 / 执行计划第一周检查点）。
所以缓存必须能自己报出「这轮到底拉了几次外部数据」。

## 全同步

架构文档 0.2：nox-core 从上到下是同步的，别把 async 传染进来。
文档里 `async def get(...)` 那版接口形状照抄，但去掉 async。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CachedState:
    """一份缓存下来的 Provider 状态。"""

    value: dict[str, Any]
    cached_at: datetime
    ttl: timedelta

    @property
    def age(self) -> timedelta:
        return datetime.now() - self.cached_at

    @property
    def expired(self) -> bool:
        # 用 >= 不是 >：Windows 上 datetime.now() 的分辨率约 15.6ms
        # （实测 2000 次调用只拿到 2 个不同值），同一时间片里 age 恒为 0。
        # 写成 `age > ttl` 的话 ttl=0 永远判不出过期，测试里想「立刻过期」都做不到。
        return self.age >= self.ttl

    @property
    def age_s(self) -> float:
        return round(self.age.total_seconds(), 1)


class ContextCache:
    """进程内的 Provider 状态缓存。

    加锁是因为 Starlette 用线程池推同步生成器，多个请求会并发进来 ——
    这套代码在并发边界上有前科（contextvars 那次，PROJECT.md 第十九节第 13 条），
    这里宁可老实加锁。锁只护字典本身，不护 `_fetch`：
    两个请求同时拉同一个 Provider 最多多花一次，把网络调用锁进临界区
    才是真会出事的做法。
    """

    def __init__(self) -> None:
        self._data: dict[str, CachedState] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._stale_hits = 0

    def get(self, name: str, *, allow_stale: bool = False) -> CachedState | None:
        """取缓存。

        `allow_stale=True` 时连过期的也返回 —— 这条是给「Provider 拉取失败」
        用的：宁可让他知道得旧一点，也不要突然失明。
        """
        with self._lock:
            hit = self._data.get(name)
            if hit is None:
                self._misses += 1
                return None
            if hit.expired:
                if not allow_stale:
                    self._misses += 1
                    return None
                self._stale_hits += 1
                return hit
            self._hits += 1
            return hit

    def set(self, name: str, value: dict[str, Any], ttl: timedelta) -> None:
        with self._lock:
            self._data[name] = CachedState(value=value, cached_at=datetime.now(), ttl=ttl)

    def invalidate(self, name: str | None = None) -> int:
        """清掉某个 Provider 的缓存；不传名字就全清。返回清掉几条。"""
        with self._lock:
            if name is None:
                n = len(self._data)
                self._data.clear()
                return n
            return 1 if self._data.pop(name, None) is not None else 0

    def stats(self) -> dict[str, Any]:
        """命中率。第一周检查点要量的就是它。

        `hit_rate` 低说明 TTL 设短了或者每轮都在强制刷新 —— 那意味着每轮都在
        真打外部服务，慢且贵。
        """
        with self._lock:
            total = self._hits + self._misses
            return {
                "entries": len(self._data),
                "hits": self._hits,
                "misses": self._misses,
                "stale_hits": self._stale_hits,
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
                "ages_s": {k: v.age_s for k, v in self._data.items()},
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = self._misses = self._stale_hits = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
