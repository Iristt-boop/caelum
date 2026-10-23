"""我的电脑 v2：登录（她填表、我不经手）+ 登录着的站上写动作出确认卡（10-23，设计稿 5.2）。

守的几条（端到端：真 Core + desk-agent 按协议连上 + 真加密）：
  · 🔴 哨兵密码只在「她的手机加密前」和「我电脑解开后」出现：他的工具结果、Core 的日志、Core 的每个回包里都没有
  · 🔴 他拿到的只有一句「已经请她填了」：框有哪些、标签、公钥都不进他的工具结果
  · 🔴 第一次见的站：她不勾「允许」就不填；同一张卡不能交两次
  · 🔴 登录着的站上点 / 填：不直接做，出确认卡；她确认才由端点去做（带着「还得是卡上那个东西」的核对）
  · 🔴 付款 / 下单一类的按钮：确认卡也不发
  · 没登录的站照常点；退出之后也照常点；结果下一次用电脑时告诉他
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

import pytest

from tests.test_desk import DESK_SECRET, _agent, _desk_login, _free_port, _run_agent, _wait
from tests.test_desk_watch import JPEG, _get

desk_login = _desk_login()


PW, USER = "PW-SENTINEL-4b7e-密码", "tangtang-USER-SENTINEL"


class _Browser:
    """desk-agent 的 Browser 换成假的页面，但登录那几步用真的 desk_login（真生成钥匙、真解密）"""

    def __init__(self):
        self.url, self.card, self.got, self.calls, self.button = "", None, None, [], "加入购物车"

    def scrub(self, text):
        return text

    async def open(self, url):
        self.url = url
        return f"【测试页】{url}\n\n正文"

    async def read(self, part=1):
        return f"【测试页】{self.url}\n第 {part} 段"

    async def frame(self):
        return f"【测试页】{self.url}\nIMAGE image/jpeg {JPEG}"

    async def login_begin(self):
        fields = [{"key": "user", "kind": "user", "label": "账号"}, {"key": "pass", "kind": "pass", "label": "密码"}]
        self.card = desk_login.new_card(self.url, None, fields)
        c = self.card
        return json.dumps({"login_id": c.login_id, "origin": c.origin, "fields": fields,
                           "public_key": c.public_spki, "expires_in": 180})

    async def login_fill(self, login_id, epk, iv, ct):
        card, self.card = self.card, None
        if card is None or card.login_id != login_id:
            raise ValueError("这张登录卡已经作废了")
        self.got = desk_login.decrypt(card, epk, iv, ct)
        return json.dumps({"status": "ok", "site": desk_login.site_of("login.taobao.com"), "message": "登进去了"})

    async def login_cancel(self, login_id):
        self.card = None
        return "好，不登了。"

    async def logout(self, site):
        self.calls.append(("logout", site))
        return f"退出了 {site}"

    async def describe(self, ref):
        return json.dumps({"kind": "按钮", "name": self.button, "url": self.url}, ensure_ascii=False)

    async def click(self, ref, expect=None):
        self.calls.append(("click", ref, expect))
        return f"点了 [{ref}]\n【购物车】{self.url}\n加好了"


@pytest.fixture
def core(monkeypatch, tmp_path):
    import uvicorn

    from api.server import create_app
    from data.store import Store
    from tests.test_api import FakeNox

    monkeypatch.setenv("CAELUM_DESK_SECRET", DESK_SECRET)
    reg = {}
    nox = FakeNox()
    nox.cfg.db_path = str(tmp_path / "core.db")
    nox.loop.register = lambda spec, handler: reg.__setitem__(spec.name, handler)
    app = create_app(nox, Store(tmp_path / "s.db"))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    assert _wait(lambda: server.started)
    browser = _Browser()
    stop = threading.Event()
    ag = threading.Thread(target=_run_agent, args=(_agent(), port, DESK_SECRET, "/agent/desk", browser, stop),
                          daemon=True)
    ag.start()
    assert _wait(lambda: nox.desk_hand.is_ready)
    yield port, nox, reg, browser, tmp_path
    stop.set()
    ag.join(5)
    server.should_exit = True
    th.join(5)


def _post(port, path, body=None):
    import urllib.request
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _her_phone(card, values):
    """她的手机做的事：用卡上的公钥加密（Python 版，和 shared/desk/login-crypto.js 同一套）"""
    return desk_login.encrypt_for(card["public_key"], card["id"], values)


def _login(port, reg, allow=True):
    reg["desk_open"]({"url": "https://login.taobao.com/member/login.jhtml"})
    told = reg["desk_request_login"]({})
    card = _get(port, "/api/nox/desk/status")[1]["login"]
    return told, card, _post(port, f"/api/nox/desk/login/{card['id']}",
                             {**_her_phone(card, {"user": USER, "pass": PW}), "allow": allow})


def test_红线_端到端_哨兵密码哪儿都没有_他只拿到一句话(core, caplog):
    port, nox, reg, browser, tmp = core
    caplog.set_level(logging.DEBUG)
    reg["desk_open"]({"url": "https://login.taobao.com/member/login.jhtml"})
    told = reg["desk_request_login"]({})
    assert "已经请糖糖在她那边登录 taobao.com" in told
    assert "账号" not in told.replace("账号密码你看不到", "") and "public" not in told and "MF" not in told, \
        "框有哪些、公钥都只进她的卡"

    st = _get(port, "/api/nox/desk/status")[1]
    card = st["login"]
    assert card["site"] == "taobao.com" and card["host"] == "login.taobao.com" and card["first_seen"] is True
    assert [f["kind"] for f in card["fields"]] == ["user", "pass"] and card["public_key"]

    sealed = _her_phone(card, {"user": USER, "pass": PW})
    code, r = _post(port, f"/api/nox/desk/login/{card['id']}", {**sealed, "allow": False})
    assert code == 409 and "允许" in r["detail"] and browser.got is None, "第一次见的站：不勾允许就不填"

    code, r = _post(port, f"/api/nox/desk/login/{card['id']}", {**sealed, "allow": True})
    assert code == 200 and r["status"] == "ok"
    assert browser.got == {"user": USER, "pass": PW}, "我电脑那边解开的就是她填的"
    code, _ = _post(port, f"/api/nox/desk/login/{card['id']}", {**sealed, "allow": True})
    assert code == 409, "同一张卡不能交两次"

    after = reg["desk_read"]({})
    assert after.startswith("（糖糖帮你登上了 taobao.com"), "结果在他下一次用电脑时告诉他"
    sites = _get(port, "/api/nox/desk/sites")[1]["sites"]
    assert [s["site"] for s in sites] == ["taobao.com"]
    saved = (tmp / "desk-login.json").read_text(encoding="utf-8")

    seen = [told, after, json.dumps(st, ensure_ascii=False), json.dumps(r, ensure_ascii=False), saved, caplog.text]
    for i, blob in enumerate(seen):
        assert PW not in blob and USER not in blob, f"🔴 哨兵值漏进了第 {i} 处"


def test_红线_登录着的站_点按钮出确认卡_她确认才做(core):
    port, nox, reg, browser, _ = core
    _login(port, reg)
    reg["desk_open"]({"url": "https://cart.taobao.com/cart.htm"})
    out = reg["desk_click"]({"ref": 3})
    assert "确认卡" in out and not [c for c in browser.calls if c[0] == "click"], "不直接点"
    conf = _get(port, "/api/nox/desk/status")[1]["confirm"]
    assert conf["text"] == "在 taobao.com 点「加入购物车」"
    code, r = _post(port, f"/api/nox/desk/action/{conf['id']}/confirm")
    assert code == 200 and r["ok"]
    assert browser.calls[-1] == ("click", 3, {"name": "加入购物车", "url": "https://cart.taobao.com/cart.htm"}), \
        "确认端点去点，还带着「得是卡上那个东西」的核对"
    assert _post(port, f"/api/nox/desk/action/{conf['id']}/confirm")[0] == 409, "连点两次只做一次"
    assert "已经替你在 taobao.com 点「加入购物车」" in reg["desk_read"]({})


def test_红线_付款下单一类_确认卡也不发(core):
    port, nox, reg, browser, _ = core
    _login(port, reg)
    reg["desk_open"]({"url": "https://buy.taobao.com/order"})
    browser.button = "提交订单"
    out = reg["desk_click"]({"ref": 9})
    assert "不碰" in out and _get(port, "/api/nox/desk/status")[1]["confirm"] is None
    assert not [c for c in browser.calls if c[0] == "click"]


def test_没登录的站照常点_退出之后也照常点(core):
    port, nox, reg, browser, _ = core
    reg["desk_open"]({"url": "https://www.example.com/"})
    reg["desk_click"]({"ref": 1})
    assert browser.calls[-1][0] == "click", "没登录的站不出卡"
    _login(port, reg)
    assert _post(port, "/api/nox/desk/sites/taobao.com/logout")[1]["ok"]
    assert ("logout", "taobao.com") in browser.calls and _get(port, "/api/nox/desk/sites")[1]["sites"] == []
    reg["desk_open"]({"url": "https://cart.taobao.com/cart.htm"})
    reg["desk_click"]({"ref": 2})
    assert browser.calls[-1][:2] == ("click", 2), "退出了就不算登录着的站"


def test_她取消_卡没了_他下次知道(core):
    port, nox, reg, browser, _ = core
    reg["desk_open"]({"url": "https://login.taobao.com/x"})
    reg["desk_request_login"]({})
    cid = _get(port, "/api/nox/desk/status")[1]["login"]["id"]
    assert _post(port, f"/api/nox/desk/login/{cid}/cancel")[1]["ok"]
    assert _get(port, "/api/nox/desk/status")[1]["login"] is None and browser.card is None
    assert "她取消了" in reg["desk_read"]({})


def test_主域名口径():
    from tools.desk_auth import site_of
    assert site_of("login.taobao.com") == "taobao.com"
    assert site_of("taobao.com.evil.com") == "evil.com", "冒充的子域名显示真正的主域名"
    assert site_of("www.bbc.co.uk") == "bbc.co.uk" and site_of("passport.jd.com.cn") == "jd.com.cn"
    assert site_of("panel.noxtang.com") == desk_login.site_of("panel.noxtang.com") == "noxtang.com"
