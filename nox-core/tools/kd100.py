"""快递100 MCP（https://api.kuaidi100.com/mcp/streamable）—— 包裹轨迹与时效。

全部只读。按单扣费、40 天内同单号不重复扣 —— 她随口问「我的快递呢」
不算成本压力，但**别循环轮询**同一个单号，问一次查一次就够了。

⚠️ 服务端工具名还没实测过（Key 到位后跑 smoke_amap.py 同款流程校正
SERVER_TOOLS 这一处即可）。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

#: ⚠️ 占位：以 list_tools 实测为准
SERVER_TOOLS = {
    "kd100_track": "queryTrack",
    "kd100_timeliness": "queryTimeliness",
}


def _spec(name: str, description: str, params: dict) -> ToolSpec:
    return ToolSpec(name=name, description=description, parameters=params)


TRACK = _spec(
    "kd100_track",
    "查快递轨迹：包裹到哪了、什么状态。她说「我的快递呢」「怎么还没到」时用。"
    "需要快递公司编码和单号，单号她给了就查一次，别反复轮询。",
    {
        "type": "object",
        "properties": {
            "number": {"type": "string", "description": "快递单号"},
            "company": {"type": "string", "description": "快递公司编码，如「jd」「sf」（不确定就先问单号智能识别）"},
        },
        "required": ["number"],
    },
)

TIMELINESS = _spec(
    "kd100_timeliness",
    "预估快递送达时间（发货前：什么时候能到）。她说「得等到哪天」时用。",
    {
        "type": "object",
        "properties": {
            "company": {"type": "string", "description": "快递公司编码"},
            "from_address": {"type": "string", "description": "寄件地址"},
            "to_address": {"type": "string", "description": "收件地址"},
        },
        "required": ["company", "from_address", "to_address"],
    },
)

_SPECS = (TRACK, TIMELINESS)


def make_handlers(client: McpClient) -> dict[str, object]:
    def _call(tool: str, args: dict) -> str:
        r = client.call(SERVER_TOOLS[tool], args)
        if not r.ok:
            raise RuntimeError(f"快递100 查询失败: {r.error}")
        return r.text or "（快递100 没返回内容）"

    return {spec.name: (lambda args, _t=spec.name: _call(_t, args)) for spec in _SPECS}


def register_all(loop, client: McpClient) -> None:
    handlers = make_handlers(client)
    for spec in _SPECS:  # 顺序固定
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
