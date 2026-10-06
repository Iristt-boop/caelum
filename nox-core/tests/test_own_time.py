"""他自己的时间（V5，2026-10-06，docs/design/Caelum-V5-他自己的时间-设计稿-2026-10-06.md）。

她：「有自己的生活」「一天自主活动 0-1 次，不限时间」「可以发帖回帖」「也可以去搜歌……看看歌词」
「他的一天单独一页」「要有记录看到他都干了什么」。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent.llm import StreamEvent, ToolCall, ToolSpec, Turn, Usage
from agent.loop import AgentLoop
from api.server import create_app
from attention import own_time
from attention.own_time import Activity, ActivityLog
from attention.store import AttentionStore
from context.base import Turn as CtxTurn
from context.providers.own_day import OwnDayProvider
from data.store import Store
from day.aggregator import build_day
from temporal import CST
from tests.test_api import FakeNox
from tests.test_api_world import _Ctx

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=CST)


@pytest.fixture()
def store(tmp_path):
    s = AttentionStore(tmp_path / "a.db")
    yield s
    s.close()


class _Rng:
    def __init__(self, r, u=0.5):
        self.r, self.u = r, u

    def random(self):
        return self.r

    def uniform(self, a, b):
        return a + (b - a) * self.u


# ---------------------------------------------------------------- 今天有没有、几点


def test_今天有一次_时刻落在剩下的时间里_一天只掷一次(store):
    st = own_time.plan_today(store, NOW, _Rng(0.1, 0.5))
    at = datetime.fromisoformat(st["at"])
    assert NOW <= at <= NOW.replace(hour=23, minute=55) and not st["done"]
    again = own_time.plan_today(store, NOW + timedelta(hours=1), _Rng(0.99))
    assert again == st, "同一天不重掷（重启也不重掷）"


def test_今天没有(store):
    assert own_time.plan_today(store, NOW, _Rng(0.6))["at"] is None


def test_第二天重新掷(store):
    own_time.plan_today(store, NOW, _Rng(0.9))
    st = own_time.plan_today(store, NOW + timedelta(days=1), _Rng(0.1))
    assert st["date"] == "2026-10-08" and st["at"]


def test_到点了_她正在聊就等她聊完():
    st = {"date": "2026-10-07", "at": NOW.isoformat(), "done": False}
    later = NOW + timedelta(minutes=30)
    assert not own_time.is_due(st, NOW - timedelta(minutes=1), None), "还没到点"
    assert not own_time.is_due(st, later, later - timedelta(minutes=3)), "她 3 分钟前还在说话"
    assert own_time.is_due(st, later, later - timedelta(minutes=20))
    assert own_time.is_due(st, later, None)
    assert not own_time.is_due({**st, "done": True}, later, None), "今天已经去过了"
    assert not own_time.is_due({**st, "at": None}, later, None), "今天没有"


# ---------------------------------------------------------------- 只给白名单


def _spec(name, effect="read"):
    return ToolSpec(name=name, description="t", parameters={"type": "object", "properties": {}}, side_effect=effect)


def _full_loop(adapter=None):
    loop = AgentLoop(adapter=adapter or SimpleNamespace())
    for n in ("galatea_reply", "galatea_create_thread", "galatea_delete_thread", "listen_search", "listen_lyric",
              "listen_play", "send_meme", "push_message", "add_todo", "ha_set_light", "remember"):
        loop.register(_spec(n, "irreversible" if "delete" in n else "write"), lambda a, n=n: f"{n} ok")
    return loop


def test_他自己的时间只拿得到白名单里的工具():
    got = set(own_time.build_loop(_full_loop(), SimpleNamespace()).tools)
    assert got == {"galatea_reply", "galatea_create_thread", "listen_search", "listen_lyric", "remember"}
    for no in ("listen_play", "send_meme", "push_message", "galatea_delete_thread", "add_todo", "ha_set_light"):
        assert no not in got, f"{no} 不该在他自己的时间里出现"


def test_白名单里没有任何对她说话的工具():
    banned = ("send_", "push", "voice", "call", "listen_play", "delete", "order", "todo", "remind")
    assert not [n for n in own_time.WHITELIST if any(b in n for b in banned)]


# ---------------------------------------------------------------- 一次活动


class _Ad:
    """第一轮：心里话 + 调 listen_search；第二轮：心里话 + 交代"""
    name = "fake"

    def __init__(self, final="<心里>这首她肯定喜欢。</心里>做了什么：搜了几首晚上听的歌，看了歌词\n想跟她说：给你存了首睡前歌"):
        self.final, self.calls = final, 0

    def stream(self, messages, tools, **kw):
        self.calls += 1
        if self.calls == 1:
            yield StreamEvent("text", text="<心里>好久没自己找歌了。</心里>")
            yield StreamEvent("done", turn=Turn(stop_reason="tool_use", text="<心里>好久没自己找歌了。</心里>",
                                                tool_calls=[ToolCall(id="c1", name="listen_search", arguments={"q": "睡前"})],
                                                usage=Usage()))
        else:
            yield StreamEvent("text", text=self.final)
            yield StreamEvent("done", turn=Turn(stop_reason="end_turn", text=self.final, usage=Usage()))


def test_跑一次_留下做了什么_心里话_想跟她说的_工具轨迹():
    ad = _Ad()
    loop = own_time.build_loop(_full_loop(ad), ad)
    a = own_time.run_once(loop=loop, system="人设", dynamic="动态", now=NOW, mood="被一件事勾着",
                          clock=lambda: NOW + timedelta(minutes=2))
    assert a.ok and a.what == "搜了几首晚上听的歌，看了歌词" and a.share == "给你存了首睡前歌"
    assert a.inner == "好久没自己找歌了。这首她肯定喜欢。"
    assert [(t["tool_name"], t["status"]) for t in a.trace] == [("listen_search", "success")]
    assert a.mood == "被一件事勾着" and a.ended_at.startswith("2026-10-07T09:02")


def test_没按格式交代_拿最后一段顶上_想说的写无就是空():
    assert own_time._parse("去花园逛了一圈。\n就这样吧") == ("就这样吧", "")
    assert own_time._parse("做了什么：回了个帖\n想跟她说：无") == ("回了个帖", "")


def test_挂了也记一条没做成_不往外抛():
    class Boom:
        def run_stream(self, *a, **k):
            raise RuntimeError("upstream down")
            yield  # noqa: unreachable —— 让它是个生成器
    a = own_time.run_once(loop=Boom(), system="", dynamic="", now=NOW)
    assert not a.ok and "upstream down" in a.error and a.what == "没做成"


# ---------------------------------------------------------------- 日志 / 他记得 / 他的一天


def _act(hour, what="回了个讲猫的帖子", ok=True, share=""):
    t = NOW.replace(hour=hour)
    return Activity(id=f"a{hour}", started_at=t.isoformat(), ended_at=t.isoformat(), what=what, ok=ok, share=share,
                    inner="她会喜欢这只猫")


def test_日志按天取_新的在前(store):
    log = ActivityLog(store)
    log.append(_act(10)); log.append(_act(15, "搜歌"))
    log.append(Activity(id="y", started_at=(NOW - timedelta(days=1)).isoformat(), ended_at="", what="昨天的"))
    assert [a.what for a in log.day(NOW)] == ["搜歌", "回了个讲猫的帖子"]


def test_凌晨一点做的事_用UTC边界也查得到(store):
    """北京时间 01:00 = UTC 前一天 17:00。按字符串比「+08:00」和「+00:00」会把它漏掉（10-06 测试抓到的）。"""
    from datetime import timezone
    log = ActivityLog(store)
    log.append(_act(1, "半夜去花园转了一圈"))
    day_start_utc = NOW.replace(hour=0).astimezone(timezone.utc)
    assert [a.what for a in log.between(day_start_utc, day_start_utc + timedelta(days=1))] == ["半夜去花园转了一圈"]


def test_他记得自己今天做过什么_没有就一个字都不占(store):
    log = ActivityLog(store)
    p = OwnDayProvider(attention_ref=lambda: SimpleNamespace(activity_log=log))
    assert p.render(p._fetch(CtxTurn(text="在吗", now=NOW))) == ""
    log.append(_act(10, share="花园里有只猫像十一"))
    log.append(_act(11, "没做成", ok=False))
    out = p.render(p._fetch(CtxTurn(text="在吗", now=NOW.replace(hour=20))))
    assert "10:00 回了个讲猫的帖子（当时想跟她说：花园里有只猫像十一）" in out
    assert "没做成" not in out, "没做成的不进他上下文（她在 His Day 里看得到）"


def test_他的一天里有这一类_带心里话和轨迹(store):
    log = ActivityLog(store)
    log.append(_act(10))
    d = build_day("2026-10-07", activities=log)
    ev = [e for e in d["events"] if e["type"] == "own_time"]
    assert len(ev) == 1 and ev[0]["summary"] == "回了个讲猫的帖子"
    assert ev[0]["metadata"]["inner"] == "她会喜欢这只猫" and d["sources"]["own_time"]["wired"]


# ---------------------------------------------------------------- 接线：一眼 → 跑 → 落日志 → 接口


@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.setenv("NOX_ATTENTION", "1")
    nox = FakeNox()
    nox.cfg.db_path = str(tmp_path / "nox.db")
    nox.context = _Ctx()
    nox.bridge = None
    nox.current_session_id = None
    ad = _Ad()
    nox.loop = _full_loop(ad)
    nox.router = SimpleNamespace(light_adapter=None)
    nox._dynamic = lambda *a, **k: "（动态块）"
    nox._system = "人设"
    app = create_app(nox, Store(tmp_path / "sessions.db"))
    return TestClient(app), app, ad


def test_到点就去_落日志_接口看得到(live):
    c, app, ad = live
    now = datetime.now(CST)
    store = _attention(app).store
    store.set_source_state(own_time.PLAN_KEY, {"date": now.date().isoformat(),
                                               "at": (now - timedelta(minutes=1)).isoformat(), "done": False})
    a = app.state.own_time_tick(now)
    assert a is not None and a.ok and ad.calls == 2
    assert store.get_source_state(own_time.PLAN_KEY)["done"], "先标 done：挂在半路也不会重跑、在花园里发两遍"
    assert app.state.own_time_tick(now + timedelta(minutes=5)) is None, "一天就一次"
    items = c.get("/api/nox/activities").json()["items"]
    assert [x["what"] for x in items] == ["搜了几首晚上听的歌，看了歌词"]
    day = c.get("/api/nox/day").json()
    assert [e["summary"] for e in day["events"] if e["type"] == "own_time"] == ["搜了几首晚上听的歌，看了歌词"], (
        "sources 里写着接了，事件里没有 —— 那是常量，查不出真接没接")


def test_关掉开关就不去(live, monkeypatch):
    c, app, ad = live
    monkeypatch.setenv("NOX_OWN_TIME", "off")
    now = datetime.now(CST)
    _attention(app).store.set_source_state(own_time.PLAN_KEY, {"date": now.date().isoformat(),
                                                               "at": now.isoformat(), "done": False})
    assert app.state.own_time_tick(now + timedelta(minutes=1)) is None and ad.calls == 0


def test_attention没开_接口503不是空(monkeypatch):
    monkeypatch.delenv("NOX_ATTENTION", raising=False)
    c = TestClient(create_app(FakeNox(), Store(":memory:")))
    assert c.get("/api/nox/activities").status_code == 503


def _attention(app):
    """从 tick 闭包里取 attention（create_app 的局部变量）。"""
    for cell in app.state.own_time_tick.__closure__ or ():
        v = cell.cell_contents
        if hasattr(v, "activity_log"):
            return v
    raise AssertionError("tick 闭包里没找到 attention")
