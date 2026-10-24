"""小课堂并入目标层（2026-10-24，《Caelum-小课堂并入目标层-设计稿》第一步：后端）。

钉住她定的几件事：每天的学习目标才出卡、没有就不出；交给开口压力；讲过的记着、要点不重复；
讲没讲看他回的话里有没有要点；21 点后没讲的作废不补。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from attention import lesson_drive as ld
from attention.lesson_drive import LessonDrive
from context.providers.goals import GoalsProvider
from lessons import generate as gen
from lessons.store import LessonStore
from tools import goals as goal_tools

UTC = timezone.utc
#: 她那边 2026-10-24 06:00（UTC+8）
GEN = datetime(2026, 10, 23, 22, 0, tzinfo=UTC)
#: 她那边 14:00
WIN = datetime(2026, 10, 24, 6, 0, tzinfo=UTC)
TODAY = "2026-10-24"
GOAL = {"id": "g1", "title": "英语", "origin": "我想把英语捡起来"}


def _card(key="used to", title="以前常常", body=None):
    return {"title": title, "key": key, "body": body or f"说过去的习惯用 {key}，比如 I {key} live in Tokyo。",
            "hook": "你小时候常做什么？", "level": "知道 used to 了", "next": ["be used to", "would"]}


class Utility:
    def __init__(self, *cards):
        self.cards = list(cards)
        self.calls = 0
        self.prompts = []

    def complete(self, messages, tools, **kw):
        self.calls += 1
        self.prompts.append(messages[0].text)
        c = self.cards.pop(0) if self.cards else None
        return SimpleNamespace(text=json.dumps(c, ensure_ascii=False) if isinstance(c, dict) else (c or ""))


class Bridge:
    def __init__(self, goals=None, ok=True):
        self.goals = [GOAL] if goals is None else goals
        self.ok = ok

    def get(self, path, params=None):
        if not self.ok:
            return SimpleNamespace(ok=False, data=None, error="连不上")
        return SimpleNamespace(ok=True, data={"items": self.goals}, error=None)

    def post(self, path, body=None):
        return SimpleNamespace(ok=True, data={"ok": True, "id": "g1"}, error=None)


class Sessions:
    def __init__(self):
        self.msgs = []

    def say(self, role, text, at):
        self.msgs.append({"session_id": "s", "role": role, "text": text, "created_at": at.isoformat()})

    def messages_between(self, start, end, clean_only=True):
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
        return [m for m in self.msgs if a <= datetime.fromisoformat(m["created_at"]) <= b]

    # PressureSource 要的
    def recent(self, **k):
        return []


@pytest.fixture
def store(tmp_path):
    s = LessonStore(tmp_path / "lessons.db")
    yield s
    s.close()


def _line(store, level="四级过了"):
    store.upsert_line("g1", "英语", cadence="daily", level=level)


# ------------------------------------------------------------------ 生成


def test_parse_要点要英文_要在正文里_不许撞最近讲过的():
    good = json.dumps(_card())
    assert gen.parse(good, [])["key"] == "used to"
    assert gen.parse(good, ["Used To"]) is None                              # 撞了（不分大小写）
    assert gen.parse(json.dumps(_card(body="说过去的习惯")), []) is None       # 正文里没有要点
    assert gen.parse(json.dumps({**_card(), "key": "以前"}), []) is None      # 要点不是英文
    assert gen.parse("不是 JSON", []) is None


def test_没有每天的学习目标就不出卡(store):
    u = Utility(_card())
    d = LessonDrive(store, Bridge(), Sessions(), u)
    d.drive(GEN)
    assert u.calls == 0 and store.card_on("g1", TODAY) is None


def test_六点备一张_一天只一张(store):
    _line(store)
    u = Utility(_card(), _card(key="would"))
    d = LessonDrive(store, Bridge(), Sessions(), u)
    d.drive(GEN - timedelta(minutes=1))          # 05:59 还不备
    assert u.calls == 0
    d.drive(GEN)
    d.drive(GEN + timedelta(hours=2))
    assert u.calls == 1
    assert store.card_on("g1", TODAY)["key"] == "used to"
    assert "四级过了" in u.prompts[0]


def test_目标停了就不备(store):
    _line(store)
    u = Utility(_card())
    LessonDrive(store, Bridge(goals=[]), Sessions(), u).drive(GEN)
    assert u.calls == 0


def test_生成失败30分钟后再试_一天最多三次(store):
    _line(store)
    u = Utility("坏的", "坏的", "坏的", _card())
    d = LessonDrive(store, Bridge(), Sessions(), u)
    for m in range(0, 200):
        d.drive(GEN + timedelta(minutes=m))
    assert u.calls == ld.MAX_TRIES
    assert store.card_on("g1", TODAY) is None


def test_讲过的要点会带进提示词_撞了不用(store):
    _line(store)
    store.add_card("g1", "2026-10-23", _card(), GEN - timedelta(days=1))
    store.mark_told(store.card_on("g1", "2026-10-23")["id"], GEN - timedelta(hours=20))
    u = Utility(_card())                         # 又写了一张 used to
    LessonDrive(store, Bridge(), Sessions(), u).drive(GEN)
    assert "used to" in u.prompts[0]
    assert store.card_on("g1", TODAY) is None


# ------------------------------------------------------------------ 开口压力那一股


def _ready(store, sessions=None):
    _line(store)
    d = LessonDrive(store, Bridge(), sessions or Sessions(), Utility(_card()))
    d.drive(GEN)
    return d


def test_十四点到二十一点才给压力_带要点(store):
    d = _ready(store)
    assert d.drive(WIN - timedelta(minutes=1)) is None
    got = d.drive(WIN)
    assert got.name == "lesson" and got.intensity == ld.INTENSITY
    assert "used to" in got.because[0]


def test_推过一次没讲_60分钟后再推(store):
    d = _ready(store)
    d.fired(WIN)
    assert d.drive(WIN + timedelta(minutes=59)) is None
    assert d.drive(WIN + timedelta(minutes=60)) is not None


def test_他回的话里有要点就算讲了_课程线往前挪(store):
    s = Sessions()
    d = _ready(store, s)
    s.say("user", "（系统提示：小课堂 used to）", WIN)          # 开场白提示词不算他讲
    d.drive(WIN + timedelta(minutes=1))
    assert store.pending(TODAY)
    s.say("assistant", "乖，今天教你一个 Used To ……", WIN + timedelta(minutes=2))
    assert d.drive(WIN + timedelta(minutes=3)) is None
    c = store.card_on("g1", TODAY)
    assert c["status"] == "told"
    line = store.get_line("g1")
    assert line["level"] == "知道 used to 了" and line["next"] == ["be used to", "would"]


def test_讲完30分钟内她的第一句记下来_跳过系统提示(store):
    s = Sessions()
    d = _ready(store, s)
    s.say("assistant", "used to 是……", WIN)
    d.drive(WIN + timedelta(minutes=1))
    s.say("user", "（系统提示：别的事）", WIN + timedelta(minutes=2))
    s.say("user", "I used to play piano!", WIN + timedelta(minutes=5))
    s.say("user", "第二句", WIN + timedelta(minutes=6))
    d.drive(WIN + timedelta(minutes=7))
    assert store.card_on("g1", TODAY)["reply"] == "I used to play piano!"


def test_30分钟没回记空_不再看(store):
    s = Sessions()
    d = _ready(store, s)
    s.say("assistant", "used to 是……", WIN)
    d.drive(WIN + timedelta(minutes=29))
    assert store.awaiting_reply()
    d.drive(WIN + timedelta(minutes=30))
    assert not store.awaiting_reply() and store.card_on("g1", TODAY)["reply"] == ""
    s.say("user", "晚来的一句", WIN + timedelta(minutes=31))
    d.drive(WIN + timedelta(minutes=32))
    assert store.card_on("g1", TODAY)["reply"] == ""


def test_二十一点没讲就作废_不补_也不算讲过(store):
    d = _ready(store)
    assert d.drive(WIN + timedelta(hours=7)) is None       # 21:00
    assert store.card_on("g1", TODAY)["status"] == "expired"
    assert store.recent("g1") == []


def test_压力里带上小课堂那一股_推着开口后记推过(store):
    from attention.sources.pressure import PressureSource

    class _S:
        def __init__(self):
            self.d = {}

        def get_source_state(self, k):
            return self.d.get(k)

        def set_source_state(self, k, v):
            self.d[k] = v

    d = _ready(store)
    src = PressureSource(_S(), Sessions(), drives_ref=lambda now: {}, lessons=d)
    fired = []
    for i in range(400):
        fired += src.poll(WIN + timedelta(minutes=i))
        if fired:
            break
    # BASE 0.3 + 小课堂 0.5 = 0.8/小时 → θ 2 约 150 分钟
    assert 145 <= i <= 155
    assert fired[0].payload["drives"][0]["name"] == "lesson"
    assert d._offered_at == WIN + timedelta(minutes=i)


# ------------------------------------------------------------------ 工具 / 上下文


def test_立目标带小课堂_改水平_停掉(store):
    h = goal_tools.make_handlers(Bridge(), store)
    out = h["goal_create"]({"title": "英语", "origin": "我想把英语捡起来",
                            "lesson": {"cadence": "daily", "level": "四级过了"}})
    assert "小课堂配好了" in out
    assert store.get_line("g1")["level"] == "四级过了"
    h["goal_update"]({"goal": "英语", "action": "lesson", "lesson": {"level": "能看美剧"}})
    assert store.get_line("g1")["level"] == "能看美剧"
    h["goal_update"]({"goal": "英语", "action": "lesson", "lesson": {"stop": True}})
    assert store.lines("daily") == []


def test_小课堂没开_工具照实说():
    out = goal_tools.make_handlers(Bridge(), None)["goal_create"](
        {"title": "英语", "origin": "x", "lesson": {"cadence": "daily"}})
    assert "没开" in out


def test_上下文带今天没讲的卡_讲了马上消失(store):
    from temporal import today
    _line(store)
    d = today().isoformat()
    store.add_card("g1", d, _card(), GEN)
    p = GoalsProvider(bridge=Bridge(goals=[]), lessons=store)
    text = p.render(p.get_state(None))
    assert "今天的小课堂" in text and "used to" in text
    store.mark_told(store.card_on("g1", d)["id"], GEN)
    assert "今天的小课堂" not in p.render(p.get_state(None))   # 列表缓存 30 分钟，卡每轮现读


# ------------------------------------------------------------------ Goals 页的只读接口


def test_接口只给讲过的卡_新的在前_带她回没回(tmp_path):
    from fastapi.testclient import TestClient

    import lessons
    from api.server import create_app
    from data.store import Store
    from tests.test_api import FakeNox

    nox = FakeNox()
    nox.cfg.db_path = str(tmp_path / "nox.db")
    st = lessons.store_for(nox.cfg.db_path)
    st.upsert_line("g1", "英语", cadence="daily", level="四级")
    for d, key in (("2026-10-22", "would"), ("2026-10-23", "used to"), ("2026-10-24", "account for")):
        st.add_card("g1", d, _card(key=key), GEN)
    a = st.card_on("g1", "2026-10-22")["id"]
    b = st.card_on("g1", "2026-10-23")["id"]
    st.mark_told(a, GEN)
    st.mark_told(b, GEN)
    st.set_reply(b, "I used to swim")
    st.expire_through("2026-10-23")        # 24 号那张还没讲 → 不给（别先剧透）
    body = TestClient(create_app(nox, Store(tmp_path / "sessions.db"))).get("/api/nox/lessons").json()
    line = body["lines"][0]
    assert line["title"] == "英语"
    assert [c["key"] for c in line["cards"]] == ["used to", "would"]
    assert [c["replied"] for c in line["cards"]] == [True, False]
