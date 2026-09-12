"""滴滴官方 MCP（https://mcp.didichuxing.com）—— 打车预估、链接、订单查询。

🔴 **边界（2026-09-05 糖糖定的）：他不下单。**
`taxi_create_order` / `taxi_cancel_order` **故意不注册** —— 他能做的最多是
算好价格、生成打车链接发给她，扣扳机的永远是她。要先接沙箱
（mcp-servers-sandbox，Mock 数据）跑通，再切生产。

坐标红线（滴滴服务端强制）：经纬度必须是**字符串**「经度,纬度」，
且必须用 amap_search_poi 实时查，不许凭记忆给 —— 服务端会报 -32010。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

SERVER_TOOLS = {
    "didi_estimate": "taxi_estimate",
    "didi_ride_link": "taxi_generate_ride_app_link",
    "didi_order_status": "taxi_query_order",
}


#: 每个工具的副作用声明（审计 3.1）。**新加工具必须在这里登记**，
#: 否则 `_spec()` 直接抛 —— 炸在启动，好过某天悄悄下了一单。
_EFFECTS: dict[str, tuple[str, str | None]] = {
    "didi_estimate": ("read", None),
    "didi_ride_link": ("write", "链接/卡片发给她，她自己在对方 App 里点确认"),
    "didi_order_status": ("read", None),
}


def _spec(name: str, description: str, params: dict) -> ToolSpec:
    try:
        effect, via = _EFFECTS[name]
    except KeyError:  # noqa: PERF203
        raise ValueError(
            f"{name} 没有在 didi._EFFECTS 里声明副作用。"
            "拿不准就往重了标：花钱填 spend，撤不回来填 irreversible。"
        ) from None
    return ToolSpec(
        name=name, description=description, parameters=params,
        side_effect=effect, confirm_via=via,
    )


ESTIMATE = _spec(
    "didi_estimate",
    "查打车价格预估和可用车型。「打个车过去多少钱」「叫车贵不贵」时用。"
    "起终点坐标先用 amap_search_poi 查好（字符串「经度,纬度」）。",
    {
        "type": "object",
        "properties": {
            "origin": {"type": "string", "description": "起点，「经度,纬度」字符串"},
            "destination": {"type": "string", "description": "终点，「经度,纬度」字符串"},
        },
        "required": ["origin", "destination"],
    },
)

RIDE_LINK = _spec(
    "didi_ride_link",
    "生成一个打车链接发给她 —— 她点开就能在滴滴 App 里确认叫车。"
    "这是帮他叫车的**唯一**方式：把链接和预估价格一起发过去，让她自己点。"
    "**不许说「已经帮你叫好车了」。**",
    {
        "type": "object",
        "properties": {
            "origin": {"type": "string", "description": "起点，「经度,纬度」"},
            "destination": {"type": "string", "description": "终点，「经度,纬度」"},
        },
        "required": ["origin", "destination"],
    },
)

ORDER_STATUS = _spec(
    "didi_order_status",
    "查她滴滴订单的状态（司机信息、行程进度）。她提到「车来了吗」「司机到哪了」时用。",
    {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "订单号"},
        },
        "required": ["order_id"],
    },
)

_SPECS = (ESTIMATE, RIDE_LINK, ORDER_STATUS)


def make_handlers(client: McpClient) -> dict[str, object]:
    def _call(tool: str, args: dict) -> str:
        r = client.call(SERVER_TOOLS[tool], args)
        if not r.ok:
            raise RuntimeError(f"滴滴查询失败: {r.error}")
        return r.text or "（滴滴没返回内容）"

    return {spec.name: (lambda args, _t=spec.name: _call(_t, args)) for spec in _SPECS}


def register_all(loop, client: McpClient) -> None:
    handlers = make_handlers(client)
    for spec in _SPECS:  # 顺序固定
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
