"""高德官方 MCP（https://mcp.amap.com/mcp）—— 地点智能给对话用。

感知层（LocationProvider）已经知道她在哪、那是什么地方；这一层管的是
**她开口要**的时候：附近有什么、怎么走、天气如何。全部只读查询。

坐标红线（滴滴和高德都一样）：经纬度让 amap_search_poi / amap_search_nearby
现场查，不许凭记忆编 —— 编出来的坐标会把人带到错误的地方。
服务端工具名以 list_tools 实测为准（SERVER_TOOLS 一处改）。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

#: 我们的 LLM 工具名 → 高德服务端的工具名。名字对不上会在调用时报
#: 「unknown tool」，拿 smoke_amap.py 的 list_tools 结果来校正这一处。
SERVER_TOOLS = {
    "amap_search_poi": "maps_text_search",
    "amap_search_nearby": "maps_around_search",
    "amap_route_driving": "maps_direction_driving",
    "amap_route_transit": "maps_direction_transit_integrated",
    "amap_weather": "maps_weather",
}


def _spec(name: str, description: str, properties: dict) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        parameters={"type": "object", "properties": properties},
    )


SEARCH_POI = _spec(
    "amap_search_poi",
    "按关键词搜地点，拿到名称、地址和经纬度。她说「找个咖啡馆」「那家店在哪」，"
    "或者要查任何地点的坐标时用。keywords 必填；city 给城市名能显著更准。",
    {
        "type": "object",
        "properties": {
            "keywords": {"type": "string", "description": "搜索关键词，如「咖啡馆」「西二旗地铁站」"},
            "city": {"type": "string", "description": "城市名，如「北京」，可选"},
        },
        "required": ["keywords"],
    },
)

SEARCH_NEARBY = _spec(
    "amap_search_nearby",
    "按坐标搜周边的地点。她问「附近有什么」「这附近哪能吃饭」时用。"
    "经纬度先用 amap_search_poi 查，或者用环境里她的位置。",
    {
        "type": "object",
        "properties": {
            "keywords": {"type": "string", "description": "要找什么，如「面馆」「药店」"},
            "longitude": {"type": "string", "description": "中心点经度"},
            "latitude": {"type": "string", "description": "中心点纬度"},
        },
        "required": ["keywords", "longitude", "latitude"],
    },
)

ROUTE_DRIVING = _spec(
    "amap_route_driving",
    "查驾车路线：距离、耗时、路线概要。「开车去那儿多久」「怎么走」时用。"
    "起终点坐标先用 amap_search_poi 查，别编。",
    {
        "type": "object",
        "properties": {
            "origin_longitude": {"type": "string", "description": "起点经度"},
            "origin_latitude": {"type": "string", "description": "起点纬度"},
            "destination_longitude": {"type": "string", "description": "终点经度"},
            "destination_latitude": {"type": "string", "description": "终点纬度"},
        },
        "required": ["origin_longitude", "origin_latitude",
                     "destination_longitude", "destination_latitude"],
    },
)

ROUTE_TRANSIT = _spec(
    "amap_route_transit",
    "查公交/地铁路线（含跨市）。「坐地铁怎么去」「不开车的话怎么走」时用。"
    "起终点坐标先用 amap_search_poi 查，city 必填。",
    {
        "type": "object",
        "properties": {
            "origin_longitude": {"type": "string", "description": "起点经度"},
            "origin_latitude": {"type": "string", "description": "起点纬度"},
            "destination_longitude": {"type": "string", "description": "终点经度"},
            "destination_latitude": {"type": "string", "description": "终点纬度"},
            "city": {"type": "string", "description": "所在城市，如「北京」"},
        },
        "required": ["origin_longitude", "origin_latitude",
                     "destination_longitude", "destination_latitude", "city"],
    },
)

WEATHER = _spec(
    "amap_weather",
    "查一个城市的实时天气和未来预报。她出门前、约在室外、或者随口问天气时用。",
    {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名或 adcode，如「北京」"},
        },
        "required": ["city"],
    },
)

_SPECS = (SEARCH_POI, SEARCH_NEARBY, ROUTE_DRIVING, ROUTE_TRANSIT, WEATHER)


def make_handlers(client: McpClient) -> dict[str, object]:
    def _call(tool: str, args: dict) -> str:
        server_tool = SERVER_TOOLS[tool]
        r = client.call(server_tool, args)
        if not r.ok:
            raise RuntimeError(f"高德查询失败: {r.error}")
        return r.text or "（高德没返回内容）"

    return {spec.name: (lambda args, _t=spec.name: _call(_t, args)) for spec in _SPECS}


def register_all(loop, client: McpClient) -> None:
    handlers = make_handlers(client)
    for spec in _SPECS:  # 顺序固定 —— 工具定义是缓存前缀的一部分
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
