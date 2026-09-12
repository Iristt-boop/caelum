"""瑞幸咖啡官方 MCP（https://gwmcp.lkcoffee.com/order/user/mcp）—— 咖啡点单。

🟡 **下单/取消是确认制**（同麦当劳）：
- luckin_order：必须先 luckin_preview 算好价格，把商品明细、总价、取餐门店
  完整复述给她，得到她明确的「确认/下单」答复后才能调。
- luckin_cancel：仅她明确说取消、且订单还没开始制作时才调。
坐标用 amap_search_poi 现场查或环境里她的位置，别凭记忆编。
配置：NOX_LUCKIN_MCP_URL + NOX_LUCKIN_TOKEN（open.lkcoffee.com 登录后获取）。
"""

from __future__ import annotations

import json
import logging

from agent.llm import ToolSpec
from orders import luckin as luckin_order_flow
from tools import context as tool_context
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


#: 每个工具的副作用声明（审计 3.1）。**新加工具必须在这里登记**，
#: 否则 `_spec()` 直接抛 —— 炸在启动，好过某天悄悄下了一单。
_EFFECTS: dict[str, tuple[str, str | None]] = {
    "luckin_shops": ("read", None),
    "luckin_search": ("read", None),
    "luckin_product": ("read", None),
    "luckin_preview": ("read", None),
    "luckin_order": ("spend", "出卡后走 /api/nox/orders/{id}/confirm，她点「确认下单」才真的下"),
    "luckin_order_detail": ("read", None),
    "luckin_cancel": ("write", None),
}


def _spec(name: str, description: str, params: dict) -> ToolSpec:
    try:
        effect, via = _EFFECTS[name]
    except KeyError:  # noqa: PERF203
        raise ValueError(
            f"{name} 没有在 luckin._EFFECTS 里声明副作用。"
            "拿不准就往重了标：花钱填 spend，撤不回来填 irreversible。"
        ) from None
    return ToolSpec(
        name=name, description=description, parameters=params,
        side_effect=effect, confirm_via=via,
    )


SHOPS = _spec(
    "luckin_shops",
    "查附近的瑞幸门店。「附近有瑞幸吗」「这家瑞幸还开着吗」时用。"
    "🔴 **她提到具体地名时（「中原万达附近」「公司楼下」），必须先用 "
    "amap_search_poi 把那个地名查成坐标再传进来，不许用她当前的位置** ——"
    "2026-09-06 就是直接用了当前位置，她要中原万达却搜到了桐柏路。"
    "只有她说「附近」「我这儿」这种没指地方的时候，才用环境里她的位置。"
    "同一个商场可能有两家店（中原万达就有），把 deptName 传上去能收窄。",
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
    "预览订单：算出这一单的原价、优惠和实付。她问「多少钱」时用。"
    "🔴 返回里 totalInitialPrice=原价 privilegeMoney=优惠 discountPrice=实付，"
    "**优惠那一项一定要说**——瑞幸的券是 preview 自动挑的，"
    "你不说她就不知道有没有用上（2026-09-06 有过一次 20 块买了本该 10.9 的）。"
    "⚠️ 下单不用先调这个：luckin_order 出卡时系统会自己调一次取权威价。",
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
    "出一张**待确认**的瑞幸下单卡给她。🔴 **这个工具不会下单** ——"
    "它只把门店、明细、原价/优惠/实付算好，发一张卡到聊天里，"
    "等她自己点「确认下单」才真的下。所以**不要说「已经下单了」**，"
    "也不要复述价格明细（卡片上都有）。"
    "价格和优惠券由系统自己调 previewOrder 取权威值，你不用管、也不要转述。"
    "🔴 **卡片只有真的调用了这个工具才会出现。**"
    "光在回复里说卡发了、没调用的话，她那边什么都看不到 ——"
    "她会等一张永远不来的卡（2026-09-06 真的发生过两次）。",
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


#: 查门店必须有坐标。空着打上去，服务端只回一句 `empty String`，
#: 从日志完全看不出是**谁**空了（2026-09-06 实录）。
_NEEDS_COORDS = ("luckin_shops",)


def _data(client: McpClient, tool: str, args: dict) -> dict:
    """调一次 MCP 并把 data 段取出来。失败就抛（不许吞成空）。"""
    r = client.call(SERVER_TOOLS[tool], args)
    if not r.ok:
        raise RuntimeError(f"瑞幸 {tool} 失败: {r.error}（传了 {sorted(args)}）")
    try:
        body = json.loads(r.text or "")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"瑞幸 {tool} 返回的不是 JSON: {(r.text or '')[:80]}") from exc
    if body.get("code") not in (0, "0"):
        raise RuntimeError(f"瑞幸 {tool} 拒绝了: {body.get('msg')}")
    return body.get("data") or {}


