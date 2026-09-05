"""麦当劳官方 MCP（https://mcp.mcd.cn/mcp-servers/mcd-mcp）—— 麦麦的点餐周边。

🔴 **边界（与滴滴同一条）：不接任何下单/写入工具。**
create-order / party-order-create / mall-create-order / draw-lottery /
auto-bind-coupons / delivery-create-address 全部**故意不注册** ——
他可以查门店、翻菜单、算价格、看你的券和订单，但「下单买汉堡」
必须你自己来。想放开的话改 SERVER_TOOLS 白名单前先跟糖糖说。

鉴权：Bearer Token（open.mcd.cn/mcp 申请，绑定她的账号），
经 McpClient 的 headers 传入。查询她的券/订单 = 她自己的数据，只读不写。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

SERVER_TOOLS = {
    "mcd_nearby_stores": "query-nearby-stores",
    "mcd_menu": "query-meals",
    "mcd_meal_detail": "query-meal-detail",
    "mcd_price": "calculate-price",
    "mcd_order": "query-order",
    "mcd_orders": "order-list",
    "mcd_my_coupons": "query-my-coupons",
    "mcd_available_coupons": "available-coupons",
    "mcd_campaign": "campaign-calendar",
}


def _spec(name: str, description: str, params: dict) -> ToolSpec:
    return ToolSpec(name=name, description=description, parameters=params)


NEARBY = _spec(
    "mcd_nearby_stores",
    "搜麦当劳门店（按城市/关键词）。「附近有麦当劳吗」「哪家店还开着」时用。"
    "返回门店编码（storeCode），后续查菜单/算价格都要用它。",
    {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名，如「北京」"},
            "keyword": {"type": "string", "description": "门店名关键词，可选"},
            "searchType": {"type": "integer", "description": "搜索类型（默认 1）"},
            "beType": {"type": "integer", "description": "1-到店自取（默认）"},
        },
        "required": ["beType", "searchType"],
    },
)

MENU = _spec(
    "mcd_menu",
    "看某家麦当劳门店的菜单（餐品列表）。「麦当劳有什么」「看看套餐」时用。"
    "storeCode 来自 mcd_nearby_stores。",
    {
        "type": "object",
        "properties": {
            "storeCode": {"type": "string", "description": "门店编码"},
            "orderType": {"type": "integer", "description": "1-到店自提"},
            "beType": {"type": "integer", "description": "1-到店自取"},
        },
        "required": ["storeCode", "orderType", "beType"],
    },
)

MEAL_DETAIL = _spec(
    "mcd_meal_detail",
    "看单个餐品的详情（组成、能否换套餐内容）。「这个套餐里有什么」时用。",
    {
        "type": "object",
        "properties": {
            "storeCode": {"type": "string", "description": "门店编码"},
            "orderType": {"type": "integer", "description": "1-到店 2-外送"},
            "beType": {"type": "integer", "description": "1-到店 2-麦乐送 5-得来速"},
            "code": {"type": "string", "description": "餐品编码（菜单里拿）"},
        },
        "required": ["storeCode", "orderType", "beType", "code"],
    },
)

PRICE = _spec(
    "mcd_price",
    "算一份麦当劳订单的价格（含优惠）。「这一顿多少钱」时用。"
    "items 里给 productCode 和 quantity 就行。**只算价，不创建订单。**",
    {
        "type": "object",
        "properties": {
            "storeCode": {"type": "string", "description": "门店编码"},
            "orderType": {"type": "integer", "description": "1-到店 2-外送"},
            "beType": {"type": "integer", "description": "1-到店 2-麦乐送"},
            "items": {
                "type": "array",
                "description": "商品列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "productCode": {"type": "string", "description": "餐品编码"},
                        "quantity": {"type": "integer", "description": "数量"},
                    },
                    "required": ["productCode", "quantity"],
                },
            },
        },
        "required": ["storeCode", "orderType", "beType", "items"],
    },
)

ORDER = _spec(
    "mcd_order",
    "查麦当劳订单详情/配送进度。她说「我的麦当劳到哪了」「查下订单」时用。",
    {
        "type": "object",
        "properties": {"orderId": {"type": "string", "description": "订单号"}},
        "required": ["orderId"],
    },
)

ORDERS = _spec(
    "mcd_orders",
    "查她的麦当劳历史订单列表。她说「我最近吃了什么麦当劳」时用。看到就好，别逐条念。",
    {"type": "object", "properties": {}},
)

MY_COUPONS = _spec(
    "mcd_my_coupons",
    "看她卡包里已有的麦当劳券。「我有什么券」「券快过期了吗」时用。",
    {"type": "object", "properties": {}},
)

AVAILABLE_COUPONS = _spec(
    "mcd_available_coupons",
    "看当前可以领的麦当劳优惠券。「有什么优惠」「能领什么券」时用。",
    {"type": "object", "properties": {}},
)

CAMPAIGN = _spec(
    "mcd_campaign",
    "查麦当劳当月营销活动日历（品鉴会、主题派对等）。「最近麦当劳有什么活动」时用。",
    {"type": "object", "properties": {}},
)

_SPECS = (NEARBY, MENU, MEAL_DETAIL, PRICE, ORDER, ORDERS, MY_COUPONS, AVAILABLE_COUPONS, CAMPAIGN)


def make_handlers(client: McpClient) -> dict[str, object]:
    def _call(tool: str, args: dict) -> str:
        r = client.call(SERVER_TOOLS[tool], args)
        if not r.ok:
            raise RuntimeError(f"麦当劳查询失败: {r.error}")
        return r.text or "（麦当劳没返回内容）"

    return {spec.name: (lambda args, _t=spec.name: _call(_t, args)) for spec in _SPECS}


def register_all(loop, client: McpClient) -> None:
    handlers = make_handlers(client)
    for spec in _SPECS:  # 顺序固定
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
