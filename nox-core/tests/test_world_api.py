"""World 页数据源的测试。四段都注入依赖，一行网络都不打。

盯的是同一件事的四个面：**分不出来的时候要说分不出来**。
"""

import pytest

from api import world as W


class TestParseDeviceList:
    def test_认得出清单(self):
        text = "家里可控设备：\n- 主卧 风扇 → fan.dmaker_p5c\n- 客厅 电视 → media_player.xiaomi"
        assert W.parse_device_list(text) == [
            ("主卧 风扇", "fan.dmaker_p5c"),
            ("客厅 电视", "media_player.xiaomi"),
        ]

    def test_箭头写成ASCII也认(self):
        # 这行文案是给模型看的，改它的人不会想到界面在读
        assert W.parse_device_list("- 蒸蛋器 -> switch.cuco") == [("蒸蛋器", "switch.cuco")]

    def test_解析不出来的行跳过不猜(self):
        text = "家里可控设备：\n- 这行没有箭头\n- 主卧 空调 → climate.lumi\n- 坏的 → 没有点号"
        assert W.parse_device_list(text) == [("主卧 空调", "climate.lumi")]

    def test_空文本不炸(self):
        assert W.parse_device_list("") == []
        assert W.parse_device_list(None) == []


class TestRoomOf:
    @pytest.mark.parametrize("name,room,label", [
        ("主卧 床头灯", "主卧", "床头灯"),
        ("电竞房 空调", "电竞房", "空调"),
        ("主卧 风扇摆风", "主卧", "风扇摆风"),
        ("蒸蛋器", "厨房", "蒸蛋器"),            # 名字里没房间，_ROOM_HINT 补
        ("蒸蛋器 自动断电", "厨房", "自动断电"),  # 有空格也走提示表——按前缀推会得到「房间=蒸蛋器」
    ])
    def test_从名字推房间(self, name, room, label):
        assert W._room_of(name) == (room, label)

    def test_猜不出房间就留空_不许瞎猜(self):
        """🔴 猜错了灯会亮在错的屋里。"""
        # 猜错了灯会亮在错的屋里 —— 宁可让它落到「其他」
        assert W._room_of("新买的加湿器") == (None, "新买的加湿器")


class TestCollectHome:
    LIST = "- 主卧 床头灯 → light.bed\n- 客厅 空调 → climate.living"

    def test_正常拿到状态和属性(self):
        def state_of(eid):
            return {
                "light.bed": {"state": "on", "attributes": {"brightness": 128, "friendly_name": "x"}},
                "climate.living": {"state": "cool", "attributes": {"current_temperature": 26, "temperature": 24}},
            }[eid]

        out = W.collect_home(lambda: self.LIST, state_of)
        assert out["ok"] is True
        bed = next(d for d in out["devices"] if d["entity_id"] == "light.bed")
        assert bed["room"] == "主卧" and bed["label"] == "床头灯"
        assert bed["state"] == "on" and bed["attrs"]["brightness"] == 128
        # 白名单之外的属性不透传
        assert "friendly_name" not in bed["attrs"]

    def test_单个设备查不到_不拖垮整张图(self):
        def state_of(eid):
            if eid == "light.bed":
                raise TimeoutError("超时")
            return {"state": "cool", "attributes": {}}

        out = W.collect_home(lambda: self.LIST, state_of)
        assert out["ok"] is True                       # 整体还在
        bed = next(d for d in out["devices"] if d["entity_id"] == "light.bed")
        assert bed["ok"] is False and bed["state"] is None   # 这一个如实标查不到
        assert len(out["devices"]) == 2

    def test_离线的state原样透传_不美化(self):
        """🔴 不许把 unavailable 翻译成「关着」。"""
        # HA 在设备离线时会保留断电前的最后状态，我们能拿到的诚实信号
        # 只有 unavailable。不许把它翻译成「关着」
        out = W.collect_home(lambda: "- 蒸蛋器 → switch.cuco",
                             lambda e: {"state": "unavailable", "attributes": {}})
        assert out["devices"][0]["state"] == "unavailable"

    def test_清单拿不到就说拿不到(self):
        def boom():
            raise ConnectionError("ha-mcp 连不上")

        out = W.collect_home(boom, lambda e: {})
        assert out["ok"] is False and out["devices"] == []

    def test_清单是空的不等于家里没设备(self):
        out = W.collect_home(lambda: "家里可控设备：", lambda e: {})
        assert out["ok"] is False


class TestCollectSky:
    def test_实时加预报都拿到(self):
        def get(path):
            if path.endswith("now"):
                return {"now": {"text": "多云", "icon": "101", "temp": "26", "feelsLike": "28", "humidity": "70"}}
            return {"daily": [{"tempMax": "30", "tempMin": "22", "sunrise": "06:12", "sunset": "18:40"}]}

        sky = W.collect_sky(get)
        assert sky["ok"] and sky["icon"] == "101" and sky["temp"] == 26.0
        assert sky["temp_max"] == 30.0 and sky["sunrise"] == "06:12"
        assert sky["has_forecast"] is True

    def test_预报挂了但实时还在_照样能画天空(self):
        def get(path):
            if path.endswith("now"):
                return {"now": {"text": "小雨", "icon": "305", "temp": "18"}}
            raise RuntimeError("3d 挂了")

        sky = W.collect_sky(get)
        assert sky["ok"] is True and sky["icon"] == "305"
        assert sky["has_forecast"] is False

    def test_实时挂了就说挂了_不许编个晴天(self):
        def get(path):
            raise ConnectionError("和风超时")

        assert W.collect_sky(get)["ok"] is False


class TestCollectPresence:
    def test_在家(self):
        p = W.collect_presence(lambda e: {"state": "home", "last_changed": "2026-09-02T10:00:00+08:00"})
        assert p["ok"] and p["home"] is True and p["since"]

    def test_出门(self):
        p = W.collect_presence(lambda e: {"state": "not_home"})
        assert p["ok"] and p["home"] is False

    @pytest.mark.parametrize("st", ["unknown", "unavailable", "", None])
    def test_HA说不知道就是不知道_不当成出门(self, st):
        # 判成 not_home 的话，房子会因为一次 HA 抖动整个暗下去
        assert W.collect_presence(lambda e: {"state": st})["ok"] is False


class TestCollectActivity:
    def test_连着且有窗口(self):
        a = W.collect_activity(("Delta Force", 900), link_ready=True)
        assert a["ok"] and a["app"] == "Delta Force" and a["seconds"] == 900

    def test_连着但没窗口_是不知道_不是她不忙(self):
        """🔴 「不知道」和「她不忙」是两件事。"""
        a = W.collect_activity(None, link_ready=True)
        assert a["ok"] is True and a["app"] is None

    def test_够不到电脑时不许断言原因(self):
        """🔴 说错原因比说不知道更糟（2026-08-31）。"""
        a = W.collect_activity(None, link_ready=False)
        assert a["ok"] is False
        # 2026-08-31：他说「电脑没开机」而她电脑开着，真正的问题是网关没起。
        # 文案必须承认分不出来，且不许指挥她去开机
        assert "分不出" in a["error"]
        assert "等你开机" not in a["error"]
