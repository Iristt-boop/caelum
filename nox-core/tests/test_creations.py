"""Creations（2026-10-11，他给她做的单文件小网页；菜单 Studio）。

守的几条（设计稿第八节 + 10-11 修订）：
  · 超限 / 不像网页的不收，不发到聊天里
  · edit 保持同一个 id（她在里面存的东西不丢）；find 必须正好一处；能退回上一版
  · 删了 = HTML 清空 + Studio 看不到 + 他也查不到
  · 从使用方断言：工具真的把 creation 附件交给了 loop 的结果（bridge 靠它出聊天卡片）；端点真的能取 / 删
  · 五个工具名不和别的模块撞；平常聊天不背这五个（按需工具包）
"""

from __future__ import annotations

import pytest

from agent import toolpacks
from agent.llm import ToolCall, Turn
from agent.loop import AgentLoop
from tools import context
from tools import creations as cr

HTML = "<!doctype html><html><body><div id=b>棋盘</div><script>let x=1</script></body></html>"


@pytest.fixture
def store(tmp_path):
    return cr.CreationStore(tmp_path / "creations.db")


def _run(h, name, args):
    ctx = context.ToolContext(session_id="s", user_text="")
    with context.bind(ctx):
        out = h[name](args)
    return out, ctx.attachments


# ---- make -------------------------------------------------------------------

def test_做一个_存下_发到聊天(store):
    h = cr.make_handlers(store)
    out, att = _run(h, "make_creation", {"title": "五子棋", "html": HTML})
    cid = out.split("id=")[1].split("，")[0]
    assert store.get(cid)["html"] == HTML
    assert att == [{"type": "creation", "creation_id": cid, "title": "五子棋"}]


@pytest.mark.parametrize("html,why", [
    ("", "空"), ("就一句话", "网页"), ("<div>" + "x" * (cr.MAX_BYTES + 1) + "</div>", "太大"),
], ids=["空", "不像网页", "超限"])
def test_红线_不合格的不收也不发(store, html, why):
    out, att = _run(cr.make_handlers(store), "make_creation", {"title": "x", "html": html})
    assert out.startswith("没做成") and why in out
    assert att == [] and store.list() == []


def test_没名字不收(store):
    out, att = _run(cr.make_handlers(store), "make_creation", {"title": " ", "html": HTML})
    assert att == [] and store.list() == []


# ---- edit -------------------------------------------------------------------

def _made(store):
    h = cr.make_handlers(store)
    out, _ = _run(h, "make_creation", {"title": "五子棋", "html": HTML})
    return h, out.split("id=")[1].split("，")[0]


def test_红线_小改_同一个id_重新发(store):
    h, cid = _made(store)
    out, att = _run(h, "edit_creation", {"id": cid, "find": "棋盘", "replace": "蓝色棋盘"})
    assert "id 不变" in out
    assert "蓝色棋盘" in store.get(cid)["html"]
    assert att[0]["creation_id"] == cid
    assert len(store.list()) == 1, "改不许变出第二个"


@pytest.mark.parametrize("find", ["不存在的一段", "<"])   # 0 次 / 多次
def test_find不是正好一处_不改(store, find):
    h, cid = _made(store)
    out, att = _run(h, "edit_creation", {"id": cid, "find": find, "replace": "y"})
    assert out.startswith("没改") and store.get(cid)["html"] == HTML and att == []


def test_大改_然后退回上一版(store):
    h, cid = _made(store)
    new = HTML.replace("棋盘", "转盘")
    _run(h, "edit_creation", {"id": cid, "html": new})
    assert store.get(cid)["html"] == new
    _run(h, "edit_creation", {"id": cid, "undo": True})
    assert store.get(cid)["html"] == HTML


def test_大改也要合格(store):
    h, cid = _made(store)
    out, _ = _run(h, "edit_creation", {"id": cid, "html": "纯文字"})
    assert out.startswith("没改") and store.get(cid)["html"] == HTML


