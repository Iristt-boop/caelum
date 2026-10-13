"""Creations S1.1（2026-10-13，糖糖选的）：他自己的时间里，没人要、他自己想给她做一个。

守的几条：
  · 只在他自己的时间里有（leave_creation 不进聊天那个 loop）；做好**只放进 Studio，不发到聊天**（R1：那段时间不开口）
  · 🔴 节制在代码里：上一个自己做的她还没打开 → 不给；离上一个不到 OWN_EVERY → 不给；一次只做一个
  · 「打开过」只认她真点开（?open=1），Studio 卡片上滑过去的缩小预览不算
  · 旧库（10-11 建的）开起来自动补列，原来那些算「她要的」
  · 从使用方断言：tick 真把工具和那句提示交给了模型；Studio 接口真回了 origin / note / opened
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agent.llm import StreamEvent, ToolCall, Turn, Usage
from attention import own_time
from tools import context
from tools import creations as cr

HTML = "<!doctype html><html><body><canvas id=c></canvas><script>let rain=1</script></body></html>"


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def store(tmp_path):
    return cr.CreationStore(tmp_path / "creations.db")


def _leave(store, now=None, **args):
    tools = cr.own_time_tools(store, now or _now())
    assert tools, "门应该是开的"
    (spec, handler), = tools
    ctx = context.ToolContext(session_id="own-time-x", user_text="")
    with context.bind(ctx):
        out = handler({"title": "听雨", "html": HTML, "note": "你说最近睡不好", **args})
    return out, ctx.attachments


def _age(store, days):
    """把他自己做的那些往前挪几天（假装是几天前做的）"""
    then = (datetime.now(timezone.utc) - timedelta(days=days, minutes=1)).isoformat()
    store._conn.execute("UPDATE creations SET created_at=? WHERE origin=?", (then, cr.ORIGIN_OWN))
    store._conn.commit()


# ---- 做 ---------------------------------------------------------------------

def test_红线_自己做的_进Studio_不发到聊天(store):
    out, att = _leave(store)
    assert att == [], "他自己的时间不开口 —— 不许出聊天卡片"
    (c,) = store.list()
    assert (c["title"], c["origin"], c["note"], c["opened"]) == ("听雨", "own_time", "你说最近睡不好", False)
    assert "没有发到聊天" in out


def test_不合格的照样不收(store):
    out, _ = _leave(store, html="就一句话")
    assert out.startswith("没做成") and store.list() == []


def test_留言太长截断(store):
    _leave(store, note="想" * 200)
    assert len(store.list()[0]["note"]) == cr.MAX_NOTE


# ---- 门 ---------------------------------------------------------------------

def test_红线_上一个她还没打开_不给做(store):
    _leave(store)
    _age(store, 30)
    assert cr.own_time_tools(store, _now()) == []
    assert "还没打开" in cr.own_gate(store, _now())


def test_红线_打开了但才隔一天_不给_隔够了才给(store):
    _leave(store)
    store.mark_opened(store.list()[0]["id"])
    _age(store, 1)
    assert cr.own_time_tools(store, _now()) == [] and "才 1 天" in cr.own_gate(store, _now())
    _age(store, cr.OWN_EVERY.days)
    assert cr.own_time_tools(store, _now()) != []


def test_她要的那些不算_不挡他自己做(store):
    cr.make_handlers(store)["make_creation"]({"title": "五子棋", "html": HTML})
    assert cr.own_gate(store, _now()) is None


def test_她删了也算做过_间隔照样算(store):
    _leave(store)
    store.delete(store.list()[0]["id"])
    assert "才 0 天" in cr.own_gate(store, _now()), "删了就能立刻再做一个 = 删一个来一个"


def test_红线_同一次里做第二个_不收(store):
    tools = cr.own_time_tools(store, _now())
    (_, handler), = tools
    handler({"title": "听雨", "html": HTML, "note": "a"})
    out = handler({"title": "又一个", "html": HTML, "note": "b"})
    assert out.startswith("这次不做了") and [c["title"] for c in store.list()] == ["听雨"]


# ---- 她打开过没有 -----------------------------------------------------------

def test_他查得到哪个是自己做的_她打开没有(store):
    _leave(store)
    lst = cr.make_handlers(store)["list_creations"]({})
    assert "你自己想做的，她还没打开过" in lst
    store.mark_opened(store.list()[0]["id"])
    lst = cr.make_handlers(store)["list_creations"]({})
    assert "你自己想做的" in lst and "还没打开" not in lst


def test_红线_端点_预览不算打开_真点开才算(store):
    from tests.test_api import FakeNox, client_for
    fake = FakeNox()
    c = client_for(fake)
    fake.creations = store
    _leave(store)
    cid = store.list()[0]["id"]
    item = c.get("/api/nox/creations").json()["items"][0]
    assert (item["origin"], item["note"], item["opened"]) == ("own_time", "你说最近睡不好", False)
    got = c.get(f"/api/nox/creations/{cid}").json()
    assert got["note"] == "你说最近睡不好" and got["origin"] == "own_time"
    assert c.get("/api/nox/creations").json()["items"][0]["opened"] is False, "Studio 卡片的缩小预览不算她打开"
    c.get(f"/api/nox/creations/{cid}?open=1")
    assert c.get("/api/nox/creations").json()["items"][0]["opened"] is True


def test_旧库开起来自动补列_原来的算她要的(tmp_path):
    p = tmp_path / "creations.db"
    con = sqlite3.connect(p)
    con.execute("""CREATE TABLE creations (id TEXT PRIMARY KEY, title TEXT NOT NULL, html TEXT NOT NULL,
                   prev_html TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT)""")
    con.execute("INSERT INTO creations VALUES ('old1','水积成河','<div>x</div>',NULL,'2026-10-11T09:23:55+00:00',"
                "'2026-10-11T09:23:55+00:00',NULL)")
    con.commit()
    con.close()
    s = cr.CreationStore(p)
    (c,) = s.list()
    assert (c["id"], c["origin"], c["note"], c["opened"]) == ("old1", "asked", "", False)
    assert cr.own_gate(s, _now()) is None, "她要的不挡他"


# ---- 接线：他自己的时间那一轮 ------------------------------------------------

class _Maker:
    """第一轮：调 leave_creation；第二轮：交代。记下模型看到的工具和那句话"""
    name = "fake"

    def __init__(self):
        self.calls, self.tools, self.first = 0, [], ""

    def stream(self, messages, tools, **kw):
        self.calls += 1
        if self.calls == 1:
            self.tools = [t.name for t in tools]
            self.first = str(messages[-1].get("content") if isinstance(messages[-1], dict) else messages[-1])
            if "leave_creation" in self.tools:
                tc = [ToolCall(id="c1", name="leave_creation",
                               arguments={"title": "听雨", "html": HTML, "note": "你说最近睡不好"})]
                yield StreamEvent("done", turn=Turn(stop_reason="tool_use", text="", tool_calls=tc, usage=Usage()))
                return
        t = "做了什么：给她做了个听雨的小东西放进 Studio\n想跟她说：无"
        yield StreamEvent("text", text=t)
        yield StreamEvent("done", turn=Turn(stop_reason="end_turn", text=t, usage=Usage()))


@pytest.fixture
def live(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from api.server import create_app
    from data.store import Store
    from tests.test_api import FakeNox
    from tests.test_api_world import _Ctx
    from tests.test_own_time import _full_loop

    monkeypatch.setenv("NOX_ATTENTION", "1")
    nox = FakeNox()
    nox.cfg.db_path = str(tmp_path / "nox.db")
    nox.context = _Ctx()
    nox.bridge = None
    nox.current_session_id = None
    ad = _Maker()
    nox.loop = _full_loop(ad)
    nox.router = SimpleNamespace(light_adapter=None)
    nox.dynamic_kwargs = []
    nox._dynamic = lambda *a, **k: nox.dynamic_kwargs.append((a, k)) or "（动态块）"
    nox._system = "人设"
    app = create_app(nox, Store(tmp_path / "sessions.db"))
    return TestClient(app), app, ad, nox


def _due(app):
    from tests.test_own_time import _attention
    now = datetime.now(timezone.utc)
    _attention(app).store.set_source_state(own_time.PLAN_KEY, {
        "date": now.astimezone(own_time.CST).date().isoformat(),
        "at": (now - timedelta(minutes=1)).isoformat(), "done": False})
    return now


def test_红线_他自己的时间_门开着_模型真拿到工具和那句话_做好进Studio(live):
    c, app, ad, nox = live
    a = app.state.own_time_tick(_due(app))
    assert "leave_creation" in ad.tools and "make_creation" not in ad.tools, "聊天那个 make 不给（描述是「她开口才做」）"
    assert "list_creations" in ad.tools, "做之前要看得到做过哪些，别撞了"
    assert "leave_creation" in ad.first, "能做的那天，提示里要告诉他可以做"
    assert a.ok and [(t["tool_name"], t["status"]) for t in a.trace] == [("leave_creation", "success")]
    items = c.get("/api/nox/creations").json()["items"]
    assert [(i["title"], i["origin"]) for i in items] == [("听雨", "own_time")]
    assert "leave_creation" not in nox.loop.tools, "聊天那个 loop 里不许有"


def test_红线_门关着_工具和那句话都没有(live):
    c, app, ad, nox = live
    nox.creations.create("上次的", HTML, origin=cr.ORIGIN_OWN, note="")     # 她还没打开
    app.state.own_time_tick(_due(app))
    assert "leave_creation" not in ad.tools and "leave_creation" not in ad.first
    assert len(nox.creations.list()) == 1
