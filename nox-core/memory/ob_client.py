"""Ombre Brain 客户端。

OB 是个 FastMCP server（streamable-http），不是 REST —— 所以走 MCP 协议连，
不能拿 httpx 直接打。它的工具返回的是**格式化字符串**（给模型看的文本），
不是 JSON，所以这里不解析，原样交给上层注入 prompt。

一条硬规矩：**记忆层的故障绝不能让对话挂掉。**
浮现不出记忆，Nox 顶多显得健忘；抛异常穿透上去，糖糖就直接收不到回复了。
所以所有方法都不抛异常，失败返回 MemoryResult(ok=False)，由上层决定要不要
告诉她"我这会儿想不起来"。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MemoryResult:
    """一次记忆操作的结果。ok=False 时 text 是给日志看的原因，不要直接发给糖糖。"""

    ok: bool
    text: str = ""
    error: str | None = None

    def __bool__(self) -> bool:
        return self.ok


# breath 返回文本里的分段标记。OB 把钉选桶（核心准则）和动态浮现拼在
# 同一个字符串里返回，这里按标记切开。
_CORE_HEADER = "=== 核心准则 ==="
_DYNAMIC_HEADER = "=== 浮现记忆 ==="


@dataclass
class Recalled:
    """切分后的记忆。

    分开的理由是成本，不是洁癖：
      core    —— 37 个钉选桶，每次 breath 必定全量返回，约 8K tokens，
                 而且**内容几乎不变**。它属于 system prompt 的静态前缀，
                 应该标记 cache_control 让它常驻缓存 —— 命中价是基础价的
                 十分之一。每轮当新内容重发，一个月能多烧几十刀。
      dynamic —— 跟当下这句话相关的浮现，每轮都不同，只能老实付钱。
    """

    core: str = ""
    dynamic: str = ""
    ok: bool = True
    error: str | None = None

    @property
    def core_tokens_estimate(self) -> int:
        """粗估 token 数（中文约 1 字符 1 token，英文约 4 字符 1 token）。"""
        return len(self.core)

    def __bool__(self) -> bool:
        return self.ok


def split_breath(text: str) -> tuple[str, str]:
    """把 breath 的返回切成（核心准则, 动态浮现）。

    OB 的输出格式若变了，这里退化成「全部当动态」——宁可多付点钱，
    也不要因为切分失败把记忆整个丢掉。

    ⚠️ **这个退化只对 `recall()` 安全，对 `core_principles()` 不安全。**
    把内容当动态浮现，最坏是多付一轮钱；把它当核心准则，会冻进静态前缀
    变成永久自我指令。所以 `core_principles()` 不用这里的退化路径，
    它自己查头、查不到就返回空 —— 见那边的长注释。
    """
    if _CORE_HEADER not in text:
        return "", text.strip()

    after_core = text.split(_CORE_HEADER, 1)[1]
    if _DYNAMIC_HEADER in after_core:
        core, dynamic = after_core.split(_DYNAMIC_HEADER, 1)
        return core.strip(), dynamic.strip()
    return after_core.strip(), ""


class OmbreBrain:
    """OB 的同步封装。

    内部是异步的（MCP SDK 只有异步接口），对外给同步方法，因为 agent loop
    是同步的。每次调用建一次连接 —— OB 调用频率低（每轮对话一两次），
    这点开销换来的是不用维护后台事件循环和连接状态。
    真嫌慢了再改成常驻 session，但那要先有实测数据支撑。
    """

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.url = url
        self.timeout = timeout

    # ---------------------------------------------------------- 对外同步接口

    def breath(
        self,
        query: str = "",
        *,
        max_results: int = 8,
        max_tokens: int = 4000,
        domain: str = "",
        importance_min: int = -1,
        touch: bool = True,
        drift: bool = True,
    ) -> MemoryResult:
        """浮现记忆。不传 query = 自动浮现，传了 = 关键词检索。

        默认 max_results 压到 8（OB 默认 20）—— 每轮对话都要注入 prompt，
        20 条会把上下文撑得很大，而且大部分跟当下这句话无关。

        ## touch / drift：谁在回忆，决定要不要留下痕迹（2026-09-05）

            recall_memory 工具   他主动想起来的  → touch=True   该加强
            MemoryProvider      被动带出来的    → touch=False  不算

        `touch=True` 会推高 activation_count 并重置衰减，而 OB 的打分里
        有 `activation^0.3` —— 每轮自动检索都 touch 的话，等于持续给一批
        记忆续命，旧的永远归不了档，权重模型会被慢慢刷歪。

        `drift` 是 OB 那个「忽然想起来」（命中 <3 条时 40% 概率漂旧桶）。
        他主动回忆时那是浪漫；每轮自动注入时那是噪声 —— 同一句话问两次
        会拿到不同的旧记忆，而且它进的是每轮都要付钱的 dynamic 块。

        ⚠️ 两个默认值都是 True，**保持原行为** —— 现有调用方一个都不用改。
        """
        return self._call(
            "breath",
            {
                "query": query,
                "max_results": max_results,
                "max_tokens": max_tokens,
                "domain": domain,
                "importance_min": importance_min,
                "touch": touch,
                "drift": drift,
            },
        )

    def hold(
        self,
        content: str,
        *,
        tags: str = "",
        importance: int = 5,
        pinned: bool = False,
        feel: bool = False,
    ) -> MemoryResult:
        """存一条长期记忆。

        content 里尽量带上日期 —— OB 里记忆桶多了容易分不清先后，
        这是糖糖定的规矩。
        """
        if not content.strip():
            return MemoryResult(False, error="content 为空，不存")
        return self._call(
            "hold",
            {
                "content": content,
                "tags": tags,
                "importance": max(1, min(10, importance)),
                "pinned": pinned,
                "feel": feel,
            },
        )

    def grow(self, content: str) -> MemoryResult:
        """归档当天的重要内容（对话结束时调）。"""
        if not content.strip():
            return MemoryResult(False, error="content 为空，不归档")
        return self._call("grow", {"content": content})

    def pulse(self, include_archive: bool = False) -> MemoryResult:
        """看记忆系统的整体状态。"""
        return self._call("pulse", {"include_archive": include_archive})

    def trace(self, bucket_id: str, **kwargs: Any) -> MemoryResult:
        """改记忆桶的元数据或内容，也能删。

        OB 里唯一能**修改和删除**记忆的工具。参数只传要改的，不传就不动：
          resolved=1 沉底 / 0 激活
          pinned=1   钉选 / 0 取消
          digested=1 隐藏（保留但不再浮现）/ 0 取消
          content    替换正文
          delete=True 真删，不可逆
        """
        if not bucket_id:
            return MemoryResult(False, error="缺 bucket_id")
        return self._call("trace", {"bucket_id": bucket_id, **kwargs})

    def dream(self) -> MemoryResult:
        """读最近新增的记忆桶，供自省。

        OB 设计的用法是：dream 读出来 → 相关的用 merge 收拢 →
        过时的用 trace(resolved=1) 沉底。这是一条完整的整理链。
        """
        return self._call("dream", {})

    def merge(self, target_id: str, source_ids: list[str]) -> MemoryResult:
        """把若干个桶合并进一个。最多 5 个源。

        合并会累加标签、按内容长度加权重算 valence/arousal。
        钉选的桶不能作为合并目标（OB 那边会拒绝）。
        **不可逆** —— 源桶合并后就没了。
        """
        if not target_id or not source_ids:
            return MemoryResult(False, error="需要 target_id 和至少一个 source_id")
        if len(source_ids) > 5:
            return MemoryResult(False, error=f"最多 5 个源，给了 {len(source_ids)} 个")
        return self._call("merge", {"target_id": target_id, "source_ids": source_ids})

    def core_principles(self) -> MemoryResult:
        """取核心准则（钉选桶）。**整个会话只调一次**。

        实测：不带 query 的 breath 只返回钉选桶（约 11.5K 字符），
        带 query 的检索则完全不含这部分 —— 两种模式互斥，不会重复。

        这块内容几乎不变，属于 system prompt 的静态前缀，应该标记
        cache_control 常驻缓存。每轮重取重发，一个月要多烧几十刀。
        """
        r = self.breath("", max_results=50, max_tokens=20000)
        if not r.ok:
            return r

        # 🔴 **没有核心准则头的时候，必须返回空，不许退回 dynamic**
        #    （2026-09-12 修，审计 3.2 链 3b）
        #
        # 这行原来写的是 `core or dynamic`，理由是"万一 OB 改了输出格式，
        # 两段都留着别丢"。听起来稳，实际是这条链子的起点：
        #
        #   一个钉选桶都没有  →  OB 不输出 `=== 核心准则 ===` 头
        #                     →  split_breath 把整段归给 dynamic
        #                     →  `core or dynamic` 取到 dynamic
        #                     →  **随机浮现的记忆被当成「核心准则」**
        #                     →  冻进带缓存的静态前缀，渲染成
        #                        「=== 关于你和糖糖的核心记忆 ===」
        #                     →  此后每一轮都挂在 system prompt 里
        #
        # 也就是说：**模型自己某次随口浮现的东西，会变成他往后的自我指令。**
        # 「半年后他会不会偏离最初人格」的机制，有一条就是这个。
        #
        # 权衡很清楚：格式真变了的话，这里返回空 = 前缀里没有核心准则，
        # 缺失是**看得见**的（所以下面必须留一条 WARNING）；
        # 而退回 dynamic 的坏处是**看不见**，还会自我强化。
        # 宁可少一段，不可错一段。
        if _CORE_HEADER not in r.text:
            logger.warning(
                "breath 返回里没有「%s」—— 这次不装核心准则。"
                "要么一个钉选桶都没有，要么 OB 的输出格式变了；"
                "**不退回动态浮现**，那会把随机记忆冻成永久自我指令",
                _CORE_HEADER,
            )
            return MemoryResult(True, text="")

        core, _dynamic = split_breath(r.text)
        return MemoryResult(True, text=core)

    def recall(self, query: str, *, max_results: int = 6, max_tokens: int = 3000) -> MemoryResult:
        """按糖糖当下这句话检索相关记忆。**每轮调这个。**

        返回的是语义匹配到的记忆，不含核心准则 —— 那部分由
        core_principles() 在会话开始时取一次即可。
        """
        if not query.strip():
            return MemoryResult(True, text="")
        r = self.breath(query, max_results=max_results, max_tokens=max_tokens)
        if not r.ok:
            return r
        core, dynamic = split_breath(r.text)
        return MemoryResult(True, text=dynamic or core)

    def ping(self) -> MemoryResult:
        """连通性自检。启动时调一次，别等第一轮对话才发现连不上。"""
        return self.pulse()

    # -------------------------------------------------------------- 内部实现

    def _call(self, tool: str, args: dict[str, Any]) -> MemoryResult:
        try:
            return asyncio.run(self._acall(tool, args))
        except RuntimeError as exc:
            # 已经在事件循环里（比如被 FastAPI 的 async 路由直接调用）
            if "asyncio.run() cannot be called" in str(exc):
                return MemoryResult(
                    False,
                    error=f"{tool}: 不能在运行中的事件循环里调同步接口，请用 a{tool}()",
                )
            logger.warning("OB %s 失败: %s", tool, exc)
            return MemoryResult(False, error=f"{tool}: {type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 —— 记忆层的任何故障都不许穿透到对话
            logger.warning("OB %s 失败: %s", tool, exc)
            return MemoryResult(False, error=f"{tool}: {type(exc).__name__}: {exc}")

    async def _acall(self, tool: str, args: dict[str, Any]) -> MemoryResult:
        # 延迟导入：没装 mcp 包时，只有真正用到记忆层才报错
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def run() -> MemoryResult:
            async with streamablehttp_client(self.url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    resp = await session.call_tool(tool, args)

                    if getattr(resp, "isError", False):
                        return MemoryResult(False, error=f"{tool} 返回错误: {_text_of(resp)}")
                    return MemoryResult(True, text=_text_of(resp))

        try:
            return await asyncio.wait_for(run(), timeout=self.timeout)
        except asyncio.TimeoutError:
            return MemoryResult(False, error=f"{tool} 超时（{self.timeout}s）")

    # 异步版本，给 FastAPI 的 async 路由用
    async def abreath(self, query: str = "", **kwargs: Any) -> MemoryResult:
        args = {
            "query": query,
            "max_results": kwargs.get("max_results", 8),
            "max_tokens": kwargs.get("max_tokens", 4000),
            "domain": kwargs.get("domain", ""),
            "importance_min": kwargs.get("importance_min", -1),
        }
        try:
            return await self._acall("breath", args)
        except Exception as exc:  # noqa: BLE001
            return MemoryResult(False, error=f"breath: {type(exc).__name__}: {exc}")

    async def ahold(self, content: str, **kwargs: Any) -> MemoryResult:
        if not content.strip():
            return MemoryResult(False, error="content 为空，不存")
        args = {
            "content": content,
            "tags": kwargs.get("tags", ""),
            "importance": max(1, min(10, kwargs.get("importance", 5))),
            "pinned": kwargs.get("pinned", False),
            "feel": kwargs.get("feel", False),
        }
        try:
            return await self._acall("hold", args)
        except Exception as exc:  # noqa: BLE001
            return MemoryResult(False, error=f"hold: {type(exc).__name__}: {exc}")


def _text_of(resp: Any) -> str:
    """把 MCP 的 content 块拼成纯文本。OB 的工具都返回单个 text 块。"""
    parts: list[str] = []
    for block in getattr(resp, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()