def test_只改名(store):
    h, cid = _made(store)
    _run(h, "edit_creation", {"id": cid, "title": "蓝色五子棋"})
    assert store.get(cid)["title"] == "蓝色五子棋" and store.get(cid)["html"] == HTML


def test_没说怎么改(store):
    h, cid = _made(store)
    out, att = _run(h, "edit_creation", {"id": cid})
    assert "没说要怎么改" in out and att == []


# ---- send / list / delete ---------------------------------------------------

def test_重发不重做(store):
    h, cid = _made(store)
    _, att = _run(h, "send_creation", {"id": cid})
    assert att[0]["creation_id"] == cid and len(store.list()) == 1


def test_红线_删了_HTML清空_Studio和他都看不到(store):
    h, cid = _made(store)
    _run(h, "delete_creation", {"id": cid})
    assert store.get(cid) is None and store.list() == []
    out, _ = _run(h, "list_creations", {})
    assert "还没" in out
    out, att = _run(h, "send_creation", {"id": cid})
    assert "没有" in out and att == []
    raw = store._conn.execute("SELECT html, prev_html FROM creations WHERE id=?", (cid,)).fetchone()
    assert raw["html"] == "" and raw["prev_html"] is None, "删了就不留她不要的东西"


# ---- 接线 -------------------------------------------------------------------

def test_红线_经loop跑一轮_附件交给了结果(store):
    """bridge 靠 done 里的附件出聊天卡片 —— 测到 loop 的结果，不只测 handler。"""
    call = Turn(stop_reason="tool_use", tool_calls=[ToolCall(
        id="c1", name="make_creation", arguments={"title": "转盘", "html": HTML})])

    class A:
        name = "fake"
        script = [call, Turn(stop_reason="end_turn", text="做好了")]

        def complete(self, messages, tools, **kw):
            return self.script.pop(0)

    loop = AgentLoop(adapter=A())
    cr.register_all(loop, store)
    r = loop.run("帮我做个转盘", session_id="s")
    assert [a["type"] for a in r.attachments] == ["creation"]
    assert r.attachments[0]["title"] == "转盘"


def test_红线_端点_Studio列表_打开_删除(store):
    from tests.test_api import FakeNox, client_for
    fake = FakeNox()
    c = client_for(fake)
    fake.creations = store          # create_app 会建一个真库挂上去，测试换成临时库
    h, cid = _made(store)
    assert [i["id"] for i in c.get("/api/nox/creations").json()["items"]] == [cid]
    got = c.get(f"/api/nox/creations/{cid}").json()
    assert got["html"] == HTML and got["title"] == "五子棋"
    assert c.delete(f"/api/nox/creations/{cid}").json()["ok"]
    assert c.get(f"/api/nox/creations/{cid}").status_code == 404
    assert c.get("/api/nox/creations").json()["items"] == []


def test_名字不和别的模块撞():
    from agent.llm import ToolSpec
    import importlib
    import pkgutil
    import tools as pkg
    seen = {}
    for m in pkgutil.iter_modules(pkg.__path__):
        try:
            mod = importlib.import_module(f"tools.{m.name}")
        except Exception:  # noqa: BLE001 —— 有的模块要外部依赖，跳过
            continue
        for v in vars(mod).values():
            if isinstance(v, ToolSpec):
                seen.setdefault(v.name, set()).add(m.name)
    for s in cr.SPECS:
        assert seen[s.name] == {"creations"}, f"{s.name} 和 {seen[s.name]} 撞名"


def test_平常聊天不背这五个_她开口要才挂():
    names = [s.name for s in cr.SPECS] + ["send_meme"]
    offer, _ = toolpacks.select(names, texts=["今天好累"])
    assert offer == {"send_meme"}
    offer, m = toolpacks.select(names, texts=["帮我做个五子棋"])
    assert "studio" in m and {s.name for s in cr.SPECS} <= offer
