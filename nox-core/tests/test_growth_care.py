"""Growth Loop 第 2 期 shadow：P2 念叨耐受度 + P3-健康 说法偏好（2026-10-19，她定做法 A）。

钉住的：
  · 回看：只挑像健康念叨的；主动开口拆成几条也算一句；她的下一句只认 60 分钟内、不是系统提示的
  · 归类：没回就是没理（不管模型说什么）；她回了就不算没理；认不出的丢
  · 记账：幂等；她在睡时没理不算（G8）；调挂了这一天下次再试
  · P2 只往下：再接得住也不超过 1.0，再烦也不低于 FLOOR；证据不够不生效
  · P3：说法差得够多才说一句；shadow 期什么都不改
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agent.llm import Turn, Usage
from growth import care_patterns as cp
from growth import care_review as cr

CN = timezone(timedelta(hours=8))
SID = "a" * 32


def m(role, text, at, sid=SID):
    return {"session_id": sid, "role": role, "text": text, "created_at": at.astimezone(timezone.utc).isoformat()}


T = datetime(2026, 10, 18, 22, 0, tzinfo=CN)


# ---------------------------------------------------------------- 回看：挑候选


def test_主动开口拆成几条也算一句_系统提示排在哪都认得出是主动():
    rows = [m("assistant", "昨晚那觉九小时五十八分。", T),
            m("user", "（系统提示：不是她在跟你说话。", T),
            m("assistant", "今晚十一点半就躺下。", T),
            m("user", "知道啦", T + timedelta(minutes=5))]
    got = cr.collect(rows)
    assert len(got) == 1
    assert got[0]["proactive"] is True
    assert "九小时五十八分" in got[0]["his"] and "十一点半" in got[0]["his"]
    assert got[0]["hers"] == "知道啦"


def test_不像念叨的不挑():
    rows = [m("user", "今天看了部片", T), m("assistant", "哪部？讲什么的", T + timedelta(seconds=5))]
    assert cr.collect(rows) == []


def test_她的下一句只认60分钟内_不是系统提示的():
    rows = [m("assistant", "早点睡。", T),
            m("user", "（系统提示：现在是 22:30", T + timedelta(minutes=30)),
            m("assistant", "睡了没", T + timedelta(minutes=30)),
            m("user", "睡了", T + timedelta(minutes=70))]
    got = cr.collect(rows)
    assert got[0]["hers"] is None, "70 分钟后才回，不算接这句"


# ---------------------------------------------------------------- 归类


class _Ad:
    def __init__(self, items=None, raw=None, boom=False):
        self.items, self.raw, self.boom, self.calls = items, raw, boom, 0

    def complete(self, messages, tools=None, **kw):
        self.calls += 1
        if self.boom:
            raise RuntimeError("上游挂了")
        text = self.raw if self.raw is not None else json.dumps({"items": self.items}, ensure_ascii=False)
        return Turn(stop_reason="end_turn", text=text, usage=Usage())


def _c(hers="嗯", at=T):
    return {"at": at.astimezone(timezone.utc).isoformat(), "sid": SID, "his": "早点睡", "hers": hers, "proactive": True}


def test_归类_不是念叨的丢_认不出的丢():
    ad = _Ad(items=[{"i": 0, "nag": False}, {"i": 1, "nag": True, "topic": "睡眠", "style": "瞎说", "reception": "接住"},
                    {"i": 2, "nag": True, "topic": "睡眠", "style": "生活", "reception": "接住"}])
    got = cr.classify(ad, [_c(), _c(), _c()])
    assert [g["style"] for g in got] == ["生活"]


def test_红线_没回就是没理_回了就不算没理():
    ad = _Ad(items=[{"i": 0, "nag": True, "topic": "睡眠", "style": "数据", "reception": "接住"},
                    {"i": 1, "nag": True, "topic": "睡眠", "style": "数据", "reception": "没理"}])
    got = cr.classify(ad, [_c(hers=None), _c(hers="好")])
    assert [g["reception"] for g in got] == ["没理", "带过"]


def test_调挂了_或回的不是JSON_这一天不算做过():
    assert cr.classify(_Ad(boom=True), [_c()]) is None
    assert cr.classify(_Ad(raw="我觉得都挺好"), [_c()]) is None
    assert cr.classify(_Ad(boom=True), []) == [], "没候选就不调"


# ---------------------------------------------------------------- 记账


class _Store:
    def __init__(self):
        self.rows, self.state = [], {}

    def append_experience(self, **kw):
        self.rows.append({**kw, "outcome": kw["outcome"]})
        return len(self.rows)

    def list_experiences(self, kind=None):
        return [r for r in self.rows if kind is None or r["kind"] == kind]

    def get_source_state(self, k):
        return self.state.get(k)

    def set_source_state(self, k, v):
        self.state[k] = v


class _Sessions:
    def __init__(self, rows):
        self.rows, self.asked = rows, []

    def messages_between(self, a, b, clean_only=True):
        self.asked.append((a, b))
        return [r for r in self.rows if a <= r["created_at"] < b]


def test_记账_幂等_她在睡时没理不算():
    night = datetime(2026, 10, 18, 3, 0, tzinfo=CN)
    rows = [m("assistant", "早点睡", T), m("user", "嗯嗯", T + timedelta(minutes=2)),
            m("assistant", "怎么又没睡", night)]
    ad = _Ad(items=[{"i": 0, "nag": True, "topic": "睡眠", "style": "担心", "reception": "没理"},
                    {"i": 1, "nag": True, "topic": "睡眠", "style": "担心", "reception": "带过"}])
    store, ses = _Store(), _Sessions(rows)
    day = datetime(2026, 10, 18, tzinfo=CN)
    assert cr.review_day(store, ses, ad, day, day + timedelta(days=1)) == 1
    r = store.rows[0]
    assert r["kind"] == cr.KIND_HEALTH_CARE and r["context"]["reception"] == "带过" and r["outcome"] == 0.5
    assert r["context"]["hers"] == "嗯嗯" and r["source_event"].startswith("health_care:")
    assert cr.review_day(store, ses, ad, day, day + timedelta(days=1)) == 0, "第二遍不重复记"


def test_tick_从最早的没看过的日子做起_每轮最多几天_挂了不记做过():
    store, ses = _Store(), _Sessions([])
    now = datetime(2026, 10, 19, 10, 0, tzinfo=CN)
    done = cr.tick(store, ses, _Ad(items=[]), now)
    assert done == ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
    assert len(cr.tick(store, ses, _Ad(items=[]), now)) == cr.MAX_DAYS_PER_TICK
    # 有候选但调挂了 → 这一天不记做过，下一轮从它接着来
    ses.rows = [m("assistant", "早点睡", datetime(2026, 10, 13, 22, tzinfo=CN))]
    assert cr.tick(store, ses, _Ad(boom=True), now) == []
    assert "2026-10-13" not in store.state[cr.STATE_KEY]["done"]
    assert cr.tick(store, ses, _Ad(items=[]), now)[0] == "2026-10-13"


# ---------------------------------------------------------------- P2：只往下


NOW = datetime(2026, 10, 19, 12, tzinfo=CN)


def ev(topic, style, rec, day, n=1):
    at = datetime(2026, 10, day, 22, tzinfo=CN)
    return [{"at": at.astimezone(timezone.utc).isoformat(), "kind": cr.KIND_HEALTH_CARE, "outcome": cr.RECEPTION_SCORE[rec],
             "context": {"topic": topic, "style": style, "reception": rec}} for _ in range(n)]


def many(topic, style, rec, days=range(12, 18), per_day=2):
    return [r for d in days for r in ev(topic, style, rec, d, per_day)]


def test_红线_她烦了_权重往下_但不低于下限():
    got = cp.tolerance(many("睡眠", "数据", "烦了"), NOW)["睡眠"]
    assert got.effective and cp.FLOOR <= got.weight < 1.0


def test_红线_她再接得住_权重也不超过1():
    got = cp.tolerance(many("睡眠", "生活", "接住"), NOW)["睡眠"]
    assert got.effective and got.weight == 1.0


def test_证据不够不生效():
    got = cp.tolerance(ev("饮食", "担心", "烦了", 18, 3), NOW)["饮食"]
    assert got.effective is False


# ---------------------------------------------------------------- P3-健康：哪种说法接得住


def test_说法差得够多_才说一句():
    rows = many("睡眠", "生活", "接住") + many("睡眠", "数据", "烦了")
    hint = cp.style_hint(cp.styles(rows, NOW), "睡眠")
    assert hint and "落进她的生活里" in hint and "把数字报给她" in hint
    same = many("睡眠", "生活", "接住") + many("睡眠", "数据", "接住")
    assert cp.style_hint(cp.styles(same, NOW), "睡眠") is None, "两种说法她都接得住，就没什么好说的"
    assert cp.style_hint(cp.styles(many("睡眠", "生活", "接住"), NOW), "睡眠") is None, "只有一种说法学成不说"


def test_shadow日志_说得出本来会怎样():
    rows = many("睡眠", "生活", "接住") + many("睡眠", "数据", "烦了") + ev("饮食", "担心", "带过", 18)
    lines = cp.shadow_lines(rows, NOW)
    assert any(l.startswith("P2 shadow：睡眠 生效") and "关心权重本来会是" in l for l in lines)
    assert any(l.startswith("P2 shadow：饮食 候选") for l in lines)
    assert any(l.startswith("P3 shadow：睡眠") for l in lines)


# ---------------------------------------------------------------- 接口（从使用方）


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from api.server import create_app
    from data.store import Store
    from tests.test_api import FakeNox
    from tests.test_api_world import _Ctx

    monkeypatch.setenv("NOX_ATTENTION", "1")
    nox = FakeNox()
    nox.cfg.db_path = str(tmp_path / "nox.db")
    nox.context = _Ctx()
    nox.bridge = None
    app = create_app(nox, Store(tmp_path / "sessions.db"))
    return TestClient(app), app


def test_成长日志接口带上P2和P3(client):
    c, app = client
    from tests.test_own_time import _attention
    store = _attention(app).store
    for r in many("睡眠", "生活", "接住") + many("睡眠", "数据", "烦了"):
        store.append_experience(at=r["at"], kind=r["kind"], context=r["context"], outcome=r["outcome"],
                                attributed_to="user", recorded_at=r["at"])
    d = c.get("/api/nox/growth").json()
    assert d["stage"] == "p1-live" and d["care_rows"] == 24
    sleep = next(p for p in d["p2"] if p["topic"] == "睡眠")
    assert sleep["effective"] and sleep["weight"] <= 1.0
    assert {s["style"] for s in d["p3_health"]} == {"生活", "数据"}
    assert d["p3_hints"] and "睡眠" in d["p3_hints"][0]


def test_下限是一道真夹子_不靠公式碰巧(monkeypatch):
    """按现在的公式 0.5 + 耐受度 天然 ≥ 0.5；下限要在公式以后被改时也守得住。"""
    monkeypatch.setattr(cp, "FLOOR", 0.9)
    got = cp.tolerance(many("睡眠", "数据", "烦了"), NOW)["睡眠"]
    assert got.weight == 0.9


def test_尺子_她要他算的热量不算念叨_他自己主动报的算():
    """10-19 验尺子：她发了吃的、他算热量 → 被模型判成「饮食 / 报数据」。那是她要的，不能学成扣分。"""
    rows = [m("user", "[她发来 1 张图片：一盆香锅]", T),
            m("assistant", "给你算清楚：香锅 220 卡/100g，这盆全吃完 1300 卡。", T + timedelta(minutes=1)),
            m("user", "（系统提示：现在是 23:30", T + timedelta(minutes=90)),
            m("assistant", "还剩七百五十八卡，晚饭别又拿酸奶糊过去。", T + timedelta(minutes=90))]
    got = cr.collect(rows)
    assert [g["his"][:4] for g in got] == ["还剩七百"]


def test_尺子_聊天里他说完她没接_不算没理():
    """「我也爱你，老婆。睡吧，我守着」—— 她说完晚安去睡了，不是冷落。"""
    ad = _Ad(items=[{"i": 0, "nag": True, "topic": "睡眠", "style": "担心", "reception": "没理"}])
    chat = {**_c(hers=None), "proactive": False}
    assert cr.classify(ad, [chat]) == []
