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
        #: 带上付款链接 —— 真实的 createOrder 会给一个 `weixin://`
        #: （糖糖 2026-09-06 实测）。有了它，happy path 才能断言
        #: 链接真被提取出来了，从而直接抓住「模块没导入」这种错
        self.order_result = order_result or {
            "orderId": "LK202609060991",
            "payUrl": "weixin://wxpay/bizpayurl?pr=TESTONLY",
        }

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


@pytest.mark.parametrize("payload,want", [
    # 字段名各种写法都能找到 —— 因为**不按字段名找**
    ({"payUrl": "weixin://wxpay/bizpayurl?pr=A"}, "weixin://wxpay/bizpayurl?pr=A"),
    ({"wxPayUrl": "weixin://x"}, "weixin://x"),
    ({"某个没见过的字段": "weixin://y"}, "weixin://y"),
    # 藏在嵌套里
    ({"data": {"payment": {"link": "weixin://z"}}}, "weixin://z"),
    ({"list": [{"a": 1}, {"deepLink": "weixin://w"}]}, "weixin://w"),
    # 支付宝和 H5 收银台也认（万一哪天换了）
    ({"x": "alipays://platformapi/x"}, "alipays://platformapi/x"),
    ({"x": "https://wx.tenpay.com/cgi-bin/x"}, "https://wx.tenpay.com/cgi-bin/x"),
    # 没有就是没有，不许瞎给一个
    ({"orderId": "LK123", "status": 1}, ""),
    ({}, ""),
    ({"note": "订单已创建"}, ""),
])
def test_pay_link_is_found_by_shape_not_by_field_name(payload, want):
    """🔴 **P2 的命根子。**

    第一版按字段名猜（`payUrl` / `payLink` / `wxPayUrl` …）。猜错的后果
    很特别：下单**成功了**（钱那边的单子真建了），但她拿不到付款链接，
    这一单卡在那儿，而系统以为一切正常 —— 不报错、不回滚、不重试。

    所以不认字段名，只认值长什么样。
    """
    assert flow.find_pay_link(payload) == want


#: 🔴 **createOrder 的真实响应**（2026-09-06 从日志里读到的形状，不是猜的）。
#: 这是第一次真正看到它长什么样 —— 之前两版都在猜字段名。
REAL_ORDER_RESULT = {
    "orderId": 7682330841411158026,
    "payOrderUrl": "weixin://wxpay/bizpayurl?pr=REDACTED",
    "payOrderQrCodeUrl": "https://payqr.example.lkcoffee.com/x.png",
    "discountPrice": 10.9,
    "needPay": True,
    "tradeNo": None, "description": None, "businessNotifyUrl": None,
    "subMchid": None, "orderIdStr": "7682330841411158026", "payParams": None,
}


def test_native_scan_link_is_flagged():
    """🔴 `weixin://wxpay/bizpayurl?pr=…` 是微信支付的 NATIVE（扫码）链接，
    **点了不弹支付**。

    2026-09-06 实测：糖糖点了「微信付款」，微信启动了但什么都没出来。
    她第一次手动下单时是「复制链接 → 去微信粘贴打开」，那条路是通的。
    所以前端要据此把主操作从「跳转」换成「复制」。
    """
    url, qr = flow.find_links(REAL_ORDER_RESULT)
    assert url.startswith("weixin://wxpay/bizpayurl")
    assert flow.is_native_scan(url) is True
    assert qr.startswith("https://"), "二维码图没取到"


@pytest.mark.parametrize("url,native", [
    ("weixin://wxpay/bizpayurl?pr=X", True),
    ("https://wx.tenpay.com/cgi-bin/x", False),   # H5 收银台，点得开
    ("alipays://platformapi/x", False),
    ("", False),
])
def test_only_native_urls_are_scan_only(url, native):
    """别把能点的链接也标成扫码 —— 那会让她白白多复制一次。"""
    assert flow.is_native_scan(url) is native


def test_qr_field_must_be_an_image_url():
    """二维码那个字段要是也给了 weixin://，那它不是图，当没有。"""
    _, qr = flow.find_links({"payOrderUrl": "weixin://wxpay/bizpayurl?pr=A",
                             "payOrderQrCodeUrl": "weixin://wxpay/bizpayurl?pr=A"})
    assert qr == ""


def test_shape_never_leaks_values():
    """把响应结构记进日志是为了「下次不用猜」，但**不能把值带出去** ——
    里面有付款链接和订单号。"""
    s = flow.shape({"payUrl": "weixin://secret", "orderId": "LK999", "n": 3})
    blob = json.dumps(s, ensure_ascii=False)
    assert "weixin://secret" not in blob and "LK999" not in blob
    assert "payUrl" in blob and "str" in blob


def test_stale_pay_link_is_treated_as_gone(store):
    """过期的收银台链接点了也是报错页，不如当没有。"""
    oid = store.create(session_id="s", merchant="luckin",
                       snapshot={"card": {}, "args": {}}, fingerprint="fp", now=NOW)
    store.set_pay_link(oid, "weixin://x", now=NOW)
    assert store.pay_link(oid, now=NOW + timedelta(hours=3)) is None


# ---------------------------------------------------------------- 串起来


