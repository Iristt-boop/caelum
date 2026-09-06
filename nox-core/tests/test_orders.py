"""待确认单：把「确认制」从提示词变成代码（2026-09-06）。

设计见 `Caelum-点单确认卡-设计.md`。

## 这个文件盯的是一条命根子

**`luckin_order` 工具绝不能触达 `createOrder`。**

在这之前，「确认制」的全部实现是工具描述里那句「她没确认就不许调」，
而 `tools/mcd.py` 的 `ACTION_TOOLS` 常量定义完之后**整个仓库没有任何
地方用到它**，测试也只断言「描述里有『确认』二字」——
验的是那句话写了没有，不是确认真的发生了没有。

⚠️ 不打网络。假 MCP 记下每一次调用，断言 `createOrder` 一次都没出现。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import orders as orders_mod  # noqa: E402
from orders import OrderStore  # noqa: E402
from orders import luckin as flow  # noqa: E402
from tools import context as tool_context  # noqa: E402
from tools import luckin as luckin_tools  # noqa: E402

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)

#: 🔴 **真实的 previewOrder 返回**（2026-09-06 从线上抄的，不是编的）。
#: 那杯生椰拿铁：原价 20、优惠 9.1、实付 10.9，preview 自动挑好了一张券。
#: 糖糖当天付了 20 —— 因为他没调 preview，createOrder 也就没带 couponCodeList。
PREVIEW = {
    "totalInitialPrice": 20.0,
    "privilegeMoney": 9.1,
    "discountPrice": 10.9,
    "couponCodeList": ["SY120035343418848269"],
    "shopInfo": {
        "deptId": 386472, "deptName": "中原万达2F店",
        "address": "中原区中原西路171号万达广场二层2035号",
        "workStatus": "营业中", "workTimeStart": "10:00", "workTimeEnd": "22:00",
    },
    "productInfoList": [{
        "productId": 1262, "skuCode": "SP2077-01134", "name": "生椰拿铁（首创）",
        "amount": 1, "additionDesc": "大杯/冰/意式拼配/默认浓度/不另外加糖/无奶油",
    }],
}

#: 中原万达真的有两家店（2026-09-06 实测），他当时选了远的那个
SHOPS = [
    {"deptId": 326703, "deptName": "中原万达店", "address": "华山路116号", "workStatus": "营业中"},
    {"deptId": 386472, "deptName": "中原万达2F店", "address": "中原西路171号", "workStatus": "营业中"},
]

ARGS = {
    "deptId": "386472",
    "productList": [{"productId": 1262, "skuCode": "SP2077-01134", "amount": 1}],
    "longitude": "113.601", "latitude": "34.748",
}


class FakeMcp:
    """记下每一次调用。`createOrder` 出现一次就算这个设计塌了。"""

    def __init__(self, order_result: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.order_result = order_result or {"orderId": "LK202609060991"}

    def call(self, tool, args):
        self.calls.append((tool, args))
        body = {"code": 0, "msg": "success", "data": (
            PREVIEW if tool == "previewOrder"
            else SHOPS if tool == "queryShopList"
            else self.order_result if tool == "createOrder"
            else {})}
        return type("R", (), {"ok": True, "text": json.dumps(body, ensure_ascii=False),
                              "error": None})()

    @property
    def tools(self):
        return [t for t, _ in self.calls]


@pytest.fixture
def store(tmp_path):
    s = OrderStore(tmp_path / "orders.db")
    yield s
    s.close()


def _handler(client, store, sid="s-1"):
    return luckin_tools.make_handlers(
        client, store_ref=lambda: store, session_id_ref=lambda: sid,
    )["luckin_order"]


# ---------------------------------------------------------------- 命根子


def test_tool_never_places_the_order(store):
    """🔴 **整个设计的命根子。** 工具只出卡，绝不触达 createOrder。"""
    mcp = FakeMcp()
    out = _handler(mcp, store)(ARGS)
    assert "createOrder" not in mcp.tools, "工具直接下单了 —— 确认闸门形同虚设"
    assert "previewOrder" in mcp.tools, "没调 preview 就出卡，价格是模型编的"
    assert "还没有下单" in out


def test_tool_output_tells_the_model_not_to_claim_success(store):
    """给模型的返回必须把话说死，否则他会说「已经帮你点好了」。"""
    out = _handler(FakeMcp(), store)(ARGS)
    assert "不要说" in out and "已经下单" in out


def test_fail_closed_without_store():
    """🔴 订单库没接上 → **拒绝**，绝不退回「那就直接下单吧」。

    退回去的话，一次配置疏漏就让确认闸门整个消失，而且完全不报错。
    """
    mcp = FakeMcp()
    with pytest.raises(RuntimeError, match="不能下"):
        luckin_tools.make_handlers(mcp, store_ref=lambda: None,
                                   session_id_ref=lambda: "s")["luckin_order"](ARGS)
    assert mcp.tools == [], "拒绝时一次 MCP 都不该打"


# ---------------------------------------------------------------- 快照


def test_price_comes_from_preview_not_the_model(store):
    """价格由 previewOrder 说了算。模型转述价格这条路一旦留着，
    就永远会有一次它转述错了而没人发现 —— 那 20 块的咖啡就是这么来的。"""
    card, args, _ = luckin_tools.snapshot_for(FakeMcp(), ARGS)
    assert card["original_fen"] == 2000
    assert card["privilege_fen"] == 910
    assert card["actual_fen"] == 1090
    assert args["couponCodeList"] == ["SY120035343418848269"], "券没带进下单参数"


def test_money_is_integer_fen():
    """调研文档第六节：金额用整数分，状态机里不许出现浮点。

    `10.9 * 100` 在浮点里是 1089.9999999999998，`int()` 截断会变 1089。
    """
    assert flow.fen(10.9) == 1090
    assert flow.fen(20.0) == 2000
    assert flow.fen(0) == 0
    assert flow.fen(None) == 0
    assert isinstance(flow.fen(10.9), int)


def test_coupon_codes_never_reach_the_card(store):
    """券码是她账号里的东西。卡片会被截图、会进日志，没必要跟着走。"""
    card, args, _ = luckin_tools.snapshot_for(FakeMcp(), ARGS)
    blob = json.dumps(card, ensure_ascii=False)
    assert "SY120035343418848269" not in blob
    assert card["coupon_count"] == 1


def test_card_carries_the_other_stores(store):
    """中原万达有两家店，他当时选了远的。让她在卡上一眼看见 ——
    这比让他选准更可靠。"""
    card, _, _ = luckin_tools.snapshot_for(FakeMcp(), ARGS)
    names = [s["name"] for s in card["nearby"]]
    assert "中原万达店" in names
    assert "中原万达2F店" not in names, "选中的那家不该再出现在候选里"


def test_spec_is_shown():
    """点错温度杯型要看得出来。"""
    card, _, _ = luckin_tools.snapshot_for(FakeMcp(), ARGS)
    assert "大杯" in card["items"][0]["spec"] and "冰" in card["items"][0]["spec"]


# ---------------------------------------------------------------- 幂等 / 指纹


def test_double_click_places_only_one_order(store):
    """🔴 她连点两次「确认下单」不能下两单。

    先查后改的话两个请求都会看到 pending —— 所以 `claim()` 用
    `UPDATE ... WHERE state=?` 让 SQLite 保证只有一个能成。
    """
    oid = store.create(session_id="s", merchant="luckin",
                       snapshot={"card": {}, "args": {}}, fingerprint="fp", now=NOW)
    assert store.claim(oid, now=NOW) is not None
    assert store.claim(oid, now=NOW) is None, "第二次点也成功了 —— 会下两单"


def test_expired_snapshot_cannot_be_confirmed(store):
    """15 分钟前的价格不能拿来下单。"""
    oid = store.create(session_id="s", merchant="luckin",
                       snapshot={"card": {}, "args": {}}, fingerprint="fp", now=NOW)
    assert store.claim(oid, now=NOW + timedelta(minutes=16)) is None
    assert store.get(oid)["state"] == orders_mod.EXPIRED


def test_fingerprint_moves_when_price_moves():
    """价格变了指纹就得变，否则确认时比不出来。"""
    card, args, fp1 = luckin_tools.snapshot_for(FakeMcp(), ARGS)
    card2 = {**card, "actual_fen": 2000}
    assert flow.fingerprint(card2, args) != fp1


def test_fingerprint_ignores_volatile_noise():
    """距离和营业状态变了不算「这一单变了」—— 否则她永远点不成。"""
    card, args, fp1 = luckin_tools.snapshot_for(FakeMcp(), ARGS)
    noisy = {**card, "store": {**card["store"], "status": "休息中"}, "nearby": []}
    assert flow.fingerprint(noisy, args) == fp1


# ---------------------------------------------------------------- 付款链接


def test_pay_link_never_lands_in_the_snapshot(store):
    """调研文档第六节倒数第二条：**收银台 URL 不进日志、不进聊天历史、
    不进数据库明文。** snapshot_json 会进 attachment、会进日志 ——
    付款链接混在里面就等于到处都是。"""
    oid = store.create(session_id="s", merchant="luckin",
                       snapshot={"card": {"actual_fen": 1090}, "args": {}},
                       fingerprint="fp", now=NOW)
    store.set_pay_link(oid, "weixin://wxpay/bizpayurl?pr=XXXX", now=NOW)
    row = store.get(oid)
    assert "weixin://" not in json.dumps(row["snapshot"], ensure_ascii=False)
    assert store.pay_link(oid, now=NOW).startswith("weixin://")


def test_stale_pay_link_is_treated_as_gone(store):
    """过期的收银台链接点了也是报错页，不如当没有。"""
    oid = store.create(session_id="s", merchant="luckin",
                       snapshot={"card": {}, "args": {}}, fingerprint="fp", now=NOW)
    store.set_pay_link(oid, "weixin://x", now=NOW)
    assert store.pay_link(oid, now=NOW + timedelta(hours=3)) is None


# ---------------------------------------------------------------- 串起来


def test_end_to_end_card_then_confirm_then_order(store):
    """🔴 出卡 → 确认 → 真下单，全程走一遍。

    这项目栽过两次「单测全绿、串起来断在最后一步」（Resonance V3 的
    intensity 饱和、V3.6 的 Registry.upsert 拒收）。所以这条不能省。
    """
    mcp = FakeMcp()
    with tool_context.scope() as ctx:
        _handler(mcp, store)(ARGS)

    # ① 卡发出去了，而且只带 card 不带 args
    assert len(ctx.attachments) == 1
    att = ctx.attachments[0]
    assert att["type"] == "order"
    assert "SY120035343418848269" not in json.dumps(att, ensure_ascii=False)
    oid = att["order_id"]

    # ② 此时**还没有下单**
    assert "createOrder" not in mcp.tools
    assert store.get(oid)["state"] == orders_mod.PENDING

    # ③ 她点确认 —— 这才走到 createOrder
    row = store.claim(oid)
    assert row is not None
    _, args2, fp2 = luckin_tools.snapshot_for(mcp, row["snapshot"]["args"])
    assert fp2 == row["fingerprint"], "重算的指纹对不上，会被拒"
    result = luckin_tools.place(mcp, args2)
    assert "createOrder" in mcp.tools
    assert result["orderId"] == "LK202609060991"

    # ④ 下单时**带上了券** —— 那 9.1 块不能再丢一次
    order_args = dict(mcp.calls[-1][1])
    assert order_args["couponCodeList"] == ["SY120035343418848269"]
