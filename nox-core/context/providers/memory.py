"""MemoryProvider —— 按当前话题从 Ombre Brain 检索相关记忆。

## 封了一个月，2026-09-05 解禁 —— 因为封它的两条理由都失效了

原来的两条（写于 2026-08 初）：

| 当初 | 现在 |
|---|---|
| OB 检索一次 **约 7 秒** | **约 650ms**（VPS 241 桶实测热态）|
| 每轮注入不同记忆 → 缓存 98.9%→62.5%、成本 ×12 | **不成立了** |

第二条是被 2026-08-26 那次修掉的：动态块从 `system` 挪到
**历史之后、最后一条 user 之前**，实测命中 0% → 99%
（PROJECT.md 第十九节，`tests/test_adapter_cache_layout.py` 盯着位置）。
「每轮都变的东西会砸缓存」这个前提本身没了。

第一条的账要算清楚，**别照抄「440 倍」那个数**：9-05 的矩阵化只优化了
**向量相似度那一步**，而瓶颈早就换人了。2026-09-05 在 VPS 上量的热态：

    关键词通道（rapidfuzz 四维评分）   340-400 ms   ← 现在的瓶颈
    向量通道（embedding 往返 + 矩阵）  270-310 ms
    脱水（content-hash 缓存命中）      ~1 ms/条
    ────────────────────────────────────────────
    端到端                             约 650 ms（冷启动首次 1.2s+）

配上下面 5 分钟的 TTL，最坏是每 5 分钟付一次 650ms。

## 🔴 但仍然不是「每轮都带」

糖糖定的决策 7 那句「最好轻量、响应快」没有过期。
所以加载条件由**理解层**驱动（`router/intent.py:classify_context`）：
她说了有意义的话、或者他心里正搁着一件事，才去翻记忆；
问天气、点歌、「今天几号」照旧一次 OB 都不碰。

## 只读，不写

长期记忆的存储、更新、归档属于 Ombre Brain 自己（架构文档第十节）。
这里只负责「按当下检索 + 格式化」，而且走 `touch=False, drift=False`
的只读路径 —— 理由见 `_fetch` 里那段。写记忆走现有的 MCP 工具。

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

import json
import logging
import re
from datetime import timedelta
from typing import Any

from context.base import BaseContextProvider, Turn
from memory.ob_client import OmbreBrain, split_breath

logger = logging.getLogger(__name__)

#: OB「什么都没检索到」时返回的句子（抄自 `Ombre-Brain/server.py` 的
#: return 分支，不是猜的）。命中就是**空结果**，这轮什么都不说。
#:
#: ⚠️ 用 startswith 是因为有几条带变量（「没有重要度 >= 7 的记忆。」）。
#: OB 那边改了文案这里就会失灵 —— 代价是退回旧行为（把它当记忆渲染），
#: 所以 `test_memory_provider.py` 里拿这几句原文钉着。
_OB_EMPTY = (
    "未找到相关记忆",
    "没有可以展示的记忆",
    "没有重要度",
    "没有留下过 feel",
    "权重池平静",
)

#: OB **坏了**时返回的句子。和上面那组的区别是死的：
#: 「没找到」是答案，「挂了」是没有答案 —— 后者必须抛，
#: 让基类退回上一次的旧记忆并标 stale，而不是让他以为自己忘光了。
_OB_BROKEN = (
    "记忆系统暂时无法访问",
    "检索过程出错",
)

#: 最多给他几条。见下面 `_digest` 那段账。
MAX_ITEMS = 4

#: 每条最长多少字。超了截断 —— 一条记忆讲不完的事，不该在这里讲完
MAX_ITEM_CHARS = 70

#: 记忆桶的标题：`[bucket_id:xxx] 📌 记忆桶: 小克的接纳 [主题:...] [情感:...]`
_TITLE = re.compile(r"记忆桶[:：]\s*([^\[\n]+)")


def _clip(line: str) -> str:
    """超长就截断，**并且留个记号**。

    🔴 不留记号的话他会把半句话当完整信息读。实测截出来过
    「糖糖定了分工：聊天用 Fable，修代码用 Opus。她说"Sonnet 是我的白月光，Fable 是」
    —— 后半句没了，而他不知道后半句没了。
    """
    line = line.strip()
    return line if len(line) <= MAX_ITEM_CHARS else line[:MAX_ITEM_CHARS] + "…"


def _digest(body: str) -> list[str]:
    """把 OB 返回的一大坨变成几行人话。

    ## 🔴 为什么必须做这一步（2026-09-05 解禁时才发现）

    OB 的脱水结果是**格式化 JSON**，不是一句话：

        [语义关联] [bucket_id:1cae7095d021] 📌 记忆桶: 小克的接纳 [主题:人际, 心理]
        {
          "core_facts": ["…", "…", "…", "…"],
          "emotion_state": "被接纳、温暖",
          "todos": [], "keywords": ["…"],
          "summary": "一句话"
        }

    6 条 = **1866 字符**。而动态块总预算 800，`registry.render()` 会
    整段丢掉它、只留一条 warning —— 表现成「解禁了但他还是想不起来」，
    而且日志不看就永远发现不了。

    `recall_memory` 工具那条路无所谓（模型当工具结果读 JSON 没问题），
    Provider 这条路是**每轮都要付钱的上下文**，只取 `summary` 那一句。

    解析不出来就退回第一行非 JSON 的文本 —— 宁可给他一句糙的，
    也不要因为格式变了就静默丢掉一条记忆。
    """
    out: list[str] = []
    for block in body.split("\n---\n"):
        block = block.strip()
        if not block:
            continue
        title_m = _TITLE.search(block)
        title = title_m.group(1).strip() if title_m else ""

        summary = ""
        start = block.find("{")
        if start >= 0:
            try:
                summary = str(json.loads(block[start:]).get("summary") or "").strip()
            except Exception:  # noqa: BLE001
                # JSON 坏了不是致命的，下面还有一条退路
                summary = ""
        if not summary:
            # 🔴 退路：这一块没有 JSON（OB 换了格式，或者脱水降级成了原文）。
            #
            # ⚠️ 这里**必须把每一行都留下**，不能只取第一行 ——
            # 「解析不出来就少给几条」是最难查的那种故障：
            # 他答不上来，而日志里什么都没有。第一版就是只取第一行，
            # 被 `test_only_keeps_dynamic_not_core` 当场逮住。
            for ln in block.splitlines():
                ln = ln.strip()
                # `===` 是 OB 的分节符（「=== 浮现记忆 ===」）。没有核心准则
                # 那一段时 split_breath 会把它原样留下 —— 漏进去就会
                # 直接出现在他的提示词里
                if not ln or ln.startswith(("{", "}", '"', "[", "===")):
                    continue
                if "记忆桶" in ln:
                    continue
                out.append(_clip(ln))
            continue

        line = f"{title}：{summary}" if title else summary
        out.append(_clip(line))
    return out


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

        # 🔴 **只读检索**（2026-09-05 解禁时加）。
        #
        # touch=False：这是被动带出来的记忆，不是「他想起来了」。
        #   touch 会推高 activation_count + 重置衰减，而打分里有 activation^0.3
        #   —— 每轮自动检索都 touch 等于持续给一批记忆续命，旧的永不归档。
        # drift=False：关掉 OB 那个「忽然想起来」的随机浮现。
        #   他主动回忆时那是浪漫；每轮自动注入时是噪声 ——
        #   同一句话问两次拿到不同的旧记忆，而且它进的是要付钱的 dynamic 块。
        #
        # `recall_memory` 工具照旧两个都 True，那条路才是他主动回忆。
        r = self.ob.breath(query=query, max_results=self.max_results,
                           touch=False, drift=False)
        if not r.ok:
            # 失败就抛，基类会退回上一次的旧记忆并标 stale。
            # 不许吞成空 —— 「没检索到」和「检索挂了」是两件事
            raise RuntimeError(f"OB 检索失败: {r.error}")

        _core, dynamic = split_breath(r.text or "")

        # 🔴 OB 的兜底文案不是记忆（2026-09-05 解禁时发现的真 bug）。
        #
        # 它检索不到时返回的是一句**中文句子**而不是空串，而 `split_breath`
        # 没有 header 就把整段归到 dynamic —— 于是它会被当成一条记忆渲染成：
        #
        #     【记起来的】
        #     未找到相关记忆。
        #
        # 雪藏期间这个洞不发作（planner 一天才用一两次）；一解禁就是
        # 每次检索落空都往他脑子里塞一句废话，还占着要付钱的 dynamic 预算。
        #
        # ⚠️ **「没找到」和「挂了」必须分开处理**（第十九节第 3 条：失败必须可见）：
        # 没找到 → 空结果，这轮什么都不说；
        # 挂了   → 抛出去，让基类退回上一次的旧记忆并标 stale。
        # 混成一谈的话，OB 宕机会表现成「他忽然什么都不记得了」，而且悄无声息。
        body = dynamic.strip()
        if any(body.startswith(s) for s in _OB_BROKEN):
            raise RuntimeError(f"OB 检索异常: {body[:60]}")
        if any(body.startswith(s) for s in _OB_EMPTY):
            return {"relevant": [], "query": query, "count": 0}

        items = _digest(body)[:MAX_ITEMS]
        return {
            "relevant": items,
            "query": query,
            "count": len(items),
        }

    def render(self, state: dict[str, Any]) -> str:
        if state.get("available") is False or not state.get("relevant"):
            return ""
        body = "\n".join(f"· {ln}" for ln in state["relevant"])
        head = "【记起来的】"
        if state.get("stale"):
            # 让他知道这是旧的，别把陈年检索当此刻刚想起来的说
            head = "【记起来的（可能不是最新的）】"
        # 🔴 和 UnderstandingProvider 同一条叮嘱：这是**背景**不是台词。
        # 不加这句的话他会把检索结果念出来 ——「我记得你说过…」连着三条，
        # 那不是记性好，那是在背卡片
        return f"{head}\n{body}\n（这些是背景，别刻意提起，也别一条条复述。）"
