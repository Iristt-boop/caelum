"""Provider 注册表 —— 谁在场、这轮取哪几个、拼成什么话。

## 它不决定「加载谁」

决策权在 Router：闲聊只拉 chat+memory，家居才拉 home+time
（架构文档第二节第 4 点 / 第二十一条）。注册表只负责按名字取出来。

这条边界要守住 —— 一旦注册表自己开始判断该加载什么，
「普通聊天背上全量上下文」就会悄悄发生，而这件事从账单以外的地方看不出来。

## 一个 Provider 挂了不能拖垮一轮对话

`get_context_set` 对每个 Provider 单独兜异常。基类的 `get_state` 已经会把
拉取失败转成 stale 或 `available: False`，这里再兜一层是防 Provider 自己写崩
（比如 `render` 里抛了）。**兜住不等于吞掉**：异常照样记 warning，
World State 里也会留下痕迹。
"""

from __future__ import annotations

import logging
from typing import Any

from context.base import BaseContextProvider, Turn
from context.cache import ContextCache

logger = logging.getLogger(__name__)

#: 渲染出来的 Context 文本默认字符预算。
#: 架构文档第十二节：「不要把整个 World State 的 JSON 原样塞进去 ——
#: 那是给程序看的，给模型看要挑重点，几百字以内。」
#: 这段在缓存断点之后，每个字都按未命中价付费，所以要卡死上限。
#:
#: 🔴 800 → 1200（2026-09-08）。**这个数是在 MemoryProvider 解禁之前定的。**
#: 2026-09-05 记忆一解禁就往每轮上下文里加了约 266 字，而预算没跟着动，
#: 于是排在第 9 位的 todo 每次都被挤掉 —— 48 小时 61 次，
#: 他有几十轮**根本看不见她今天要做什么**，而且不报错。
#:
#: 线上实测那一轮（新日志格式打出来的）：
#:     已用 604（memory 266、mood 167、health 97、location 43、time 26）
#:     + todo 252 = 856 —— 超 56 字，就为这 56 字整条丢掉。
#: 没有哪个 provider 是畸形的，是**预算本身过时了**。
#:
#: 抬到 1200 的代价（按 glm-5.3-flash 未命中价 0.8 元/M、80 轮/天算）：
#:     400 字 ≈ 267 token × 0.8/1e6 × 80 × 30 ≈ **0.5 元/月**。
#: 拿五毛钱换「他每天看得见她的待办」，这个账不用算第二遍。
#:
#: ⚠️ 但上限还是要有：这段每个字都是未命中价，provider 会一直加下去。
#: 再撞上限的时候，**先看那条 WARNING 里「吃得最多的」是谁**，
#: 而不是条件反射再抬一次 —— 抬预算是权宜，砍冗余才是正路。
DEFAULT_BUDGET = 1200


