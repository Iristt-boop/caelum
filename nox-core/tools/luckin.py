"""瑞幸咖啡官方 MCP（https://gwmcp.lkcoffee.com/order/user/mcp）—— 咖啡点单。

🟡 **下单/取消是确认制**（同麦当劳）：
- luckin_order：必须先 luckin_preview 算好价格，把商品明细、总价、取餐门店
  完整复述给她，得到她明确的「确认/下单」答复后才能调。
- luckin_cancel：仅她明确说取消、且订单还没开始制作时才调。
坐标用 amap_search_poi 现场查或环境里她的位置，别凭记忆编。
配置：NOX_LUCKIN_MCP_URL + NOX_LUCKIN_TOKEN（open.lkcoffee.com 登录后获取）。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

SERVER_TOOLS = {
    "luckin_shops": "queryShopList",
    "luckin_search": "searchProductForMcp",
    "luckin_product": "queryProductDetailInfo",
    "luckin_preview": "previewOrder",
    "luckin_order": "createOrder",
    "luckin_order_detail": "queryOrderDetailInfo",
    "luckin_cancel": "cancelOrder",
}


def _spec(name: str, description: str, params: dict) -> ToolSpec:
    return ToolSpec(name=name, description=description, parameters=params)


SHOPS = _spec(
    "luckin_shops",
    "查附近的瑞幸门店。「附近有瑞幸吗」「这家瑞幸还开着吗」时用。"
    "经纬度用 amap_search_poi 查或用环境里她的位置。",
    {
        "type": "object",
        "properties": {
            "longitude": {"type": "string", "description": "经度"},
            "latitude": {"type": "string", "description": "纬度"},
            "deptName": {"type": "string", "description": "门店名关键词，可选"},
        },
        "required": ["longitude", "latitude"],
    },
)

SEARCH = _spec(
    "luckin_search",
    "在指定瑞幸门店里搜商品。她说「来杯生椰拿铁」「有没有轻乳茶」时用。"
    "query 传她的原话（系统会做语义匹配），deptId 来自 luckin_shops。",
    {
        "type": "object",
        "properties": {
            "deptId": {"type": "string", "description": "门店 id（luckin_shops 拿）"},
            "query": {"type": "string", "description": "她的原话，如「大杯热生椰拿铁」"},
        },
        "required": ["deptId", "query"],
    },
)

PRODUCT = _spec(
    "luckin_product",
    "看某个商品的详情（规格、价格、可点状态）。「这个是什么」「多少钱」时用。",
    {
        "type": "object",
        "properties": {
            "deptId": {"type": "string", "description": "门店 id"},
            "productId": {"type": "string", "description": "商品 id（luckin_search 拿）"},
        },
        "required": ["deptId", "productId"],
    },
)

PREVIEW = _spec(
    "luckin_preview",
    "预览订单：算出这一单的总价和可用优惠。🔴 **下单前的必经步骤**——"
    "把商品明细、总价、门店复述给她，得到明确确认才能调 luckin_order。",
    {
        "type": "object",
        "properties": {
            "deptId": {"type": "string", "description": "门店 id"},
            "productList": {
                "type": "array",
                "description": "商品列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "productId": {"type": "integer", "description": "商品 id"},
                        "skuCode": {"type": "string", "description": "SKU 编码（含杯型/温度/糖度）"},
                        "amount": {"type": "integer", "description": "数量"},
                    },
                    "required": ["productId", "skuCode", "amount"],
                },
            },
        },
        "required": ["deptId", "productList"],
    },
)

ORDER = _spec(
    "luckin_order",
    "创建瑞幸订单。🔴 **确认制**：必须先 luckin_preview 报价并得到她的明确确认；"
    "有优惠券的话 couponCodeList 从 previewOrder 的返回里拿。"
    "下单成功后把取餐门店和取餐码告诉她。",
    {
        "type": "object",
        "properties": {
            "deptId": {"type": "string", "description": "门店 id"},
            "productList": {
                "type": "array",
                "description": "商品列表（同 preview）",
                "items": {
                    "type": "object",
                    "properties": {
                        "productId": {"type": "integer", "description": "商品 id"},
                        "skuCode": {"type": "string", "description": "SKU 编码"},
                        "amount": {"type": "integer", "description": "数量"},
                    },
                    "required": ["productId", "skuCode", "amount"],
                },
            },
            "longitude": {"type": "string", "description": "她的位置经度"},
            "latitude": {"type": "string", "description": "她的位置纬度"},
            "couponCodeList": {
                "type": "array",
                "items": {"type": "string"},
                "description": "优惠券 code 列表（previewOrder 返回里拿），可选",
            },
            "remark": {"type": "string", "description": "订单备注，可选"},
        },
        "required": ["deptId", "productList", "longitude", "latitude"],
    },
)

ORDER_DETAIL = _spec(
    "luckin_order_detail",
    "查瑞幸订单详情/状态（制作中、待取餐、已完成）。她说「我的咖啡好了吗」时用。",
    {
        "type": "object",
        "properties": {"orderId": {"type": "string", "description": "订单 id"}},
        "required": ["orderId"],
    },
)

CANCEL = _spec(
    "luckin_cancel",
    "取消瑞幸订单。🔴 **确认制**：仅她明确说「取消」、且订单还没开始制作时才调。"
    "她点错了想改，先取消再重新走确认流程。",
    {
        "type": "object",
        "properties": {"orderId": {"type": "string", "description": "订单 id"}},
        "required": ["orderId"],
    },
)

_SPECS = (SHOPS, SEARCH, PRODUCT, PREVIEW, ORDER, ORDER_DETAIL, CANCEL)


def make_handlers(client: McpClient) -> dict[str, object]:
    def _call(tool: str, args: dict) -> str:
        r = client.call(SERVER_TOOLS[tool], args)
        if not r.ok:
            raise RuntimeError(f"瑞幸查询失败: {r.error}")
        return r.text or "（瑞幸没返回内容）"

    return {spec.name: (lambda args, _t=spec.name: _call(_t, args)) for spec in _SPECS}


def register_all(loop, client: McpClient) -> None:
    handlers = make_handlers(client)
    for spec in _SPECS:  # 顺序固定
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
