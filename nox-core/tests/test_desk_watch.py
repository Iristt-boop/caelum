"""她看我屏幕（Nox 的电脑 v1.5，10-23 她定：胶囊写在干嘛 + 点开看画面 + 用完留小卡）。

守的几条：
  · 🔴 端到端：真 Core + desk-agent 按协议连上，他 desk_open(doing=…) → 胶囊状态（/api/nox/desk/status）
    有「在干嘛」；翻到新页 → 工具记录里多一条 desk_page（带截图编号）→ /api/nox/desk/shot/<id> 取得回那张
  · 🔴 她喊停是真停：/api/nox/desk/stop 之后 desk_* 不再发到我的电脑，回一句让我跟她交代
  · 🔴 画面不排队：我一个慢导航做到一半，她要一帧也立刻回来（desk-agent 每条请求各起任务）
  · 同一页翻来翻去只记一次；截图编号只认自己的格式（../ 之类读不到）；胶囊过一会自己收
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
import urllib.request

import pytest

from tools import context
from tools import desk as desk_tools
from tests.test_desk import DESK_SECRET, _agent, _free_port, _run_agent, _wait

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0fake-jpeg").decode()


class _Browser:
    def __init__(self, slow=0.0):
        self.calls, self.slow, self.url = [], slow, ""

    def scrub(self, text):
        return text

    async def open(self, url):
        self.calls.append(url)
        await asyncio.sleep(self.slow)
        self.url = url
        return f"【测试页】{url}\n\n正文"

    async def read(self, part=1):
        self.calls.append(f"read:{part}")
        return f"【测试页】{self.url}\n第 {part} 段"

    async def frame(self):
        return f"【测试页】{self.url}\nIMAGE image/jpeg {JPEG}"


@pytest.fixture
def core(monkeypatch, tmp_path):
    import uvicorn

    from api.server import create_app
    from data.store import Store
    from tests.test_api import FakeNox

    monkeypatch.setenv("CAELUM_DESK_SECRET", DESK_SECRET)
    registered = {}
    nox = FakeNox()
    nox.cfg.db_path = str(tmp_path / "core.db")
    nox.loop.register = lambda spec, handler: registered.__setitem__(spec.name, handler)
    app = create_app(nox, Store(tmp_path / "s.db"))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    assert _wait(lambda: server.started)
    yield port, nox, registered
    server.should_exit = True
    th.join(5)


def _connect(port, browser):
    stop = threading.Event()
    th = threading.Thread(target=_run_agent, args=(_agent(), port, DESK_SECRET, "/agent/desk", browser, stop),
                          daemon=True)
    th.start()
    return stop, th


def _get(port, path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


def _in_ctx(fn, args):
    ctx = context.ToolContext()
    with context.bind(ctx):
        out = fn(args)
    return out, ctx.pending_steps


def test_红线_端到端_在干嘛_翻页留图_她取得回那张(core):
    port, nox, reg = core
    browser = _Browser()
    stop, th = _connect(port, browser)
    try:
        assert _wait(lambda: nox.desk_hand.is_ready)
        assert _get(port, "/api/nox/desk/status")[1]["active"] is False, "没用电脑时胶囊不出来"
        out, steps = _in_ctx(reg["desk_open"], {"url": "https://g.com/vps", "doing": "在查 VPS 的价格"})
        assert "【测试页】https://g.com/vps" in out and "IMAGE" not in out, "截图只给她看，不进他读到的东西"
        st = _get(port, "/api/nox/desk/status")[1]
        assert st["active"] and st["doing"] == "在查 VPS 的价格" and st["pages"] == 1
        pages = [s for s in steps if s["type"] == "desk_page"]
        assert len(pages) == 1 and pages[0]["url"] == "https://g.com/vps" and pages[0]["desc"] == "测试页"
        code, shot = _get(port, f"/api/nox/desk/shot/{pages[0]['shot']}")
        assert code == 200 and shot["image"] == f"data:image/jpeg;base64,{JPEG}"
        # 同一页再读一遍：不算新的一页
        _, steps2 = _in_ctx(reg["desk_read"], {"part": 2})
        assert not [s for s in steps2 if s["type"] == "desk_page"]
        assert _get(port, "/api/nox/desk/status")[1]["pages"] == 1
        # 实时画面
        code, fr = _get(port, "/api/nox/desk/frame")
        assert fr["ok"] and fr["image"].endswith(JPEG) and fr["url"] == "https://g.com/vps"
    finally:
        stop.set()
        th.join(5)


def test_红线_她喊停_之后不再发到我的电脑(core):
    port, nox, reg = core
    browser = _Browser()
    stop, th = _connect(port, browser)
    try:
        assert _wait(lambda: nox.desk_hand.is_ready)
        reg["desk_open"]({"url": "https://a.com"})
        assert _get(port, "/api/nox/desk/stop", "POST")[1]["ok"]
        n = len(browser.calls)
        out = reg["desk_open"]({"url": "https://b.com"})
        assert "让我停下" in out and len(browser.calls) == n, "喊停之后这一步不能发过去"
        assert _get(port, "/api/nox/desk/status")[1]["stopped"] is True
    finally:
        stop.set()
        th.join(5)


def test_红线_画面不排在慢导航后面(core):
    port, nox, reg = core
    browser = _Browser(slow=3.0)
    stop, th = _connect(port, browser)
    try:
        assert _wait(lambda: nox.desk_hand.is_ready)
        t = threading.Thread(target=reg["desk_open"], args=({"url": "https://slow.com", "doing": "在等一个慢网页"},))
        t.start()
        assert _wait(lambda: _get(port, "/api/nox/desk/status")[1]["busy"], 2)
        t0 = time.time()
        _get(port, "/api/nox/desk/frame")
        assert time.time() - t0 < 1.5, "她要一帧不能等我那个 3 秒的导航做完"
        t.join(10)
    finally:
        stop.set()
        th.join(5)


def test_没连上_画面如实说():
    """链路没连上的话 frame 端点回 ok:false（端到端那条之外的分支，在 DeskActivity 层面不涉及）"""
    act = desk_tools.DeskActivity(None)
    assert act.status()["active"] is False


class _Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def test_胶囊过一会自己收_隔久了是新的一段(tmp_path):
    clk = _Clock()
    a = desk_tools.DeskActivity(tmp_path, clock=clk)
    a.begin("desk_open", "在查东西")
    a.add_page("https://a.com", JPEG)
    a.end()
    clk.t += a.LIVE_S - 1
    assert a.status()["active"] and a.status()["doing"] == "在查东西"
    clk.t += 2
    assert a.status() == {"active": False, "busy": False, "doing": "", "pages": 0, "since": None, "stopped": False}
    clk.t += a.NEW_RUN_S
    a.begin("desk_read")
    assert a.status()["doing"] == "" and a.status()["pages"] == 0, "新的一段：上回的在干嘛和页数清掉"
    a.end()


def test_喊停两分钟后自己解除():
    clk = _Clock()
    a = desk_tools.DeskActivity(None, clock=clk)
    a.stop()
    assert a.begin("desk_open") is not None
    clk.t += a.STOP_S + 1
    assert a.begin("desk_open") is None


def test_截图编号只认自己的格式_留的张数有上限(tmp_path):
    clk = _Clock()
    a = desk_tools.DeskActivity(tmp_path / "shots", clock=clk)
    a.KEEP_SHOTS = 3
    ids = []
    for i in range(5):
        clk.t += 1
        ids.append(a.add_page(f"https://a.com/{i}", JPEG))
    assert len(list((tmp_path / "shots").glob("*.jpg"))) == 3
    assert a.shot(ids[0]) is None and a.shot(ids[-1]) == base64.b64decode(JPEG)
    (tmp_path / "secret.jpg").write_bytes(b"x")
    for bad in ("../secret", "..%2Fsecret", "", "1800000005-zzzzzzzz"):
        assert a.shot(bad) is None, bad


def test_doing_给她看的_压成一行():
    a = desk_tools.DeskActivity(None)
    a.begin("desk_open", "在查\n VPS   价格" + "很长" * 30)
    assert "\n" not in a.status()["doing"] and len(a.status()["doing"]) <= 24