class ContextProviderRegistry:
    """按名字管理一组 Provider。"""

    def __init__(self, cache: ContextCache | None = None) -> None:
        # 同 base.py：不能写 `cache or ContextCache()` —— 空 ContextCache 是 falsy
        self.cache = cache if cache is not None else ContextCache()
        self._providers: dict[str, BaseContextProvider] = {}

    # ------------------------------------------------------------ 注册

    def register(self, provider: BaseContextProvider) -> BaseContextProvider:
        if provider.name in self._providers:
            raise ValueError(f"Provider {provider.name} 已经注册过了")
        # 统一用注册表这一份缓存，别让各家各存各的
        provider.cache = self.cache
        self._providers[provider.name] = provider
        logger.info("注册 Context Provider: %s（ttl=%s, section=%s）",
                    provider.name, provider.ttl, provider.section)
        return provider

    def get(self, name: str) -> BaseContextProvider | None:
        return self._providers.get(name)

    def names(self) -> list[str]:
        return list(self._providers)

    def __len__(self) -> int:
        return len(self._providers)

    def __contains__(self, name: object) -> bool:
        return name in self._providers

    # ------------------------------------------------------------ 取数据

    def resolve(self, names: list[str]) -> tuple[list[str], list[str]]:
        """把 Router 给的名单拆成「在场的」和「没注册的」。

        World State 的 `_meta.missing_providers` 要用它（架构文档第九节）。
        「这个 Provider 不在场」和「它返回了空」是两件事 —— 前者是配置问题，
        后者是真实状态，混在一起就没法排查「他为什么不知道家里的情况」。
        """
        known = [n for n in names if n in self._providers]
        missing = [n for n in names if n not in self._providers]
        return known, missing

    def get_context_set(
        self, names: list[str], turn: Turn | None = None, force_refresh: bool = False
    ) -> dict[str, dict[str, Any]]:
        """按 Router 给的名单取状态。不认识的名字跳过并记一笔。

        串行取。现在的 Provider 要么是本地计算（time/mood），要么带短超时，
        真到了「一轮要拉四五个远端」再换 ThreadPoolExecutor ——
        **不要为此引入 async**（架构文档 0.2）。
        """
        out: dict[str, dict[str, Any]] = {}
        for name in names:
            p = self._providers.get(name)
            if p is None:
                logger.warning("Router 要了一个没注册的 Provider: %s", name)
                continue
            try:
                out[name] = p.get_state(turn=turn, force_refresh=force_refresh)
            except Exception as exc:  # noqa: BLE001
                # 基类已经兜过拉取失败了，能漏到这儿说明 Provider 自己写崩了。
                # 记下来但别让一轮对话跟着挂
                logger.exception("Provider %s 自身异常", name)
                out[name] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        return out

    # ------------------------------------------------------------ 渲染

    def render(self, names: list[str], turn: Turn | None = None,
               force_refresh: bool = False, budget: int = DEFAULT_BUDGET) -> str:
        """取数据并拼成一段文本。

        ⚠️ **返回值只能塞进 `dynamic_system`，永远不许进 `system`。**
        静态前缀变一个字，那 12K 缓存整段作废，一轮 ¥0.00055 变 ¥0.011。
        调用方长这样：

            loop.run(text, system=STATIC_PREFIX,          # 冻住，别动
                     dynamic_system=registry.render([...]))

        `budget` 是字符预算（架构文档第十二节：「给模型看要挑重点，几百字以内」）。
        这段每轮都要发、而且在缓存断点之后 —— 意味着**每一个字都按未命中价付费**。
        Provider 一多就会悄悄膨胀，没人会发现，所以在这里卡死。
        超预算时按 Router 给的顺序保前面的（顺序即优先级），并明说截断了。
        """
        states = self.get_context_set(names, turn=turn, force_refresh=force_refresh)
        lines: list[str] = []
        used = 0
        dropped: list[tuple[str, int]] = []
        kept: list[tuple[str, int]] = []
        for name in names:                     # 按 Router 给的顺序，输出稳定
            state = states.get(name)
            if state is None:
                continue
            p = self._providers[name]
            try:
                text = p.render(state)
            except Exception:  # noqa: BLE001
                logger.exception("Provider %s 渲染失败", name)
                continue
            text = (text or "").strip()
            if not text:
                continue
            if used + len(text) > budget:
                dropped.append((name, len(text)))
                continue
            lines.append(text)
            used += len(text) + 1
            kept.append((name, len(text)))
        if dropped:
            # 不许悄悄少说 —— 让他知道有东西没给他看到，比让他以为看全了要好
            #
            # 🔴 **连「谁吃掉的」一起打**（2026-09-08 补）。原来只报丢了谁，
            # 于是这条警告 48 小时里喊了 61 次、没有一次能直接查下去 ——
            # 想知道是谁把预算吃满的，只能去线上重放一轮。
            # 一条查不下去的警告和没有这条警告差不多。
            eat = "、".join(f"{n} {c}" for n, c in
                            sorted(kept, key=lambda x: -x[1])[:5])
            logger.warning(
                "Context 超出 %d 字预算，这轮略过: %s（已用 %d，吃得最多的：%s）",
                budget, "、".join(f"{n}({c}字)" for n, c in dropped), used, eat)
            #: 给他看的那句只说名字 —— 字数是运维信息，跟他没关系
            lines.append(
                f"（还有 {'、'.join(n for n, _ in dropped)} 的情况没放进来，需要就问我）")
        return "\n".join(lines)

    # ------------------------------------------------------------ 其它

    def invalidate(self, name: str | None = None) -> int:
        return self.cache.invalidate(name)

    def stats(self) -> dict[str, Any]:
        """给 /health 用。命中率低就意味着每轮都在真打外部服务。"""
        return {
            "providers": {n: {"ttl_s": p.ttl.total_seconds(), "section": p.section}
                          for n, p in self._providers.items()},
            "cache": self.cache.stats(),
        }
