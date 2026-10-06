"""关系状态：账本 + 识别 + 接口 + 进他上下文（2026-10-06，《Caelum-关系状态-设计稿》）。

她：「四类都要，聊天卡片和列表都要。当时的确认放聊天，后续我可以自己在列表查看。」
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent.llm import Turn, Usage
from api.server import create_app
from attention import relation_detect
from attention.relation_book import RelationBook
from attention.relationship import RelationshipState
from attention.store import AttentionStore
from context.base import Turn as CtxTurn
from context.providers.relation import RelationProvider
from data.store import Store
from temporal import now as temporal_now
from tests.test_api import FakeNox
from tests.test_api_world import _Ctx, _Loop

NOW = temporal_now()


@pytest.fixture()
def book(tmp_path):
    s = AttentionStore(tmp_path / "a.db")
    yield RelationBook(s)
    s.close()


def _p(book, kind="avoid", text="她不喜欢我老问她吃没吃饭", topic="饮食", now=NOW, **kw):
    return book.propose(kind=kind, text=text, topic=topic, quote="别老问我吃没吃饭", now=now, **kw)


# ---------------------------------------------------------------- 账本


def test_提议等她点头_Keep了才生效(book):
    it = _p(book)
    assert it.status == "pending" and book.active(NOW) == []
    assert book.decide(it.id, "confirm", NOW).status == "active"
    assert [x.text for x in book.active(NOW, "avoid")] == ["她不喜欢我老问她吃没吃饭"]
    assert book.avoid_topics(NOW) == {"饮食"}


def test_同一件事不重复提(book):
    assert _p(book) is not None
    assert _p(book, text="她嫌我总问吃饭") is None, "同 kind 同话题已经在等她确认了"
    assert _p(book, kind="care") is not None, "换一类不算重复"


def test_她说不对的_30天内不再提(book):
    it = _p(book)
    book.decide(it.id, "reject", NOW)
    assert _p(book, now=NOW + timedelta(days=29)) is None
    assert _p(book, now=NOW + timedelta(days=31)) is not None


def test_一天最多两张卡_气氛不算卡(book):
    assert _p(book, topic="饮食") and _p(book, topic="睡眠", text="b")
    assert _p(book, topic="工作", text="c") is None
    assert _p(book, kind="vibe", topic="", text="刚闹了点别扭") is not None


def test_气氛直接生效_新的顶掉旧的_三天淡掉(book):
    a = _p(book, kind="vibe", topic="", text="刚闹了点别扭")
    assert a.status == "active"
    _p(book, kind="vibe", topic="", text="刚和好", now=NOW + timedelta(hours=2))
    assert [x.text for x in book.active(NOW + timedelta(hours=3), "vibe")] == ["刚和好"]
    assert book.active(NOW + timedelta(days=4), "vibe") == []


def test_她能做的三件事_走不通的回None(book):
    it = _p(book)
    assert book.decide(it.id, "remove", NOW).status == "removed"
    assert book.decide(it.id, "confirm", NOW) is None, "删了的不能再 Keep"
    assert book.decide("nope", "confirm", NOW) is None
    assert book.decide(_p(book, topic="睡眠", text="x").id, "explode", NOW) is None


def test_给他看的那段_没有就空串(book):
    assert book.describe(NOW) == ""
    book.decide(_p(book, kind="pact", topic="", text="吵架不过夜").id, "confirm", NOW)
    _p(book, kind="care", topic="睡眠", text="她要我盯着她十二点前睡")          # 没点头的不给
    d = book.describe(NOW)
    assert "约定：吵架不过夜" in d.replace("你们的约定", "约定") and "十二点" not in d


# ---------------------------------------------------------------- 评估器现读


def test_评估器现读账本_别问归零_上心拉满(book):
    rel = RelationshipState(book=book)
    assert rel.care_weight("学习") == 0.5
    book.decide(_p(book).id, "confirm", NOW)
    book.decide(_p(book, kind="care", topic="学习", text="她要我盯着她背单词").id, "confirm", NOW)
    assert rel.is_avoided("饮食") and rel.care_weight("饮食") == 0.0
    assert rel.care_weight("学习") == 1.0


def test_账本读挂了_按写死的种子算_不抛():
    class Broken:
        def avoid_topics(self, now):
            raise RuntimeError("db gone")
        care_topics = avoid_topics
    rel = RelationshipState(book=Broken())
    assert rel.care_weight("饮食") == 1.0 and not rel.is_avoided("饮食")


# ---------------------------------------------------------------- 识别


class _Ad:
    def __init__(self, reply=None, exc=None):
        self.reply, self.exc, self.seen = reply, exc, []

    def complete(self, messages, tools=None, **kw):
        self.seen.append((kw.get("system"), messages[0].text))
        if self.exc:
            raise self.exc
        return Turn(stop_reason="end_turn", text=self.reply, usage=Usage())


def _j(**d):
    return json.dumps(d, ensure_ascii=False)


def test_识别_认出一条():
    ad = _Ad(_j(kind="avoid", text="她不喜欢我老问吃饭", topic="饮食", quote="别老问我吃没吃饭", confidence=0.9))
    got = relation_detect.detect(ad, "别老问我吃没吃饭好吗", "好好好", ["吵架不过夜"])
    assert got == {"kind": "avoid", "text": "她不喜欢我老问吃饭", "topic": "饮食", "quote": "别老问我吃没吃饭"}
    assert "吵架不过夜" in ad.seen[0][0], "已经记着的要喂给它，别重复提"


@pytest.mark.parametrize("reply", [
    _j(kind="none", text="", confidence=0.9),
    _j(kind="avoid", text="她不喜欢我老问吃饭", topic="饮食", confidence=0.6),   # 不够确定
    _j(kind="friend", text="x", confidence=0.99),                                # 不认识的类
    "我觉得没有",                                                                 # 不是 JSON
])
def test_识别_没有或不够确定_都是None(reply):
    assert relation_detect.detect(_Ad(reply), "今天好累", "抱抱") is None


def test_识别_话题不在词表里就留空_调挂了不抛():
    got = relation_detect.detect(_Ad(_j(kind="pact", text="吵架不过夜", topic="吵架", confidence=0.8)), "以后吵架不许过夜", "好")
    assert got["topic"] == "" and got["quote"] == "以后吵架不许过夜", "没给原话就用她这句"
    assert relation_detect.detect(_Ad(exc=TimeoutError("slow")), "x", "y") is None


# ---------------------------------------------------------------- 进他上下文


def test_他每轮看得到_没有就一个字都不占(book):
    p = RelationProvider(attention_ref=lambda: SimpleNamespace(relation_book=book))
    assert p.render(p._fetch(CtxTurn(text="在吗"))) == ""
    book.decide(_p(book, kind="pact", topic="", text="吵架不过夜").id, "confirm", NOW)
    out = p.render(p._fetch(CtxTurn(text="在吗")))
    assert "【你们之间的事】" in out and "吵架不过夜" in out
    assert RelationProvider(attention_ref=lambda: None).render(
        RelationProvider(attention_ref=lambda: None)._fetch(CtxTurn(text="x"))) == ""


def test_每轮名单里有它():
    from router.intent import classify_context
    assert "relation" in classify_context("在吗", light=True)


# ---------------------------------------------------------------- 一整条：她说 → 卡片 → Keep → 生效


class _Bridge:
    def __init__(self):
        self.posts = []

    def post(self, path, body=None):
        self.posts.append((path, body))
        return SimpleNamespace(ok=True, data={"ok": True})

    def get(self, path, params=None):
        return SimpleNamespace(ok=True, data={})


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setenv("NOX_ATTENTION", "1")
    nox = FakeNox(text="好，不问了")
    nox.cfg.db_path = str(tmp_path / "nox.db")
    nox.context = _Ctx()
    nox.bridge = _Bridge()
    nox.current_session_id = None
    nox.loop = _Loop()
    reply = _j(kind="avoid", text="她不喜欢我老问她吃没吃饭", topic="饮食", quote="别老问我吃没吃饭", confidence=0.9)
    nox.router = SimpleNamespace(light_adapter=_Ad(reply))
    return TestClient(create_app(nox, Store(tmp_path / "sessions.db"))), nox


def _wait(pred, s=3.0):
    end = time.time() + s
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_她说了_出卡片_Keep之后列表里是生效的(live):
    c, nox = live
    assert c.post("/chat", json={"session_id": "s-relation", "text": "别老问我吃没吃饭"}).status_code == 200
    assert _wait(lambda: nox.bridge.posts), "没出确认卡"
    path, body = nox.bridge.posts[0]
    assert path == "/api/relation/card" and body["session_id"] == "s-relation"
    iid = body["item"]["id"]
    items = c.get("/api/nox/relation").json()["items"]
    assert [(x["id"], x["status"]) for x in items] == [(iid, "pending")]

    assert c.post(f"/api/nox/relation/{iid}/confirm").json()["item"]["status"] == "active"
    assert c.get(f"/api/nox/relation/{iid}").json()["item"]["status"] == "active"
    assert c.post(f"/api/nox/relation/{iid}/reject").status_code == 409, "已经 Keep 了，不能再说不对"
    assert c.post(f"/api/nox/relation/{iid}/fly").status_code == 400
    assert c.get("/api/nox/relation/nope").status_code == 404


def test_测试会话不识别(live):
    c, nox = live
    c.post("/chat", json={"session_id": "test-relation", "text": "别老问我吃没吃饭"})
    time.sleep(0.4)
    assert nox.bridge.posts == [] and nox.router.light_adapter.seen == []


def test_attention没开_列表是503不是空(monkeypatch, tmp_path):
    monkeypatch.delenv("NOX_ATTENTION", raising=False)
    c = TestClient(create_app(FakeNox(), Store(":memory:")))
    assert c.get("/api/nox/relation").status_code == 503
