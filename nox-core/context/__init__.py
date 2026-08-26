"""Context Engine —— Nox Core 的内部模块，不是独立服务。

当前只有骨架三件套（2026-08-02，执行计划 Day 1）：

    base.py      Provider 基类：子类实现 _fetch()，可选覆盖 render()
    cache.py     跨 Provider 的唯一缓存，带命中率统计
    registry.py  谁在场、这轮取哪几个、拼成什么话

`engine.py`（World State 聚合）**故意还没写** —— 架构文档第二节：
「Engine 是 Provider 足够多之后的统一封装，不是起点。」
先让第一个真实 Provider（mood）跑起来，再谈聚合。

同理不要现在就拆独立服务、独立数据库、消息队列。
Provider 超过 5 个且调用方不只有 Core 时再说。
"""

from context.base import BaseContextProvider
from context.cache import CachedState, ContextCache
from context.registry import ContextProviderRegistry

__all__ = [
    "BaseContextProvider",
    "CachedState",
    "ContextCache",
    "ContextProviderRegistry",
]
