"""目标里「到点我去做点什么，再来告诉你」（10-23）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from attention.goal_routines import GoalRoutines, due, why_of
from attention.wakeup import WakeBook
from attention.care.signal import TASK
from tools import goals as goal_tools

CST = timezone(timedelta(hours=8))
G = {"id": "g1", "title": "AI 新闻", "status": "active",
     "routine": {"at": "09:00", "weekdays": "1,2,3,4,5", "task": "搜今天的 AI 新闻，挑一两条讲给她"}}


class Store:
    def __init__(self):
        self.d = {}

    def get_source_state(self, k):
        return self.d.get(k)

    def set_source_state(self, k, v):
        self.d[k] = v


class Bridge:
    def __init__(self, items, ok=True):
        self.items, self.ok, self.posts, self.patches = items, ok, [], []

    def get(self, path, params=None):
        return NS(ok=self.ok, data={"items": self.items}, error=None if self.ok else "HTTP 502: x")

    def post(self, path, body=None):
        self.posts.append(body)
        if body.get("routine", {}).get("at") == "9点":
            return NS(ok=False, data=None, error="HTTP 400: routine 要 …")
        return NS(ok=True, data={"ok": True, "id": "g9"}, error=None)

    def patch(self, path, body=None):
        self.patches.append((path, body))
        return NS(ok=True, data={"ok": True}, error=None)


def at(h, m, day=23):   # 2026-10-23 是周五
    return datetime(2026, 10, day, h, m, tzinfo=CST)


def test_到点_那几天_今天没做过_不补太晚的():
    assert not due(G, at(8, 59), {})
    assert due(G, at(9, 0), {})
    assert due(G, at(10, 30), {})
    assert not due(G, at(11, 30), {}), "过了两小时就不补了"
    assert not due(G, at(9, 30), {"g1": "2026-10-23"}), "今天做过了"
    assert not due(G, at(9, 30, day=24), {}), "周六不在那几天里"
    assert not due({**G, "status": "paused"}, at(9, 30), {})
    assert not due({**G, "routine": None}, at(9, 30), {})


def test_到点放一张任务纸条_马上醒_不和别的合并_一天一次():
    book, store = WakeBook(), Store()
    other = book.add("s1", "她睡了，半小时后把空调关掉", 30, now=at(8, 50), kind=TASK)
    r = GoalRoutines(Bridge([G]), store)
    made = r.poll(at(9, 5).astimezone(timezone.utc), book, "s1")
    assert len(made) == 1 and made[0].kind == TASK and made[0].wake_at <= at(9, 5)
    assert made[0] in book.due(at(9, 5)) and other.alive and other.why.startswith("她睡了"), "别盖掉她别的纸条"
    assert "搜今天的 AI 新闻" in made[0].why and "告诉她" in made[0].why
    assert r.poll(at(9, 20), book, "s1") == [], "一天一次"
    assert store.d["goal_routines"] == {"g1": "2026-10-23"}


def test_读不到bridge_没会话_都跳过不炸():
    assert GoalRoutines(Bridge([G], ok=False), Store()).poll(at(9, 5), WakeBook(), "s1") == []
    assert GoalRoutines(Bridge([G]), Store()).poll(at(9, 5), WakeBook(), None) == []


def test_他能立带到点的事的目标_格式不对照实说():
    b = Bridge([])
    h = goal_tools.make_handlers(b)
    out = h["goal_create"]({"title": "AI 新闻", "origin": "每天 9 点给我推新闻",
                            "routine": {"at": "09:00", "weekdays": "1,2,3,4,5,6,7", "task": "搜今天的 AI 新闻挑一两条讲给她"}})
    assert b.posts[0]["routine"]["at"] == "09:00" and "每到 09:00 你会去做" in out
    bad = h["goal_create"]({"title": "x新闻", "origin": "y", "routine": {"at": "9点", "task": "搜"}})
    assert "格式不对" in bad


def test_他能改和清掉到点的事():
    b = Bridge([{"id": "g1", "title": "AI 新闻"}])
    h = goal_tools.make_handlers(b)
    assert "每到 21:00" in h["goal_update"]({"goal": "新闻", "action": "routine", "routine": {"at": "21:00", "task": "看看今晚的新闻"}})
    assert b.patches[-1] == ("/api/goals/g1", {"routine": {"at": "21:00", "task": "看看今晚的新闻"}})
    assert "清掉了" in h["goal_update"]({"goal": "新闻", "action": "routine", "routine": {}})
    assert b.patches[-1] == ("/api/goals/g1", {"routine": None})


def test_纸条上写着目标名():
    assert why_of(G).startswith("【目标·AI 新闻】搜今天的 AI 新闻")
