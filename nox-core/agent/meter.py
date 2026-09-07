"""utility 用量记账 —— 让看不见的那一半变得看得见。

## 起因

糖糖 2026-09-06 问「utility 的命中要优化吗」，我答不上来 ——
因为 `bridge/server.js` 的 `usage_log` 那行写在 **SSE done 帧**里，
**只记聊天主链路**。而 utility 一天跑 166+ 次（实测：压缩 59 /
话题池 96 / 意义推断 11），比聊天还多，却一个数都没有。

「配上了 ≠ 用上了」的姊妹篇：**跑着了 ≠ 看得见**。

## 为什么回头打 bridge，而不是自己存一份

用量只该有一个真源。分两处存的话，Console 页要么只显示一半（现状），
要么得去合并两个库 —— 而 R3 明令 nox-core 不许直读别人的 SQLite。

## 🔴 绝不许影响主线

压缩、意义推断、话题过滤本身都是**增强**：它们失败了对话照常。
记账比它们更边缘，所以：

  - 后台线程发，不阻塞调用方
  - 任何异常都吞在里面（只留日志）
  - bridge 没配 / 挂了 → 静默跳过，不重试不排队

一个记账动作把主线拖慢，是本末倒置。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from agent.llm import Message, ToolSpec, Turn, Usage

logger = logging.getLogger(__name__)

#: bridge 客户端的取值函数。`api/server.py` 起来之后设进来 ——
#: `Nox.__init__` 里造 adapter 的时候 bridge 还没准备好（同
#: ResonanceProvider 的 attention_ref）
_bridge_ref: Any = None


def bind(bridge_ref: Any) -> None:
    """把 bridge 客户端接进来。没接 = 不记账，行为完全不变。"""
    global _bridge_ref
    _bridge_ref = bridge_ref


def record(task: str, model: str, usage: Usage) -> None:
    """记一笔。**失败绝不冒泡。**"""
    if usage is None or (not usage.input_tokens and not usage.output_tokens):
        return
    bridge = _bridge_ref() if callable(_bridge_ref) else _bridge_ref
    if bridge is None:
        return

    def _send() -> None:
        try:
            bridge.post("/api/usage", {
                "task": task,
                "model": model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cached_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
            })
        except Exception as exc:  # noqa: BLE001
            # 🔴 不许静默（docs/LOGGING.md），但也只到 debug ——
            # 记账失败每次都 warning 会把日志刷满，而它并不影响任何行为
            logger.debug("记 utility 用量失败（不影响主线）：%s", exc)

    threading.Thread(target=_send, daemon=True).start()


class Metered:
    """给 adapter 套一层记账。**不改变任何行为。**

        light = meter.tag(core.router.light_adapter, "compaction")

    为什么是包一层而不是在每个调用点手写 record()：
    调用点关心的是「压缩这段话」，不是「记一笔账」。
    把记账混进去，下一个人加新的 utility 路径时一定会忘。

    ⚠️ 只代理 `complete`。utility 路径没有一个用 `stream` 的 ——
    真有那天，这里会 AttributeError 而不是静默漏记，那正是想要的。
    """

    def __init__(self, inner: Any, task: str) -> None:
        self._inner = inner
        self._task = task

    def __getattr__(self, name: str) -> Any:
        #: 其余属性（cfg / name / stream…）原样透出去
        return getattr(self._inner, name)

    def complete(self, messages: list[Message], tools: list[ToolSpec], **kw: Any) -> Turn:
        turn = self._inner.complete(messages, tools, **kw)
        try:
            model = getattr(getattr(self._inner, "cfg", None), "model", "") or ""
            record(self._task, model, turn.usage)
        except Exception:  # noqa: BLE001
            logger.debug("记账包装出错（不影响主线）", exc_info=True)
        return turn


def tag(adapter: Any, task: str) -> Any:
    """给 adapter 贴一个任务名。adapter 为 None 时原样返回。"""
    return Metered(adapter, task) if adapter is not None else adapter
