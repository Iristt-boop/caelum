"""房间工具的测试。用假 client，不打网络。

盯的是三件事：**失败要炸不要装**、**他只能动他自己**、**看不懂就别转述**。
"""

import json

import pytest

from tools import room as R


class FakeResult:
    def __init__(self, ok=True, text="", error=None):
        self.ok, self.text, self.error = ok, text, error


class FakeClient:
    def __init__(self, result=None):
        self.result = result or FakeResult(text="{}")
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args or {}))
        return self.result


class TestSpecs:
    def test_四个工具都在(self):
        assert [s.name for s in R.SPECS] == [
            "room_get_state", "room_move", "room_use_furniture", "room_stop",
        ]

    def test_移动的目标只有三种_并且只能到她身边不能动她(self):
        target = R.MOVE_SPEC.parameters["properties"]["target"]["properties"]
        assert target["kind"]["enum"] == ["tile", "furniture", "character"]
        # 🔴 character_id 只能是 owner —— 那是「到她身边去」，
        # 不是「替她走」。房间那边也焊死了 MCP 只写 companion
        assert target["character_id"]["enum"] == ["owner"]

    def test_工具描述里写明了我动不了她(self):
        assert "只能移动我自己" in R.MOVE_SPEC.description

    def test_家具再多也只有一个入口(self):
        """⚠️ 上游的规矩：家具数量增加不应增加 MCP 工具数量。
        每件家具一个工具 = 白烧缓存前缀。"""
        assert sum(1 for s in R.SPECS if "furniture" in s.name) == 1


class TestHandlers:
    def test_参数原样传给房间(self):
        c = FakeClient(FakeResult(text='{"ok":true}'))
        h = R.make_handlers(c)
        h["room_move"](target={"kind": "character", "character_id": "owner"})
        assert c.calls == [("room_move", {"target": {"kind": "character", "character_id": "owner"}})]

    def test_停下来不带参数也能调(self):
        c = FakeClient(FakeResult(text="{}"))
        R.make_handlers(c)["room_stop"]()
        assert c.calls[0][0] == "room_stop"

    def test_够不到房间要炸_不许当成正常返回(self):
        """🔴 把失败转成一句「没做成」的正常返回，正是会让他编造的那个错误
        （ha.py 那条：他会说「灯已经开了」而灯根本没开）。"""
        c = FakeClient(FakeResult(ok=False, error="Connection refused"))
        with pytest.raises(RuntimeError) as e:
            R.make_handlers(c)["room_get_state"]()
        assert "够不到房间" in str(e.value)

    def test_够不到的时候不断言原因(self):
        """🔴 2026-08-31 的教训：说错原因比说不知道更糟。"""
        c = FakeClient(FakeResult(ok=False, error="timeout"))
        with pytest.raises(RuntimeError) as e:
            R.make_handlers(c)["room_stop"]()
        msg = str(e.value)
        assert "分不出" in msg
        # 不许指挥她去开机 / 断言是哪一种故障
        assert "没开机" not in msg


class TestHumanize:
    def test_挑出两个人在做什么_原文照样附上(self):
        """⚠️ 这份数据是 2026-09-02 从真房间拷回来的形状，不是编的。

        第一版测试按我猜的 `{"companion": {...}}` 写，真跑一次才发现
        `characters` 是**数组**——代码和测试当时一起错，测试全绿而实际
        一个人也挑不出来（安静地走了兜底分支）。
        """
        raw = json.dumps({
            "ok": True, "scene_id": "home", "revision": 58,
            "characters": [
                {"id": "owner", "position": {"x": 35, "y": 59},
                 "activity": "read", "furniture": "bookcase"},
                {"id": "companion", "position": {"x": 35, "y": 58},
                 "activity": "idle", "furniture": None},
            ],
        }, ensure_ascii=False)
        out = R._humanize("room_get_state", raw)
        assert "我：idle·(35,58)" in out        # 没在用家具就报坐标
        assert "糖糖：read·bookcase" in out      # 在用家具就报家具
        assert raw in out                        # 挑漏了他还能自己看原文

    def test_characters_是字典时也认(self):
        """上游哪天改成按 id 索引也不该当场瞎掉。"""
        raw = json.dumps({"characters": {"companion": {"activity": "sit", "furniture": "sofa"}}},
                         ensure_ascii=False)
        assert "我：sit·sofa" in R._humanize("room_get_state", raw)

    def test_房间说做不了就照实说(self):
        out = R._humanize("room_move", '{"error":"目标不可达"}')
        assert "做不了" in out and "目标不可达" in out

    def test_看不懂就把原文交上去_不装作看懂(self):
        """⚠️ JSON 变了形状时宁可交原文，也不要一本正经地转述一个
        不存在的状态。"""
        assert R._humanize("room_get_state", "not json at all") == "not json at all"
        assert R._humanize("room_get_state", "[1,2,3]") == "[1,2,3]"

    def test_空回复不炸(self):
        assert R._humanize("room_stop", "") == ""
