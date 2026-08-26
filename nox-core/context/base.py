"""Context Provider 基类。

一个 Provider 回答「世界的某一面现在是什么样」：几点了、家里灯开着吗、
她昨晚睡了多久、最近聊过什么。

## 子类只需要实现三件事

    class TimeProvider(BaseContextProvider):
        name = "time"
        ttl = timedelta(minutes=1)

        def _fetch(self) -> dict:
            ...          # 拉数据，失败直接抛，基类兜

可选再覆盖 `render()` 决定怎么变成给他看的一句话。

## 三条硬约束

**1. 渲染结果只能进 `dynamic_system`，永远不许进 `system`。**
提示词缓存按「模型 + 前缀字节」匹配，静态前缀变一个字，那 12K 就整段作废。
World State 天生每轮都不一样，进了静态前缀就是每轮亲手砸一次缓存 ——
一轮 ¥0.00055 会变成 ¥0.011，二十倍。
（架构文档第零节 0.1 / PROJECT.md 第十九节第 9 条）

**2. 写同步，不要 async。**
nox-core 从 `loop.run()` 到 `tool.handler()` 到 `run_stream()` 全是同步的。
混 async 要么每次造事件循环，要么大重构。而且这套代码在异步/同步边界上有前科
（contextvars 跨同步生成器，PROJECT.md 第十九节第 13 条）。
真要并行，在 registry 那层用 ThreadPoolExecutor，别让 async 传染进来。

**3. 拉不到数据就退回旧数据，不要变成失明。**
Provider 挂了，正确的表现是「他知道得旧一点」，不是「他突然什么都不知道」。
只有从来没成功过才报 `available: False` —— 而且要**如实报**，
不许悄悄返回空字典假装一切正常（第十九节第 3 条：失败必须可见）。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, ClassVar

from context.cache import ContextCache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Turn:
    """当轮的输入。

    架构文档里 `_fetch()` 是无参数的 —— 那是假设 Provider 取的都是「世界状态」
    （几点了、灯开着吗），跟这轮说了什么无关。
    但第一个真实 Provider（mood）就不成立：它的场景判断
    （敏感话题 / 在聊技术 / 她在靠近你）**必须看当轮说了什么**。

    所以给 `_fetch` 传一个 Turn。不需要的 Provider 忽略它就行
    （TimeProvider / HomeProvider 都用不上）。
    """

    text: str = ""
    now: datetime | None = None
    voice: bool = False
    scene: str | None = None


class BaseContextProvider(ABC):
    """所有 Context Provider 的基类。"""

    #: 在 World State 里的键名，也是缓存键。子类必须给。
    name: ClassVar[str] = ""
    #: 缓存多久。按数据变化的快慢定，别一律用默认值：
    #: time 1 分钟 / home 30 秒 / memory 5 分钟 / health 当天 / weather 30 分钟
    ttl: ClassVar[timedelta] = timedelta(minutes=5)
    #: World State 的一级字段。只有这五个，新数据源尽量归进已有分类，
    #: 不要无限扩展顶层（架构文档第二十三条）
    section: ClassVar[str] = "environment"
    #: 每轮必新、**不许缓存**的 Provider 标 True。
    #:
    #: 这不是性能取舍，是正确性：mood 的状态依赖当轮的话，
    #: 缓存住就会把上一轮的场景判断用到这一轮 ——
    #: 她这句在聊技术，他却还按上一句「她在靠近你」的调子回话。
    volatile: ClassVar[bool] = False

    def __init__(self, config: dict[str, Any] | None = None,
                 cache: ContextCache | None = None) -> None:
        if not self.name:
            raise ValueError(f"{type(self).__name__} 没有设置 name")
        self.config = config or {}
        # 缓存统一放外面，不在 Provider 身上自己存一份 —— 两套存储迟早不一致，
        # 而且那样没法统一清、也量不出命中率（见 cache.py 开头）
        #
        # ⚠️ 必须 `is not None`，不能写 `cache or ContextCache()`：
        # ContextCache 定义了 __len__，**空缓存的 bool() 是 False**，
        # 刚传进来的空缓存会被当成假值丢掉，每个 Provider 又各自新建一个 ——
        # 统一存放直接失效，而且完全不报错。2026-08-02 写第一版时就踩了。
        self.cache = cache if cache is not None else ContextCache()

    # ------------------------------------------------------------ 子类实现

    @abstractmethod
    def _fetch(self, turn: Turn) -> dict[str, Any]:
        """真正去拿数据。用不到 `turn` 就忽略它。

        **失败就抛**，不要自己吞成一个空 dict —— 吞掉之后基类分不清
        「拿到了但确实是空的」和「根本没拿到」，就没法退回旧数据了。
        """

    def render(self, state: dict[str, Any]) -> str:
        """把状态变成给他看的一段话。

        默认实现只做最朴素的罗列，够用就不用覆盖。
        子类想控制措辞就覆盖它 —— 比如 mood 那种成段的自然语言。

        返回空串表示「这轮没什么好说的」，registry 会跳过它，
        不会在提示里留一行空标题。
        """
        if not state or state.get("available") is False:
            return ""
        parts = [f"{k}={v}" for k, v in state.items() if k not in _META_KEYS and v not in (None, "")]
        return f"{self.name}: " + "，".join(parts) if parts else ""

    # ------------------------------------------------------------ 对外

    def get_state(self, turn: Turn | None = None,
                  force_refresh: bool = False) -> dict[str, Any]:
        """对外接口：优先吃缓存，过期或强制刷新时才真去拉。

        `volatile` 的 Provider 一律绕开缓存 —— 它们的状态跟当轮的话绑着，
        缓存住就是把上一轮的判断用到这一轮。
        """
        turn = turn or Turn()
        if not force_refresh and not self.volatile:
            hit = self.cache.get(self.name)
            if hit is not None:
                return hit.value

        try:
            value = self._fetch(turn)
        except Exception as exc:  # noqa: BLE001
            # 拿不到就退回上一次的旧值，并明确标 stale —— 让他知道这是旧的，
            # 而不是把陈年数据当现在的说给她听
            stale = self.cache.get(self.name, allow_stale=True)
            if stale is not None:
                logger.warning(
                    "%s 拉取失败（%s: %s），退回 %.0f 秒前的旧数据",
                    self.name, type(exc).__name__, exc, stale.age_s,
                )
                return {**stale.value, "stale": True, "stale_age_s": stale.age_s}
            logger.warning("%s 拉取失败且没有旧数据: %s: %s", self.name, type(exc).__name__, exc)
            return {"available": False, "error": f"{type(exc).__name__}: {exc}"}

        if not isinstance(value, dict):
            raise TypeError(f"{self.name}._fetch() 要返回 dict，拿到的是 {type(value).__name__}")

        if not self.volatile:
            self.cache.set(self.name, value, self.ttl)
        return value

    def invalidate(self) -> None:
        self.cache.invalidate(self.name)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} name={self.name} ttl={self.ttl}>"


#: 这些键是基类自己加的元信息，默认渲染时不该当成内容念出来
_META_KEYS = frozenset({"available", "error", "stale", "stale_age_s"})
