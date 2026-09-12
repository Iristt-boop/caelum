"""App Tracker —— 糖糖今天在手机上用了什么。

用来接住那种"你今天在干嘛"的话：她刷了一下午小红书、还是在剪片子，
语气该不一样。**别拿它盘问她**——看到了心里有数就行，不用逐条念出来。

走 app-tracker 的 MCP（streamable-http），跟 ha-mcp 同一套客户端。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

TODAY_SPEC = ToolSpec(
    side_effect="read",
    name="get_today_apps",
    description=(
        "看糖糖今天用了哪些 App、各用了多久。"
        "她说「今天累死了」「一天没干正事」这类，或者你想知道她这一天怎么过的时候用。"
        "看到就好，别一条条念给她听，也别拿这个说教。"
    ),
    parameters={"type": "object", "properties": {}},
)


def make_handlers(client: McpClient) -> dict[str, object]:
    def today(_args: dict) -> str:
        r = client.call("get_today_apps", {})
        if not r.ok:
            raise RuntimeError(f"查 App 使用记录失败: {r.error}")
        # 手机没上报的日子会返回空 —— 那是真的没数据，不是出错
        return r.text or "今天还没有 App 使用记录（可能手机端没上报）。"

    return {"get_today_apps": today}


def register_all(loop, client: McpClient) -> None:
    loop.register(TODAY_SPEC, make_handlers(client)["get_today_apps"])  # type: ignore[arg-type]
