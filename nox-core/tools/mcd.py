"""麦当劳官方 MCP（https://mcp.mcd.cn/mcp-servers/mcd-mcp）—— 麦麦的点餐周边。

🟡 **边界（2026-09-05 晚糖糖拍板放开动作类）**：下单/抽奖/领券/写地址
四个动作工具已注册（ACTION_TOOLS），但**全部确认制**——描述里写死了
「先报价复述、得到她明确的确认才能调」。她明确放开的，但闸门文字不能拆。
仍不接：party-order-create（团餐下单）、mall-create-order（商城下单）。

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
    # ---- 动作类（2026-09-05 晚糖糖放开，全部确认制）----
    "mcd_create_order": "create-order",
    "mcd_draw_lottery": "draw-lottery",
    "mcd_bind_coupons": "auto-bind-coupons",
    "mcd_create_address": "delivery-create-address",
}

#: 动作工具清单 —— 描述里必须带「确认」字样，测试盯着
ACTION_TOOLS = ("mcd_create_order", "mcd_draw_lottery", "mcd_bind_coupons", "mcd_create_address")


import json as _json


def _extract_json(text: str) -> dict | None:
    """从「API 文档 + 真实 JSON」混杂的返回里抠出最大的合法 JSON 对象。

    🔴 麦当劳 MCP 的每个返回都是这个形状：前面一大段字段说明文档
    （**文档里就有 `{}` 样例**），后面跟真实 JSON。`find("{")` 抠到的
    第一个花括号是文档的样例，解析出来是碎片 —— 2026-09-16 实测
    query-meals 的返回 24KB、第一个 `{` 在文档第 1 行。
    这里用 raw_decode 从每个 `{` 位置尝试，取**解析成功且最长**的块
    —— 文档样例再怎么干扰，真实 JSON 永远是最大的那个。

    解析不出来返回 None，绝不抛。第二个使用场景出现时再上提到公共模块。
    """
    if not text:
        return None
    decoder = _json.JSONDecoder()
    best = None
    best_len = 0
    idx = text.find("{")
    while idx != -1:
        try:
            obj, end = decoder.raw_decode(text, idx)
            if end - idx > best_len:
                best, best_len = obj, end - idx
            idx = text.find("{", end)
        except (_json.JSONDecodeError, ValueError):
            idx = text.find("{", idx + 1)
    return best if isinstance(best, dict) else None


#: 每个工具的副作用声明（审计 3.1）。**新加工具必须在这里登记**，
#: 否则 `_spec()` 直接抛 —— 炸在启动，好过某天悄悄下了一单。
_EFFECTS: dict[str, tuple[str, str | None]] = {
    "mcd_nearby_stores": ("read", None),
    "mcd_menu": ("read", None),
    "mcd_meal_detail": ("read", None),
    "mcd_price": ("read", None),
    "mcd_order": ("read", None),
    "mcd_orders": ("read", None),
    "mcd_my_coupons": ("read", None),
    "mcd_available_coupons": ("read", None),
    "mcd_campaign": ("read", None),
    "mcd_create_order": ("spend", None),
    "mcd_draw_lottery": ("spend", None),
    "mcd_bind_coupons": ("write", None),
    "mcd_create_address": ("write", None),
}


def _spec(name: str, description: str, params: dict) -> ToolSpec:
    try:
        effect, via = _EFFECTS[name]
    except KeyError:  # noqa: PERF203
        raise ValueError(
            f"{name} 没有在 mcd._EFFECTS 里声明副作用。"
            "拿不准就往重了标：花钱填 spend，撤不回来填 irreversible。"
        ) from None
    return ToolSpec(
        name=name, description=description, parameters=params,
        side_effect=effect, confirm_via=via,
    )


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

CREATE_ORDER = _spec(
    "mcd_create_order",
    "创建麦当劳订单。🔴 **确认制**：调用前必须先用 mcd_price 算出总价，把"
    "门店、餐品明细、总价完整复述给她，得到她明确的「确认/下单」答复后才能调；"
    "她没确认就不许调。外送(orderType=2)还需 addressId；到店(orderType=1)还需 takeWayCode（门店查询返回里拿）。"
    "创建成功后把订单号和取餐/配送信息告诉她。"
    "
⚠️ **上游阻断（2026-09-16 探测）**：query-meals 的商品节点只有"
    " name/currentPrice/originalPrice/image，**没有 productCode**——"
    "文档说「菜单里拿」但菜单里没有。productCode 的真实来源待确认"
    "（抓包 App 下单 / 问 MCP 维护方），确认前本工具不要调。",
    {
        "type": "object",
        "properties": {
            "storeCode": {"type": "string", "description": "门店编码"},
            "orderType": {"type": "integer", "description": "1-到店（含得来速取餐） 2-外送"},
            "beType": {"type": "integer", "description": "1-到店 2-麦乐送 5-得来速"},
            "beCode": {"type": "string", "description": "业务编码（外送/得来速必传，门店查询里拿）"},
            "addressId": {"type": "string", "description": "外送地址 id（orderType=2 必传）"},
            "takeWayCode": {"type": "string", "description": "取餐方式编码（orderType=1 到店必传；值从 mcd_nearby_stores / mcd_menu 的返回里拿）"},
            "items": {
                "type": "array",
                "description": "商品列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "productCode": {"type": "string", "description": "餐品编码"},
                        "quantity": {"type": "integer", "description": "数量"},
                        "couponId": {"type": "string", "description": "优惠券 id，可选"},
                    },
                    "required": ["productCode", "quantity"],
                },
            },
        },
        "required": ["storeCode", "orderType", "beType", "items"],
    },
)

DRAW_LOTTERY = _spec(
    "mcd_draw_lottery",
    "用积分抽一次麦当劳的奖。🔴 **确认制**：先调 query-lottery-info 把消耗规则"
    "（扣多少积分/次数、能抽什么）念给她听，她说「抽」才能调。服务端也会校验。",
    {"type": "object", "properties": {}},
)

BIND_COUPONS = _spec(
    "mcd_bind_coupons",
    "一键领取麦麦省当前所有可领的优惠券。她说「帮我领券」「把券都领了」时用。"
    "只领券不花钱，领完把券列表简短报给她。",
    {"type": "object", "properties": {}},
)

CREATE_ADDRESS = _spec(
    "mcd_create_address",
    "新增麦当劳配送地址。🔴 **确认制**：城市/联系人/性别/手机号/地址五项全部"
    "来自她的原话，调用前把完整信息复述一遍得到确认；她没给齐就先问，不许编。",
    {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名，如「南京市」"},
            "contactName": {"type": "string", "description": "联系人姓名"},
            "gender": {"type": "string", "description": "「先生」或「女士」"},
            "phone": {"type": "string", "description": "11 位手机号"},
            "address": {"type": "string", "description": "配送地址"},
            "addressDetail": {"type": "string", "description": "门牌号详情"},
        },
        "required": ["address", "addressDetail", "city", "contactName", "phone"],
    },
)

SPECS = (NEARBY, MENU, MEAL_DETAIL, PRICE, ORDER, ORDERS, MY_COUPONS, AVAILABLE_COUPONS, CAMPAIGN)
ACTION_SPECS = (CREATE_ORDER, DRAW_LOTTERY, BIND_COUPONS, CREATE_ADDRESS)
_SPECS = SPECS + ACTION_SPECS


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
