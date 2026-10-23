"""夜里认出来的跟进 → 他自己跟进的目标（10-23）。"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import goals_followups


class OB:
    def __init__(self, day, items, ok=True):
        self.day, self.items, self.ok = day, items, ok

    def nightly_follow_ups(self, day=""):
        if not self.ok:
            return NS(ok=False, text="", error="OB 睡了")
        return NS(ok=True, text=json.dumps({"day": self.day, "items": self.items}, ensure_ascii=False), error=None)


class Bridge:
    def __init__(self, titles):
        self.titles, self.posts = list(titles), []

    def get(self, path, params=None):
        return NS(ok=True, data={"items": [{"title": t} for t in self.titles]}, error=None)

    def post(self, path, body=None):
        self.posts.append(body)
        self.titles.append(body["title"])
        return NS(ok=True, data={"ok": True, "id": "x"}, error=None)


def test_建还没有的_有了的不建_同晚只处理一次(tmp_path):
    ob = OB("2026-10-23", [{"title": "姨妈推迟", "why": "42 天", "every_days": 2},
                           {"title": "腰伤恢复", "why": "刚缓过来", "every_days": 3},
                           {"title": "腰伤恢复", "why": "重复的"}])
    b = Bridge(["姨妈推迟了"])                       # 已经有（互相包含就算）
    assert goals_followups.sync(ob, b, str(tmp_path)) == ["腰伤恢复"]
    assert b.posts == [{"title": "腰伤恢复", "category": "其他", "origin": "刚缓过来", "owner": "nox", "every_days": 3}]
    b.titles.remove("腰伤恢复")                      # 她删了
    assert goals_followups.sync(ob, b, str(tmp_path)) == [], "同一晚的结果不再处理 —— 不然删了又冒出来"
    ob.day = "2026-10-24"
    assert goals_followups.sync(ob, b, str(tmp_path)) == ["腰伤恢复"], "第二天夜里又提了，才算新的"


def test_OB读不到不建不记(tmp_path):
    b = Bridge([])
    assert goals_followups.sync(OB("2026-10-23", [{"title": "x"}], ok=False), b, str(tmp_path)) == []
    assert not (tmp_path / goals_followups.STATE_FILE).exists()
