"""「关于她」档案（10-22，设计稿 2.1½ / 2.4）：他的 profile_update 工具 + 她页面用的两个端点。

OB 用假的 —— 只认 profile_read / profile_edit 两个调用，行为照 OB 的 profile_store（一字不差才改得动）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from tools import profile as profile_tools
from tests.test_api import FakeNox, client_for

SECTIONS = [{"name": "身体与作息", "lines": ["不能碰咖啡因", "怕冷"]},
            {"name": "我们之间", "lines": ["约定：每条回复都带语音", "别问：别一到晚上就催她睡"]}]


class FakeOB:
    def __init__(self):
        self.edits = []

    def _read(self):
        return SimpleNamespace(ok=True, text=json.dumps({"sections": SECTIONS, "history": []}, ensure_ascii=False))

    def _edit(self, op, **kw):
        self.edits.append({"op": op, **kw})
        if kw.get("text") == "重复的":
            return SimpleNamespace(ok=True, text=json.dumps({"ok": False, "error": "这一行已经在了"}, ensure_ascii=False))
        return SimpleNamespace(ok=True, text=json.dumps(
            {"ok": True, "op": op, "section": kw.get("section") or "身体与作息", "old": kw.get("old", ""),
             "new": kw.get("text", "")}, ensure_ascii=False))

    def profile_read(self):
        return self._read()

    def profile_edit(self, op, **kw):
        return self._edit(op, **kw)

    async def aprofile_read(self):
        return self._read()

    async def aprofile_edit(self, op, **kw):
        return self._edit(op, **kw)


# ------------------------------------------------------------------ 他的工具


def test_加一行_来源记成nox():
    ob = FakeOB()
    out = profile_tools.make_handler(ob)({"op": "add", "section": "喜欢", "text": "夏天和晚霞", "why": "她说的"})
    assert ob.edits == [{"op": "add", "section": "喜欢", "text": "夏天和晚霞", "old": "", "why": "她说的", "source": "nox"}]
    assert "夏天和晚霞" in out


def test_改一行_给几个字就够_工具找到那整行再改():
    ob = FakeOB()
    profile_tools.make_handler(ob)({"op": "update", "old": "冷", "text": "怕冷，睡觉裹被子"})
    assert ob.edits[0]["old"] == "怕冷", "交给 OB 的必须是那一整行（OB 一字不差才改）"


def test_没有这行就说没有_不乱改():
    ob = FakeOB()
    out = profile_tools.make_handler(ob)({"op": "retire", "old": "热量"})
    assert "没有含「热量」" in out and ob.edits == []


def test_好几行对得上_列候选不改():
    ob = FakeOB()
    ob._read = lambda: SimpleNamespace(ok=True, text=json.dumps(
        {"sections": [{"name": "我们之间", "lines": ["约定：A", "约定：B"]}]}, ensure_ascii=False))
    out = profile_tools.make_handler(ob)({"op": "retire", "old": "约定"})
    assert "好几行" in out and "- 约定：A" in out and "- 约定：B" in out and ob.edits == []


def test_OB拒了就照实说():
    out = profile_tools.make_handler(FakeOB())({"op": "add", "section": "喜欢", "text": "重复的"})
    assert out == "没改：这一行已经在了"


# ------------------------------------------------------------------ 她的页面


def test_页面读档案():
    nox = FakeNox()
    nox.ob = FakeOB()
    r = client_for(nox).get("/api/nox/profile")
    assert r.status_code == 200 and r.json()["sections"][0]["lines"] == ["不能碰咖啡因", "怕冷"]


def test_页面改档案_来源记成her_改不了回409带原因():
    nox = FakeNox()
    nox.ob = FakeOB()
    c = client_for(nox)
    r = c.post("/api/nox/profile/edit", json={"op": "retire", "old": "怕冷"})
    assert r.status_code == 200 and nox.ob.edits[-1]["source"] == "her" and nox.ob.edits[-1]["old"] == "怕冷"
    r = c.post("/api/nox/profile/edit", json={"op": "add", "section": "喜欢", "text": "重复的"})
    assert r.status_code == 409 and r.json()["detail"] == "这一行已经在了"


def test_没接记忆_503():
    nox = FakeNox()
    nox.ob = None
    assert client_for(nox).get("/api/nox/profile").status_code == 503
