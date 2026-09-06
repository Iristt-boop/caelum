"""瑞幸：把 previewOrder 的返回变成一张给她看的确认卡。

## 🔴 价格必须由 previewOrder 说了算，不能由模型转述

2026-09-06 实录：糖糖花 20 买了一杯本该 10.9 的生椰拿铁。
查出来 `previewOrder` 返回的是

    totalInitialPrice = 20.0     原价
    privilegeMoney    =  9.1     优惠
    discountPrice     = 10.9     实付
    couponCodeList    = ["SY1200…"]   ← **preview 自动挑好的券**

他没调 preview，于是 `createOrder` 也没带 `couponCodeList` —— 那 9.1 块就这么丢了。
他当时的说法是「搜了没券」，而**瑞幸 MCP 根本没有查券的工具**（对比麦当劳有三个），
券只从 preview 的返回里出。他不是没搜，是没有能搜的地方。

所以这里的规矩是：**下单前由工具自己调一次 preview**，
拿它的价和它的券，不问模型要。模型转述价格这条路一旦留着，
就永远会有一次它转述错了而没人发现。

⚠️ preview **没有**「可用但没选上的券」这种字段 —— 它是自动选最优的。
所以卡片不写「还有 N 张券没用上」（那是编的），只如实显示它给的那三个数；
优惠为 0 时明说「没有可用券」，让「没券」是被证实的，而不是他忘了查。

## 金额一律整数分

`Caelum-AI支付-可行性调研.md` 第六节：**金额用整数分，交易状态机里不许出现浮点。**
瑞幸返回的是元（float），进门就换成分。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

MERCHANT = "luckin"

#: 候选门店最多带几家。她今天那次「中原万达」实际有两家，
#: 而附近还有保利久街 —— 3 家够她看出选错了，再多就是刷屏
MAX_NEARBY = 4


def fen(yuan: Any) -> int:
    """元 → 分。**所有金额进门就走这一步。**

    `round` 不能省：`10.9 * 100` 在浮点里是 1089.9999999999998，
    `int()` 直接截断会变成 1089 —— 一分钱的错，但对不上账。
    """
    try:
        return int(round(float(yuan or 0) * 100))
    except (TypeError, ValueError):
        return 0


def yuan(f: int) -> str:
    """分 → 给人看的字符串。只在渲染时用，不参与任何计算。"""
    return f"{f / 100:.2f}"


def build(
    preview: dict[str, Any],
    *,
    nearby: list[dict[str, Any]] | None = None,
    product_list: list[dict[str, Any]],
    longitude: str,
    latitude: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """把 preview 的 data 拆成（给她看的卡, 下单要用的参数）。

    🔴 **分成两份是有意的。** 卡那份会进 attachment 发到前端、进日志；
    参数那份含 `couponCodeList`，是她账号里的券码，不该跟着卡到处跑。
    `api/server.py` 出 attachment 时只取卡那份。
    """
    shop = preview.get("shopInfo") or {}
    items = []
    for p in preview.get("productInfoList") or []:
        items.append({
            "name": p.get("name") or "",
            #: 「大杯/冰/意式拼配/默认浓度/不另外加糖/无奶油」——
            #: 规格必须显示，不然她看不出是不是点错了温度或杯型
            "spec": p.get("additionDesc") or "",
            "amount": int(p.get("amount") or 1),
        })

    original = fen(preview.get("totalInitialPrice"))
    privilege = fen(preview.get("privilegeMoney"))
    actual = fen(preview.get("discountPrice"))
    coupons = list(preview.get("couponCodeList") or [])

    card = {
        "merchant": MERCHANT,
        "store": {
            "id": str(shop.get("deptId") or ""),
            "name": shop.get("deptName") or "",
            "address": shop.get("address") or "",
            "status": shop.get("workStatus") or "",
            "hours": f"{shop.get('workTimeStart') or ''}-{shop.get('workTimeEnd') or ''}".strip("-"),
        },
        "items": items,
        "original_fen": original,
        "privilege_fen": privilege,
        "actual_fen": actual,
        #: 只给**张数**，不给券码 —— 券码是她账号里的东西，
        #: 卡片会被截图、会进日志，没必要跟着走
        "coupon_count": len(coupons),
        "nearby": [
            {
                "id": str(s.get("deptId") or ""),
                "name": s.get("deptName") or "",
                "address": s.get("address") or "",
                "status": s.get("workStatus") or "",
            }
            for s in (nearby or [])[:MAX_NEARBY]
            if str(s.get("deptId") or "") != str(shop.get("deptId") or "")
        ],
    }

    args = {
        "deptId": str(shop.get("deptId") or ""),
        "productList": product_list,
        "longitude": str(longitude),
        "latitude": str(latitude),
        "couponCodeList": coupons,
    }
    return card, args


def fingerprint(card: dict[str, Any], args: dict[str, Any]) -> str:
    """这一份快照的指纹。

    确认时会**重跑一次 preview** 并重算指纹，不一致就拒绝、重新出卡。
    对应 `Caelum-AI支付-可行性调研.md` 第六节第二条：
    **一次审批只绑定一份完整订单快照，不因一次审批自动授权后续订单。**

    进指纹的东西：门店 + 商品 + 实付 + 用了哪些券。
    不进的：距离、营业状态、附近门店 —— 那些变了不影响这一单是什么。
    """
    payload = {
        "store": card["store"]["id"],
        "items": [(i["name"], i["spec"], i["amount"]) for i in card["items"]],
        "actual_fen": card["actual_fen"],
        "coupons": sorted(args.get("couponCodeList") or []),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def summarize(card: dict[str, Any]) -> str:
    """给模型看的一句话。**不是给她看的** —— 她看卡片。

    模型需要知道「我出了一张什么单」，才能在旁边说人话；
    但绝不能让它以为已经下单了，所以话说得很死。
    """
    what = "、".join(f"{i['name']}×{i['amount']}" for i in card["items"]) or "（空）"
    price = f"原价 {yuan(card['original_fen'])}"
    if card["privilege_fen"] > 0:
        price += f"，用了 {card['coupon_count']} 张券省 {yuan(card['privilege_fen'])}"
    else:
        price += "，preview 说没有可用券"
    price += f"，实付 {yuan(card['actual_fen'])}"
    return (
        f"已经出了一张待确认单（**还没有下单**）：{what}｜"
        f"{card['store']['name']}｜{price}。\n"
        "卡片已经发到她那边了，等她点「确认下单」才会真的下。\n"
        "⚠️ 你**不要**再复述一遍价格和明细（卡片上都有），"
        "也不要说「已经下单了」——现在还没有。等她点。"
    )
