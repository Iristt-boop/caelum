"""淘宝桌面版 MCP —— 他的购物手，走 LocalLink 反向链路。

服务是淘宝桌面版（2.5+）内置的本地 MCP（`http://127.0.0.1:3654/mcp`，
登录态复用桌面版会话）。Core 在 VPS 上够不到她电脑的回环端口，
所以照房间（`tools/room.py`）的路：**Core → wss 反向链路 → 网关 → 回环的淘宝**。
网关侧（`local-gateway/src/taobao-tools.ts`）负责转发和 session 管理。

🔴 **边界（与这批 MCP 同一条）**：没有下单工具——淘宝的 MCP 本身也不提供
下单，只有加购。他能搜、能比价、能加购、能替你问客服，**付款永远是她点的**。
`add_to_cart` / `ask_seller` / `navigate` 三件在网关侧 policy.ts 是审批档
（每次她点头）；这里注册的 8 件 = 网关 catalog 白名单的全集。

⚠️ 名字用 `taobao.*`（网关 catalog 的 Caelum 名），不是淘宝 MCP 的原名 ——
写错的表现是「工具不存在」，而那看起来像淘宝挂了（room.py 同一条教训）。

⚠️ 手连不上就如实说够不到，不断言原因（桌面版没开 / MCP 服务没开 / 网不通，
这边分不出来 —— room.py / computer.py 同一条纪律）。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.llm import ToolSpec
from tools.local_link import LocalLink

logger = logging.getLogger(__name__)

#: 我们的 LLM 工具名 → 网关 catalog 的 Caelum 能力名
WIRE = {
    "taobao_search": "taobao.search",
    "taobao_image_search": "taobao.image_search",
    "taobao_product_skus": "taobao.product_skus",
    "taobao_add_to_cart": "taobao.add_to_cart",
    "taobao_browse_history": "taobao.browse_history",
    "taobao_ask_seller": "taobao.ask_seller",
    "taobao_navigate": "taobao.navigate",
    "taobao_list_pages": "taobao.list_pages",
}

#: 动作三件（审批档在网关侧 policy.ts；这里只做描述层的确认制红线）
_CONFIRM = "🔴 确认制：先把要做的事完整说给她听，得到她明确的同意才能调。"


#: 每个工具的副作用声明（审计 3.1）。**新加工具必须在这里登记**，
#: 否则 `_spec()` 直接抛 —— 炸在启动，好过某天悄悄下了一单。
_EFFECTS: dict[str, tuple[str, str | None]] = {
    "taobao_search": ("read", None),
    "taobao_image_search": ("read", None),
    "taobao_product_skus": ("read", None),
    "taobao_browse_history": ("read", None),
    "taobao_list_pages": ("read", None),
    "taobao_navigate": ("write", None),
    "taobao_add_to_cart": ("write", None),
    "taobao_ask_seller": ("irreversible", None),
}


def _spec(name: str, description: str, params: dict) -> ToolSpec:
    try:
        effect, via = _EFFECTS[name]
    except KeyError:  # noqa: PERF203
        raise ValueError(
            f"{name} 没有在 taobao._EFFECTS 里声明副作用。"
            "拿不准就往重了标：花钱填 spend，撤不回来填 irreversible。"
        ) from None
    return ToolSpec(
        name=name, description=description, parameters=params,
        side_effect=effect, confirm_via=via,
    )


SEARCH = _spec(
    "taobao_search",
    "在淘宝/天猫搜商品或店铺。「帮我找个机械键盘」「有没有便宜的XX」时用。"
    "返回带价格的商品卡片。type: all=商品（默认）/ shop=店铺 / tmall=天猫。",
    {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "搜索关键词"},
            "type": {"type": "string", "description": "all / shop / tmall，默认 all"},
        },
        "required": ["keyword"],
    },
)

IMAGE_SEARCH = _spec(
    "taobao_image_search",
    "以图搜同款。她说「帮我找找这个同款」并给了图片路径（或相册里有图）时用。",
    {
        "type": "object",
        "properties": {
            "imagePath": {"type": "string", "description": "图片本地绝对路径 / CDN 地址 / base64"},
        },
        "required": ["imagePath"],
    },
)

PRODUCT_SKUS = _spec(
    "taobao_product_skus",
    "看某个淘宝商品的规格维度和可选值（颜色/尺码/库存）。加购前规格不清楚时先调这个。",
    {
        "type": "object",
        "properties": {
            "itemId": {"type": "string", "description": "商品 id（可选，来自 taobao_search 结果）"},
        },
    },
)

ADD_TO_CART = _spec(
    "taobao_add_to_cart",
    "把商品加进她的淘宝购物车。**不付款——下单永远是她自己点的。**"
    + _CONFIRM
    + "规格不确定先调 taobao_product_skus；sku 数组必须和页面维度完全匹配"
      "（如 [\"黑色\",\"XL\"]），传空数组可拿可用 SKU 列表。加完要把加了什么告诉她。",
    {
        "type": "object",
        "properties": {
            "itemId": {"type": "string", "description": "商品 id（可选）"},
            "sku": {
                "type": "array",
                "description": "SKU 属性值数组（须与页面维度完全匹配；不确定传空数组）",
            },
        },
    },
)

BROWSE_HISTORY = _spec(
    "taobao_browse_history",
    "看她最近在淘宝浏览了什么：product=商品 / search=搜索词 / shop=店铺。"
    "看到就好，心里有数就行——**别拿这个一条条盘问她**（tracker 同一条规矩）。",
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "description": "product / search / shop"},
        },
        "required": ["type"],
    },
)

ASK_SELLER = _spec(
    "taobao_ask_seller",
    "用旺旺向卖家客服发消息（会发给真人）。她说「问问卖家」「客服说什么时候发货」时用。"
    + _CONFIRM
    + "消息内容要她认可；source: cart=购物车 / order=订单 / search=搜索，"
      "productName 或 query 用来定位是哪件商品。",
    {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "要发给客服的消息"},
            "source": {"type": "string", "description": "cart / order / search（可选）"},
            "productName": {"type": "string", "description": "商品名关键词（cart/order 场景）"},
            "query": {"type": "string", "description": "搜索关键词（search 场景）"},
        },
    },
)

NAVIGATE = _spec(
    "taobao_navigate",
    "在她的淘宝桌面版里打开某个页面（home/cart/order/coupon/秒杀等），会弹到她屏幕上。"
    + _CONFIRM
    + "不确定有哪些页面名先调 taobao_list_pages。",
    {
        "type": "object",
        "properties": {
            "page": {"type": "string", "description": "页面名，如 cart / order / coupon"},
            "searchKey": {"type": "string", "description": "导航后自动搜索的关键词（cart/order 支持）"},
        },
        "required": ["page"],
    },
)

LIST_PAGES = _spec(
    "taobao_list_pages",
    "列出淘宝桌面版所有可导航的页面名。要导航但不确定页面名时先调这个。",
    {"type": "object", "properties": {}},
)

_SPECS = (SEARCH, IMAGE_SEARCH, PRODUCT_SKUS, ADD_TO_CART,
          BROWSE_HISTORY, ASK_SELLER, NAVIGATE, LIST_PAGES)


def make_handlers(client: LocalLink) -> dict[str, Any]:
    def _call(spec_name: str, args: dict[str, Any] | None = None) -> str:
        r = client.call(WIRE[spec_name], args or {})
        if not r.ok:
            raise RuntimeError(
                f"够不到淘宝（{r.error or '没有回应'}）——"
                "分不出是桌面版没开、MCP 服务没开，还是网不通。"
            )
        return r.text or "（淘宝没有返回内容）"

    return {spec.name: (lambda args, _t=spec.name: _call(_t, args)) for spec in _SPECS}


def register_all(loop, client: LocalLink) -> None:
    handlers = make_handlers(client)
    for spec in _SPECS:  # 顺序固定 —— 工具定义是缓存前缀的一部分
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
