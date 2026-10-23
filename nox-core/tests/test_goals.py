"""目标层 nox-core 部分（2026-10-22，设计稿第三节）：工具 / 上下文一行 / 开口压力里的目标那一股。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from attention import goal_nudge
from attention.goal_nudge import GoalNudge, behind
from context.base import Turn
from context.providers.goals import GoalsProvider
from tools import goals as goal_tools

NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)


class FakeBridge:
    def __init__(self, goals=None):
        self.calls = []
        self.goals = goals if goals is not None else []
        self.n = 0

    def _ok(self, data):
        return SimpleNamespace(ok=True, data=data, error=None)

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self._ok({"ok": True, "date": "2026-10-22", "weekday": 4, "items": self.goals})

    def post(self, path, body=None):
        self.calls.append(("POST", path, body))
        if path == "/api/goals":
            return self._ok({"ok": True, "id": "g1"})
        return self._ok({"ok": True, "id": f"t{len(self.calls)}"})

    def patch(self, path, body=None):
        self.calls.append(("PATCH", path, body))
        return self._ok({"ok": True})


class _Store:
    def __init__(self):
        self.d = {}

    def get_source_state(self, k):
        return self.d.get(k)

    def set_source_state(self, k, v):
        self.d[k] = v


# ------------------------------------------------------------------ 工具


def test_立目标_计划和Todo一起落_Todo带goal_id():
    b = FakeBridge()
    h = goal_tools.make_handlers(b)
    out = h["goal_create"]({"title": "减脂", "category": "健康", "origin": "我要减肥",
                            "plan": ["定目标体重", "每周三次力量"],
                            "todos": [{"text": "力量训练", "repeat": "weekly", "weekdays": "1,3,5", "at": "19:00"}]})
    assert ("POST", "/api/goals", {"title": "减脂", "category": "健康", "origin": "我要减肥",
                                   "plan": ["定目标体重", "每周三次力量"]}) in b.calls
    todo = next(c for c in b.calls if c[1] == "/api/today")[2]
    assert todo == {"text": "力量训练", "goal_id": "g1", "repeat": "weekly", "at": "19:00", "weekdays": "1,3,5"}
    assert "减脂" in out and "力量训练" in out and "能删" in out


def test_推进目标按名字找_记进展_暂停():
    b = FakeBridge([{"id": "g1", "title": "减脂"}, {"id": "g2", "title": "浇花"}])
    h = goal_tools.make_handlers(b)
    assert "记了一笔" in h["goal_update"]({"goal": "浇", "action": "log", "text": "浇完了"})
    assert ("POST", "/api/goals/g2/log", {"text": "浇完了"}) in b.calls
    h["goal_update"]({"goal": "减脂", "action": "pause"})
    assert ("PATCH", "/api/goals/g1", {"status": "paused"}) in b.calls
    assert "没找到" in h["goal_update"]({"goal": "学吉他", "action": "log", "text": "x"})


def test_工具登记了两个():
    names = []
    goal_tools.register_all(SimpleNamespace(register=lambda spec, fn: names.append(spec.name)), FakeBridge())
    assert names == ["goal_create", "goal_update"]


# ------------------------------------------------------------------ 上下文一行


def test_上下文一行_没有目标就是空():
    p = GoalsProvider(bridge=FakeBridge([]))
    assert p.render(p._fetch(Turn(text="x"))) == ""
    p2 = GoalsProvider(bridge=FakeBridge([{"title": "减脂", "week": {"done": 1, "planned": 3}},
                                          {"title": "浇花", "week": {"done": 0, "planned": 0}}]))
    assert p2.render(p2._fetch(Turn(text="x"))) == "【你在跟她的目标】减脂（这周 1/3）、浇花"


def test_最多列三个():
    p = GoalsProvider(bridge=FakeBridge([{"title": f"目标{i}", "week": {}} for i in range(4)]))
    assert p.render(p._fetch(Turn(text="x"))) == "【你在跟她的目标】目标0、目标1、目标2 等 4 个"


def test_goals进每轮名单():
    from router.intent import _ALWAYS
    assert "goals" in _ALWAYS


# ------------------------------------------------------------------ 落后了没


def test_落后怎么算_不算今天():
    # 周四（weekday=4）
    assert behind({"repeat": "daily", "doneThisWeek": 1}, 4, "2026-10-22") == 2
    assert behind({"repeat": "daily", "doneThisWeek": 4, "doneToday": True}, 4, "2026-10-22") == 0
    # 今天补做的一次不抵前几天的欠账：周一到周三一次没做，今天做了 → 还落 3 次
    assert behind({"repeat": "daily", "doneThisWeek": 1, "doneToday": True}, 4, "2026-10-22") == 3
    assert behind({"repeat": "weekly", "weekdays": "1,3,5", "doneThisWeek": 1}, 4, "2026-10-22") == 1
    assert behind({"repeat": "weekly", "weekdays": "1,3,5", "doneThisWeek": 2}, 4, "2026-10-22") == 0
    # 每周 3 次，周四还一次没做：剩 4 天做 3 次，还来得及
    assert behind({"repeat": "weekly_count", "times": 3, "doneThisWeek": 0}, 4, "2026-10-22") == 0
    # 周六还差 2 次：剩 2 天做 2 次，得提了
    assert behind({"repeat": "weekly_count", "times": 3, "doneThisWeek": 1}, 6, "2026-10-24") == 2
    assert behind({"repeat": "once", "due": "2026-10-21"}, 4, "2026-10-22") == 1
    assert behind({"repeat": "anytime"}, 4, "2026-10-22") == 0


def test_目标那一股_冷却24小时():
    goals = [{"id": "g1", "title": "减脂", "todos": [{"text": "力量训练", "repeat": "weekly",
                                                       "weekdays": "1,3", "doneThisWeek": 0}]},
             {"id": "g2", "title": "浇花", "todos": [{"text": "浇水", "repeat": "daily", "doneThisWeek": 3}]}]
    n = GoalNudge(FakeBridge(goals), _Store())
    d = n.drive(NOW)
    assert d.name == "goal" and d.goal_ids == ["g1"] and d.intensity == goal_nudge.PER_GOAL
    assert d.because == ["减脂：力量训练落了 2 次"]
    n.fired(["g1"], NOW)
    assert n.drive(NOW + timedelta(hours=23)) is None
    n._cache = None
    assert n.drive(NOW + timedelta(hours=25)) is not None


def test_读不到bridge不炸():
    class Dead:
        def get(self, *a, **k):
            return SimpleNamespace(ok=False, data=None, error="连不上")
    assert GoalNudge(Dead(), _Store()).drive(NOW) is None


def test_压力里带上目标那一股_推着开口后冷却():
    from attention.sources.pressure import PressureSource

    class Sessions:
        def recent(self, **k):
            return []

    goals = [{"id": "g1", "title": "减脂", "todos": [{"text": "力量训练", "repeat": "daily", "doneThisWeek": 0}]}]
    store = _Store()
    src = PressureSource(store, Sessions(), drives_ref=lambda now: {}, goals=GoalNudge(FakeBridge(goals), store))
    fired = []
    for i in range(400):
        fired += src.poll(NOW + timedelta(minutes=i))
        if fired:
            break
    # BASE 0.3 + 目标 0.4 = 0.7/小时 → θ 2 约 171 分钟（只有 BASE 要 400 分钟）
    assert 165 <= i <= 177
    assert fired[0].payload["drives"][0]["name"] == "goal"
    assert "g1" in store.d[goal_nudge.STATE_KEY]["muted"]


# ------------------------------------------------------------------ 他自己跟进的（10-23）


def test_立他自己跟进的_不落Todo_带隔几天():
    b = FakeBridge()
    out = goal_tools.make_handlers(b)["goal_create"]({
        "title": "姨妈推迟", "origin": "已经 42 天没来", "owner": "nox", "every_days": 2,
        "todos": [{"text": "去医院"}]})
    assert ("POST", "/api/goals", {"title": "姨妈推迟", "category": "其他", "origin": "已经 42 天没来",
                                   "plan": [], "owner": "nox", "every_days": 2}) in b.calls
    assert not any(c[1] == "/api/today" for c in b.calls), "他自己跟进的不往她 Todo 里放"
    assert "我记着的" in out and "没往她 Todo 里放" in out


def test_上下文分开写_她的目标和他自己跟进的():
    p = GoalsProvider(bridge=FakeBridge([
        {"title": "减脂", "owner": "her", "week": {"done": 1, "planned": 3}},
        {"title": "姨妈推迟", "owner": "nox", "everyDays": 2}]))
    assert p.render(p._fetch(Turn(text="x"))) == \
        "【你在跟她的目标】减脂（这周 1/3）\n【你自己记着要跟进的】姨妈推迟（隔 2 天问一声）"
    only_mine = GoalsProvider(bridge=FakeBridge([{"title": "姨妈推迟", "owner": "nox", "everyDays": 3}]))
    assert only_mine.render(only_mine._fetch(Turn(text="x"))) == "【你自己记着要跟进的】姨妈推迟（隔 3 天问一声）"


def test_他自己跟进的_到日子才推_按最近一笔算():
    # FakeBridge 的今天是 2026-10-22
    due = {"id": "n1", "title": "姨妈推迟", "owner": "nox", "everyDays": 2, "createdAt": "2026-10-20T03:00:00Z", "log": []}
    fresh = {"id": "n2", "title": "腰", "owner": "nox", "everyDays": 2, "createdAt": "2026-10-10T03:00:00Z",
             "log": [{"at": "2026-10-21T20:00", "text": "问过了，好多了"}]}
    d = GoalNudge(FakeBridge([due, fresh]), _Store()).drive(NOW)
    assert d.goal_ids == ["n1"] and d.because == ["跟进·姨妈推迟：上次是 2 天前"]