# ---------------------------------------------------------------- 端点（真的打一次）
#
# 🔴 **2026-09-06 事故补的这一组。**
#
# 那天上线后糖糖点了「确认下单」：瑞幸那边订单真建出来了（小程序里
# 看得到「未支付」），我们这边却显示「下单失败」——
# `api/server.py` 里 `luckin_order_flow` 这个名字**没导入**，
# `createOrder` 成功返回之后下一行 NameError，于是 set_state 没跑到。
#
# **她看到失败，而钱那边是成功的。** 比下单失败糟得多 —— 她会再点一次。
#
# 上面那些测试一条都没拦住，因为它们全在工具层，**从没调用过那个端点**。
# py_compile 也拦不住（NameError 是运行期的名字查找）。
# 所以这里必须真的把 HTTP 请求打进去。


class _Cfg:
    history_limit = 40
    recent_window_tokens = 8000
    context_budget_tokens = 20000

    class primary:  # noqa: N801
        model = "fake"

    class utility:
        usable = False
        model = ""
        base_url = ""

    def __init__(self, db_path):
        self.db_path = str(db_path)


class _Core:
    """够 create_app 跑起来的最小 core。"""

    def __init__(self, db_path, mcp):
        self.cfg = _Cfg(db_path)
        self.loop = type("L", (), {"tools": {}, "register": lambda *a, **k: None})()
        self.bridge = None
        self.system_prompt = "（前缀）"
        self.current_session_id = None
        self.context = type("C", (), {"get": staticmethod(lambda n: None)})()
        self.router = type("R", (), {"light_adapter": None})()
        self.luckin_client = mcp

    def model_name(self, model=None):
        return model or "fake"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api.server import create_app
    from data.store import Store

    monkeypatch.delenv("NOX_ATTENTION", raising=False)
    mcp = FakeMcp()
    core = _Core(tmp_path / "sessions.db", mcp)
    s = Store(tmp_path / "sessions.db")
    c = TestClient(create_app(core, s))
    yield c, core, mcp
    s.close()


def test_confirm_endpoint_actually_works(client):
    """🔴 把请求真的打进去 —— 这是唯一能拦住端点里 NameError 的测试。"""
    c, core, mcp = client
    oid = core.orders.create(
        session_id="s-1", merchant="luckin",
        snapshot={"card": {"actual_fen": 1090}, "args": ARGS},
        fingerprint=luckin_tools.snapshot_for(mcp, ARGS)[2])

    r = c.post(f"/api/nox/orders/{oid}/confirm")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    assert body["state"] == orders_mod.PENDING_PAYMENT
    assert core.orders.get(oid)["state"] == orders_mod.PENDING_PAYMENT
    # 🔴 断言链接**真被提取出来了**。少这一条的话，
    # 「取链接那段整个炸了」会被结构兜底吞掉而测试照样绿 ——
    # 那正是 2026-09-06 事故里最难发现的那一半
    assert core.orders.pay_link(oid) == "weixin://wxpay/bizpayurl?pr=TESTONLY"


def test_order_is_never_lost_when_bookkeeping_blows_up(client, monkeypatch):
    """🔴 **下单成功之后的任何失败，都不许把「已经下单」这个事实丢掉。**

    2026-09-06 那次就是：createOrder 成功了，下一行炸了，
    于是库里停在 confirmed、没商家单号、没链接，前端显示失败，
    而瑞幸那边订单好好挂着。她会以为可以再点一次。

    所以顺序写死：先落状态，再做别的；后面全包在 try 里。
    """
    c, core, mcp = client
    oid = core.orders.create(
        session_id="s-1", merchant="luckin",
        snapshot={"card": {"actual_fen": 1090}, "args": ARGS},
        fingerprint=luckin_tools.snapshot_for(mcp, ARGS)[2])

    # 让「下单之后」的那段炸掉
    import api.server as srv
    monkeypatch.setattr(srv.luckin_order_flow, "find_pay_link",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("炸了")))

    r = c.post(f"/api/nox/orders/{oid}/confirm")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == orders_mod.PENDING_PAYMENT
    row = core.orders.get(oid)
    assert row["state"] == orders_mod.PENDING_PAYMENT, "订单被丢了 —— 她会以为可以再点一次"
    assert row["merchant_order_id"] == "LK202609060991"


def test_confirm_twice_over_http_places_one_order(client):
    """她连点两次「确认下单」，走真实 HTTP 也只能下一单。"""
    c, core, mcp = client
    oid = core.orders.create(
        session_id="s-1", merchant="luckin",
        snapshot={"card": {"actual_fen": 1090}, "args": ARGS},
        fingerprint=luckin_tools.snapshot_for(mcp, ARGS)[2])

    first = c.post(f"/api/nox/orders/{oid}/confirm").json()
    second = c.post(f"/api/nox/orders/{oid}/confirm").json()
    assert first["ok"] is True and second["ok"] is False
    assert mcp.tools.count("createOrder") == 1, "下了两单"


def test_pay_endpoint_returns_the_link(client):
    """卡片二刷新之后要能重新拿到链接。"""
    c, core, mcp = client
    oid = core.orders.create(session_id="s", merchant="luckin",
                             snapshot={"card": {"actual_fen": 1090}, "args": {}},
                             fingerprint="fp")
    core.orders.set_pay_link(oid, "weixin://wxpay/bizpayurl?pr=AAA")
    d = c.get(f"/api/nox/orders/{oid}/pay").json()
    assert d["has_link"] is True
    assert d["pay_url"].startswith("weixin://")


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