def snapshot_for(client: McpClient, args: dict) -> tuple[dict, dict, str]:
    """按下单参数算出一份权威快照。返回（卡, 下单参数, 指纹）。

    🔴 **价格和券由 previewOrder 说了算，不问模型要。**
    模型转述价格这条路一旦留着，就永远会有一次它转述错了而没人发现 ——
    2026-09-06 那杯 20 块的生椰拿铁就是这么来的（本该 10.9）。

    出卡时调一次，她点确认时**再调一次**并比对指纹 ——
    价格或券变了就拒绝、重新出卡（调研文档第六节第二条）。
    """
    dept_id = str(args.get("deptId") or "")
    product_list = args.get("productList") or []
    if not dept_id or not product_list:
        raise RuntimeError("缺门店或商品，没法算价（deptId / productList 必传）")

    preview = _data(client, "luckin_preview",
                    {"deptId": dept_id, "productList": product_list})

    #: 附近还有哪几家 —— 她今天那次「中原万达」实际有两家店，
    #: 他选了远的那个。让她在卡上一眼看见，比让他选准更可靠
    nearby: list[dict] = []
    lon, lat = str(args.get("longitude") or ""), str(args.get("latitude") or "")
    if lon and lat:
        try:
            got = _data(client, "luckin_shops", {"longitude": lon, "latitude": lat})
            nearby = got if isinstance(got, list) else []
        except Exception as exc:  # noqa: BLE001
            #: 拿不到候选门店不该挡住下单 —— 少一块信息而已。
            #: 但要留痕，否则「换一家」按钮悄悄消失没人知道为什么
            logger.warning("取附近门店失败，卡片少一块：%s", exc)

    card, order_args = luckin_order_flow.build(
        preview, nearby=nearby, product_list=product_list,
        longitude=lon, latitude=lat,
    )
    return card, order_args, luckin_order_flow.fingerprint(card, order_args)


def place(client: McpClient, order_args: dict) -> dict:
    """真的下单。**只有确认端点会调它** —— 它不在工具表里，模型够不着。"""
    return _data(client, "luckin_order", order_args)


def make_handlers(
    client: McpClient,
    *,
    store_ref: object = None,
    session_id_ref: object = None,
) -> dict[str, object]:
    def _call(tool: str, args: dict) -> str:
        # 🔴 坐标空就**别打 API 碰运气**（2026-09-06）。
        #
        # 病根：她说「给我点杯咖啡」不带任何位置词，location Provider
        # 没加载，他不知道她在哪，于是经纬度传了空串上去。
        # 服务端回 `queryShopList 返回错误: empty String` —— 这句话
        # 既不告诉他缺什么，也不告诉他该怎么办，他只能干瞪眼。
        #
        # 这是「不许编」那条纪律的同一面：**不知道就说不知道，
        # 不要传一个空值上去看运气**。现在直接给他一句能照着做的话。
        if tool in _NEEDS_COORDS:
            missing = [k for k in ("longitude", "latitude")
                       if not str(args.get(k) or "").strip()]
            if missing:
                raise RuntimeError(
                    f"不知道她在哪（缺{'、'.join(missing)}），没法查门店。"
                    "先用 amap_search_poi 查她说的地名，或者直接问她在哪儿。"
                )

        r = client.call(SERVER_TOOLS[tool], args)
        if not r.ok:
            # 带上工具名和参数键，否则日志里只有一句服务端的错误码，
            # 查不出是哪一步、传了什么（docs/LOGGING.md：留痕要含输入）
            raise RuntimeError(
                f"瑞幸 {tool} 失败: {r.error}（传了 {sorted(args)}）"
            )
        return r.text or "（瑞幸没返回内容）"

    def _make_card(args: dict) -> str:
        """🔴 `luckin_order` 现在**不下单**，只出一张待确认卡（2026-09-06）。

        真正的 `createOrder` 挪到 `/api/nox/orders/{id}/confirm` 后面 ——
        模型物理上够不到它，因为它不在工具表里。

        这是把「确认制」从一句提示词变成代码。在这之前，`tools/mcd.py` 的
        `ACTION_TOOLS` 常量定义完之后**整个仓库没有任何地方用到**，
        测试也只断言「描述里有『确认』二字」—— 验的是那句话写了没有，
        不是确认真的发生了没有。
        """
        store = store_ref() if callable(store_ref) else store_ref
        if store is None:
            #: 🔴 **fail-closed**：订单库没接上就拒绝，绝不退回「那就直接下单吧」。
            #: 退回去的话，一次配置疏漏就让确认闸门整个消失，而且完全不报错
            raise RuntimeError(
                "订单确认链路没接上（orders store 缺失），这单不能下。"
                "如实告诉她现在下不了单，别自己想办法绕过去。"
            )
        sid = (session_id_ref() if callable(session_id_ref) else session_id_ref) or ""

        card, order_args, fp = snapshot_for(client, args)
        oid = store.create(session_id=sid, merchant=luckin_order_flow.MERCHANT,
                           snapshot={"card": card, "args": order_args}, fingerprint=fp)

        ctx = tool_context.current()
        if ctx is not None:
            #: 只把 card 那份带出去 —— args 里有她账号的券码
            ctx.attach_order(oid, card)
        else:
            #: 不在轮次里（单测直接调）。单子照建，卡发不出去 ——
            #: 要留痕，否则会表现成「他说出卡了但她什么都没看见」
            logger.warning("不在对话轮次里，待确认单 %s 的卡片没发出去", oid)

        return luckin_order_flow.summarize(card)

    handlers = {spec.name: (lambda args, _t=spec.name: _call(_t, args))
                for spec in _SPECS}
    handlers["luckin_order"] = _make_card
    return handlers


def register_all(loop, client: McpClient, *, store_ref=None, session_id_ref=None) -> None:
    handlers = make_handlers(client, store_ref=store_ref, session_id_ref=session_id_ref)
    for spec in _SPECS:  # 顺序固定
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
