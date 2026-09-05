"""通用 MCP 客户端（streamable-http）。

Ombre Brain 和 ha-mcp 都是 FastMCP 的 streamable-http 服务，连接方式完全
一样。抽出来共用，免得两份几乎相同的代码各自演化 —— 今天刚在设备清单上
吃过这个亏（同一份数据散在三处，漏了三个设备才被发现）。

一条硬规矩：**不抛异常**。工具服务挂掉不该让对话崩，失败返回 ok=False，
由调用方决定怎么呈现给糖糖。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CallResult:
    ok: bool
    text: str = ""
    error: str | None = None

    def __bool__(self) -> bool:
        return self.ok


class McpClient:
    """一次调用建一次连接。

    这么选是因为调用频率低（每轮对话零到两次），换来的是不用维护后台事件
    循环和连接状态。真嫌慢了再改常驻 session，但要先有实测数据 ——
    实测 OB 单次调用约 7 秒，其中握手只占几百毫秒，瓶颈在服务端检索，
    改连接复用救不了。
    """

    def __init__(self, url: str, name: str = "mcp", timeout: float = 30.0) -> None:
        self.url = url
        self.name = name
        self.timeout = timeout

    def __repr__(self) -> str:
        # 脱敏 —— ha-mcp 的访问凭据在 URL 路径里，异常栈和 repr 都会泄露它
        masked = re.sub(r"/[0-9a-f]{16,}(?=/|$)", "/<token>", self.url)
        return f"McpClient(name={self.name!r}, url={masked!r})"

    def call(self, tool: str, args: dict[str, Any] | None = None) -> CallResult:
        try:
            return asyncio.run(self.acall(tool, args))
        except RuntimeError as exc:
            if "asyncio.run() cannot be called" in str(exc):
                return CallResult(
                    False,
                    error=f"{self.name}.{tool}: 不能在运行中的事件循环里调同步接口",
                )
            logger.warning("%s.%s 失败: %s", self.name, tool, exc)
            return CallResult(False, error=f"{self.name}.{tool}: {type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 —— 外部服务的故障不许穿透到对话
            logger.warning("%s.%s 失败: %s", self.name, tool, exc)
            return CallResult(False, error=f"{self.name}.{tool}: {type(exc).__name__}: {exc}")

    async def acall(self, tool: str, args: dict[str, Any] | None = None) -> CallResult:
        # 延迟导入：没装 mcp 包时，只有真用到才报错
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def run() -> CallResult:
            async with streamablehttp_client(self.url, headers=self.headers) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    resp = await session.call_tool(tool, args or {})
                    text = _text_of(resp)
                    if getattr(resp, "isError", False):
                        return CallResult(False, error=f"{tool} 返回错误: {text}")
                    return CallResult(True, text=text)

        try:
            return await asyncio.wait_for(run(), timeout=self.timeout)
        except asyncio.TimeoutError:
            return CallResult(False, error=f"{self.name}.{tool} 超时（{self.timeout}s）")

    def list_tools(self) -> CallResult:
        """列出服务端有哪些工具。启动自检用 —— 别等第一次调用才发现连不上。"""
        try:
            return asyncio.run(self._alist())
        except Exception as exc:  # noqa: BLE001
            return CallResult(False, error=f"{self.name}: {type(exc).__name__}: {exc}")

    async def _alist(self) -> CallResult:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def run() -> CallResult:
            async with streamablehttp_client(self.url, headers=self.headers) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    resp = await session.list_tools()
                    names = [t.name for t in resp.tools]
                    return CallResult(True, text=", ".join(names))

        try:
            return await asyncio.wait_for(run(), timeout=self.timeout)
        except asyncio.TimeoutError:
            return CallResult(False, error=f"{self.name} 列工具超时")


def _text_of(resp: Any) -> str:
    parts: list[str] = []
    for block in getattr(resp, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()
