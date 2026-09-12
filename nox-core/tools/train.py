"""12306 MCP（Joooook/12306-mcp，经 supergateway 桥）—— 火车票查询。

只查不买：余票、经停。12306 没有开放购票 API，查到的信息她自己去
App 下单。**境外 VPS 查 12306 可能被风控** —— 上线前先跑
tests/smoke_train.py 冒烟，被拦就别硬上（降级方案见 PROJECT.md 44 节）。

⚠️ 服务端工具名以 list_tools 实测为准（SERVER_TOOLS 一处改）。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

SERVER_TOOLS = {
    "train_tickets": "search_tickets",
    "train_interstation": "interstation-tickets",
}


#: 每个工具的副作用声明（审计 3.1）。**新加工具必须在这里登记**，
#: 否则 `_spec()` 直接抛 —— 炸在启动，好过某天悄悄下了一单。
_EFFECTS: dict[str, tuple[str, str | None]] = {
    "train_tickets": ("read", None),
    "train_interstation": ("read", None),
}


def _spec(name: str, description: str, params: dict) -> ToolSpec:
    try:
        effect, via = _EFFECTS[name]
    except KeyError:  # noqa: PERF203
        raise ValueError(
            f"{name} 没有在 train._EFFECTS 里声明副作用。"
            "拿不准就往重了标：花钱填 spend，撤不回来填 irreversible。"
        ) from None
    return ToolSpec(
        name=name, description=description, parameters=params,
        side_effect=effect, confirm_via=via,
    )


TICKETS = _spec(
    "train_tickets",
    "查 12306 火车余票：车次、出发到达时间、历时、各席别余票和票价。"
    "她说「周末回去的票还有吗」时用。只查票，购票她自己去 12306 App。",
    {
        "type": "object",
        "properties": {
            "from_station": {"type": "string", "description": "出发站，如「北京南」"},
            "to_station": {"type": "string", "description": "到达站，如「上海虹桥」"},
            "date": {"type": "string", "description": "日期，如「2026-09-12」（默认今天）"},
        },
        "required": ["from_station", "to_station", "date"],
    },
)

INTERSTATION = _spec(
    "train_interstation",
    "查某个车次沿途经停站和到发时刻。「这趟车经过哪些站」时用。",
    {
        "type": "object",
        "properties": {
            "train_no": {"type": "string", "description": "车次号，如「G103」"},
            "from_station": {"type": "string", "description": "出发站"},
            "to_station": {"type": "string", "description": "到达站"},
            "date": {"type": "string", "description": "日期（默认今天）"},
        },
        "required": ["train_no", "from_station", "to_station", "date"],
    },
)

_SPECS = (TICKETS, INTERSTATION)


def make_handlers(client: McpClient) -> dict[str, object]:
    def _call(tool: str, args: dict) -> str:
        r = client.call(SERVER_TOOLS[tool], args)
        if not r.ok:
            raise RuntimeError(f"12306 查询失败: {r.error}")
        return r.text or "（12306 没返回内容）"

    return {spec.name: (lambda args, _t=spec.name: _call(_t, args)) for spec in _SPECS}


def register_all(loop, client: McpClient) -> None:
    handlers = make_handlers(client)
    for spec in _SPECS:  # 顺序固定
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
