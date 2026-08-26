"""MemoryProvider —— 按当前话题从 Ombre Brain 检索相关记忆。

## ⚠️ 写好了，但**故意没接进每轮的名单**

架构文档第八节把 `memory` 放进了**所有**请求类型的 Context 集合，
连「普通聊天」也是 `chat + memory`。**这条不采纳**，原因是它写于实测之前：

| | 代价 |
|---|---|
| OB 检索一次 | **约 7 秒**（瓶颈在服务端语义检索，不是连接开销）|
| 每轮注入不同记忆 | `dynamic_system` 每轮都变 → DeepSeek 的自动前缀缓存里，
  它后面的 history 和当轮消息**全部错位**，命中率 98.9% → 62.5% |
| 折算每轮成本 | ¥0.00034 → 约 ¥0.0043，**12 倍** |

而糖糖定的决策 7（PROJECT.md 第十九节）原话是：
「需要的时候我让他调用 ob breath 就行，最好轻量、响应快、缓存命中高。」
现在闲聊路径**一次 OB 都不碰**，2.7 秒、缓存 98.9%。

**后定的、有数据支撑的那个说了算。** 日常对话继续走 `recall_memory` 工具，
模型自己判断要不要回忆 —— 该花 7 秒的时候才花。

## 那它什么时候用

Daily Planner（Phase 2 之后）那种**一天触发一两次**的场景。
那里 7 秒完全可接受，而且本来就要跨多个 Provider 聚合一次。
到时候在调用方的名单里加一个 `"memory"` 就能启用，代码不用改。

## 只读，不写

长期记忆的存储、更新、归档属于 Ombre Brain 自己（架构文档第十节）。
这里只负责「按当下检索 + 格式化」。写记忆走现有的 MCP 工具，
两套逻辑混在一起以后没人分得清是谁写坏的。

## 不要核心准则，只要动态浮现

`breath()` 返回的 core（37 个钉选桶，约 8K tokens）**已经在静态前缀里了** ——
Core 启动时取一次冻住，正是靠它撑起 98.9% 的命中率。
这里再塞一遍就是花钱买重复。所以只取 `dynamic` 那一半。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from context.base import BaseContextProvider, Turn
from memory.ob_client import OmbreBrain, split_breath

logger = logging.getLogger(__name__)


class MemoryProvider(BaseContextProvider):
    """按当前话题检索相关记忆。**只读。**"""

    name = "memory"
    section = "memory"
    # 5 分钟（架构文档的缓存策略表）。
    #
    # 这里**故意不标 volatile**，尽管检索结果确实跟当轮的话有关 ——
    # 标了就是每轮 7 秒，那是这个 Provider 最贵的失败模式。
    # 5 分钟内话题通常是连续的，复用上一次的检索结果够用；
    # 真要精确匹配当下这句，他有 recall_memory 工具。
    ttl = timedelta(minutes=5)

    #: 检索几条。默认 8 —— 20 条会把上下文撑大，而且大半跟当下无关
    max_results = 6

    def __init__(self, ob: OmbreBrain, **kw: Any) -> None:
        super().__init__(**kw)
        self.ob = ob

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        query = (turn.text or "").strip()
        if not query:
            # 没有话题就别去检索 —— 不带 query 的 breath() 返回的是钉选桶，
            # 那份已经在静态前缀里了，白花 7 秒
            return {"relevant": [], "query": "", "skipped": "没有可检索的话题"}

        r = self.ob.breath(query=query, max_results=self.max_results)
        if not r.ok:
            # 失败就抛，基类会退回上一次的旧记忆并标 stale。
            # 不许吞成空 —— 「没检索到」和「检索挂了」是两件事
            raise RuntimeError(f"OB 检索失败: {r.error}")

        _core, dynamic = split_breath(r.text or "")
        items = [ln.strip() for ln in dynamic.splitlines() if ln.strip()]
        return {
            "relevant": items,
            "query": query,
            "count": len(items),
        }

    def render(self, state: dict[str, Any]) -> str:
        if state.get("available") is False or not state.get("relevant"):
            return ""
        body = "\n".join(state["relevant"])
        head = "【记起来的】"
        if state.get("stale"):
            # 让他知道这是旧的，别把陈年检索当此刻刚想起来的说
            head = "【记起来的（可能不是最新的）】"
        return f"{head}\n{body}"
