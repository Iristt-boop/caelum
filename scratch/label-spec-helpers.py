#!/usr/bin/env python3
"""给 8 个用 `_spec()` 批量造工具的文件补上 side_effect（审计 3.1 下半）。

这 8 个文件是**最危险的那批** —— 点餐、打车、购物、公开发帖。
它们用 `_spec(name, description, params)` 从表里循环生成，所以第一个脚本
（只认直接构造）一个都没覆盖到。

做法：给每个文件加一张 `_EFFECTS` 表（name -> (effect, confirm_via)），
`_spec()` 改成从表里查，**查不到就抛** —— 新加工具忘了标，启动就炸。

在 nox-core/ 下跑：
    python ../scratch/label-spec-helpers.py
"""

from __future__ import annotations

import pathlib

R, W = "read", "write"
S, I = "spend", "irreversible"

#: 她自己点的那一下，就是闸门。
SELF = "链接/卡片发给她，她自己在对方 App 里点确认"
ORDER = "出卡后走 /api/nox/orders/{id}/confirm，她点「确认下单」才真的下"

EFFECTS: dict[str, dict[str, tuple[str, str | None]]] = {
    "amap": {
        "amap_search_poi": (R, None), "amap_search_nearby": (R, None),
        "amap_route_driving": (R, None), "amap_route_transit": (R, None),
        "amap_weather": (R, None),
    },
    "kd100": {
        "kd100_track": (R, None), "kd100_auto_number": (R, None),
        "kd100_timeliness": (R, None), "kd100_price": (R, None),
    },
    "train": {"train_tickets": (R, None), "train_interstation": (R, None)},
    "didi": {
        "didi_estimate": (R, None),
        # 只生成链接，她点开在滴滴里自己确认 —— 描述里写死了"不许说已经帮你叫好车了"
        "didi_ride_link": (W, SELF),
        "didi_order_status": (R, None),
    },
    "luckin": {
        "luckin_shops": (R, None), "luckin_search": (R, None),
        "luckin_product": (R, None), "luckin_preview": (R, None),
        # 它只出卡、不下单，但目的是花钱 —— 往重了标，让它和别的花钱工具排在一起
        "luckin_order": (S, ORDER),
        "luckin_order_detail": (R, None), "luckin_cancel": (W, None),
    },
    "mcd": {
        "mcd_nearby_stores": (R, None), "mcd_menu": (R, None),
        "mcd_meal_detail": (R, None), "mcd_price": (R, None),
        "mcd_order": (R, None), "mcd_orders": (R, None),
        "mcd_my_coupons": (R, None), "mcd_available_coupons": (R, None),
        "mcd_campaign": (R, None),
        # 🔴 审计点名的那一条：它**会真的下单**，而"确认制"只写在描述里
        #    （tools/mcd.py:155-158）。提示词不是闸门。
        "mcd_create_order": (S, None),
        # 消耗她的积分，一次性。确认制同样只是提示词
        "mcd_draw_lottery": (S, None),
        "mcd_bind_coupons": (W, None), "mcd_create_address": (W, None),
    },
    "taobao": {
        "taobao_search": (R, None), "taobao_image_search": (R, None),
        "taobao_product_skus": (R, None), "taobao_browse_history": (R, None),
        "taobao_list_pages": (R, None), "taobao_navigate": (W, None),
        # 加购不等于付款，描述里写着"下单永远是她自己点的"
        "taobao_add_to_cart": (W, None),
        # 🔴 用旺旺给**真人卖家**发消息。发出去就收不回来
        "taobao_ask_seller": (I, None),
    },
    "galatea": {
        # 只读
        "galatea_list_games": (R, None), "galatea_my_status": (R, None),
        "galatea_game_summary": (R, None), "galatea_get_machine": (R, None),
        "galatea_self": (R, None), "galatea_get_thread": (R, None),
        "galatea_list_threads": (R, None), "galatea_list_activity": (R, None),
        "galatea_list_notifications": (R, None), "galatea_tool_schema": (R, None),
        "galatea_messages": (R, None),
        # 游戏内动作：对外可见但轻，且是花园里的常规玩法
        "galatea_join_game": (W, None), "galatea_leave_waiting_game": (W, None),
        "galatea_start_game": (W, None), "galatea_submit_action": (W, None),
        "galatea_send_game_chat": (W, None), "galatea_interact": (W, None),
        "galatea_type": (W, None),
        # 🔴 对外公开、且撤不回来 —— 审计 3.4 点名的那一批
        "galatea_create_thread": (I, None), "galatea_create_reply": (I, None),
        "galatea_delete_thread": (I, None), "galatea_delete_reply": (I, None),
        "galatea_update_profile": (I, None), "galatea_decorate_avatar": (I, None),
        "galatea_review_drift_bottles": (W, None),
    },
}

OLD_HELPER = """def _spec(name: str, description: str, params: dict) -> ToolSpec:
    return ToolSpec(name=name, description=description, parameters=params)"""


def new_helper(mod: str) -> str:
    lines = [
        "#: 每个工具的副作用声明（审计 3.1）。**新加工具必须在这里登记**，",
        "#: 否则 `_spec()` 直接抛 —— 炸在启动，好过某天悄悄下了一单。",
        "_EFFECTS: dict[str, tuple[str, str | None]] = {",
    ]
    for name, (eff, via) in EFFECTS[mod].items():
        via_s = f'"{via}"' if via else "None"
        lines.append(f'    "{name}": ("{eff}", {via_s}),')
    lines += [
        "}",
        "",
        "",
        "def _spec(name: str, description: str, params: dict) -> ToolSpec:",
        "    try:",
        "        effect, via = _EFFECTS[name]",
        "    except KeyError:  # noqa: PERF203",
        "        raise ValueError(",
        f'            f"{{name}} 没有在 {mod}._EFFECTS 里声明副作用。"',
        '            "拿不准就往重了标：花钱填 spend，撤不回来填 irreversible。"',
        "        ) from None",
        "    return ToolSpec(",
        "        name=name, description=description, parameters=params,",
        "        side_effect=effect, confirm_via=via,",
        "    )",
    ]
    return "\n".join(lines)


def main() -> int:
    if not pathlib.Path("agent/loop.py").exists():
        print("请在 nox-core/ 下跑")
        return 1
    done = 0
    for mod in EFFECTS:
        p = pathlib.Path(f"tools/{mod}.py")
        src = p.read_text(encoding="utf-8")
        if "_EFFECTS" in src:
            print(f"  {mod}: 已经改过，跳过")
            continue
        if src.count(OLD_HELPER) != 1:
            print(f"  🔴 {mod}: 找不到唯一的 _spec 定义（{src.count(OLD_HELPER)} 处），跳过不猜")
            continue
        p.write_text(src.replace(OLD_HELPER, new_helper(mod)), encoding="utf-8")
        done += 1
        print(f"  ✅ {mod}: {len(EFFECTS[mod])} 个工具登记")
    print(f"改了 {done} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
