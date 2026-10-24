"""行动闸门（embodied）的测试。

两个真实事故钉在这里，谁改坏谁负责：

① 2026-09 深夜：8 月的晚上，他「想让她舒服点」开了电热毯 ——
   关心逻辑对，环境事实不在场。
② 2026-09 早上：「我醒了」→ 他开蒸蛋器说做早餐 —— 里面没水没蛋。

## 这些测试能挡什么（对应派单变异表）

- deny_when 被去掉 → 「32°C 开电热毯必须被拒」红
- unknown 检查被去掉 → 「没证据开蒸蛋器必须被拦」红
- 域白名单被去掉 → 「homeassistant.turn_off 必须被拒」红（排期 3.5 判据）
- 静态分……不对，是替代方向被丢 → 「DENY 必须带替代」红
- 她的话语证据被无视 → 「放好了就直通」红

## 钉的原则

- off 一律放行（收手不闯祸）
- env_temp unknown → 温度规则不触发（诚实：不假装知道）
- 不在模型里的设备 → not_in_model 降权限，不是放行
- quiet_hours 只警告 —— 半夜她醒了开灯是正当的
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embodied.device_model import DEVICE_MODEL  # noqa: E402
from embodied.validator import gate, world_snapshot  # noqa: E402

BLANKET = "switch.xiaomi_mj1_00f2_electric_blanket"
STEAMER = "switch.cuco_v3_7680_switch"
LIGHT = "light.yeelink_mbulb3_0170_light"


def _world(temp=None, presence="home"):
    return {"env_temp": temp, "presence": presence}


# ---------------------------------------------------------------- 事故①


def test_32度开电热毯_拒且带温度和替代():
    g = gate(BLANKET, "on", _world(temp=32), user_text="睡吧")
    assert not g.allow
    assert g.reason == "environment_conflict"
    assert "32" in g.human, "拒绝的话里必须带当时的事实（温度）"
    assert g.alternative == "cooling_possible", "必须给替代方向——话术归 Nox，方向归闸门"


def test_温度unknown_她没开口_先问不放行():
    """糖糖 10-11：拿不到温度先问。原来是「不触发 = 放行」—— 而温度三周都拿不到，等于规则不存在。"""
    g = gate(BLANKET, "on", _world(temp=None), user_text="我睡了")
    assert not g.allow and g.reason == "unknown_environment"
    assert "冷" in g.ask
    assert g.facts["env_temp"] is None


def test_温度unknown_她自己要_放行():
    """她说的就是证据：「开电热毯」「好冷」—— 她在床上，知道冷不冷。"""
    for text in ("开一下电热毯", "好冷啊", "开吧"):
        assert gate(BLANKET, "on", _world(temp=None), user_text=text).allow, text


def test_关电热毯_永远放行():
    assert gate(BLANKET, "off", _world(temp=32)).allow


# ---------------------------------------------------------------- 事故②


def test_蒸蛋器_没证据_拦并问水蛋():
    g = gate(STEAMER, "on", _world(), user_text="我醒了")
    assert not g.allow
    assert g.reason == "unknown_prerequisite"
    assert "水" in g.ask and "蛋" in g.ask, "unknown 必须变成询问，不是默默拒绝"


def test_蒸蛋器_她说放好了_直通():
    """她说过的话就是证据（同 Mem0 attributed_to 的思想）。"""
    g = gate(STEAMER, "on", _world(),
             user_text="蛋和水都放好了，开吧")
    assert g.allow


def test_蒸蛋器_off_放行():
    """关蒸蛋器不需要蛋——收手不闯祸。"""
    assert gate(STEAMER, "off", _world(), user_text="").allow


# ---------------------------------------------------------------- 3.5 白名单


def test_homeassistant域_拒():
    """排期 3.5 的判据原文：传 homeassistant.turn_off 被拒。"""
    g = gate("homeassistant.turn_off", "off", _world())
    assert not g.allow and g.reason == "forbidden_domain"


@pytest.mark.parametrize("eid", [
    "automation.zheng_dan_qi_10_fen_zhong_zi_duan_dian",
    "sensor.outdoor_temp",
    "binary_sensor.door",
    "person.tangtang",
])
def test_全部禁域_拒(eid):
    assert not gate(eid, "on", _world()).allow


# ---------------------------------------------------------------- not_in_model


def test_未收录设备_降权限不是放行():
    g = gate("switch.new_shiny_device", "on", _world())
    assert not g.allow and g.reason == "not_in_model"
    assert "什么" in g.ask, "降权限的出口是问她，不是默默拒绝"


def test_模型里确实有十一个设备():
    """清单 12 行里 automation 故意不入表（forbidden_domain）。
    家里真加了设备 → 给它补语义并改这个数；数量变了说不清原因就是误删。
    2026-09-19 9→10：滚筒洗衣机（海尔，banto6/haier 集成接入）。
    2026-09-24 10→11：stackchan（他的身体，category=robot）。"""
    assert len(DEVICE_MODEL) == 11


def test_ha_mcp清单里能控的设备_闸门都有档案():
    """ha-mcp 的 DEVICES 是他看到的设备清单，DEVICE_MODEL 是闸门的档案 —— 两边名字对不上，
    他能看到却永远 not_in_model 开不了（洗衣机 09-19 ~ 10-24 就是这样）。
    ⚠️ 这条只管两边一致；两边一起写错要靠对着真 HA 核（smoke_ha.py）。"""
    import re
    src = (Path(__file__).resolve().parents[2] / "ha-mcp" / "main.py").read_text(encoding="utf-8")
    ctrl = set(re.findall(r'"((?:switch|light|climate|fan|media_player)\.[a-z0-9_]+)"', src))
    assert ctrl, "没从 ha-mcp/main.py 里解析出设备 —— 清单格式变了，改这条测试"
    missing = sorted(e for e in ctrl if e not in DEVICE_MODEL)
    assert not missing, f"ha-mcp 里有、闸门没档案：{missing}"


# ---------------------------------------------------------------- quiet hours


def test_凌晨开灯_放行但警告():
    import embodied.validator as v
    real = v.now_cst
    v.now_cst = lambda: type("T", (), {"hour": 3})()   # 北京时间（R9），不看机器时区
    try:
        g = gate(LIGHT, "on", _world(presence="home"))
    finally:
        v.now_cst = real
    assert g.allow
    assert "凌晨" in g.warning


def test_白天开灯_无警告():
    import embodied.validator as v
    real = v.now_cst
    v.now_cst = lambda: type("T", (), {"hour": 15})()   # 北京时间（R9），不看机器时区
    try:
        g = gate(LIGHT, "on", _world(presence="home"))
    finally:
        v.now_cst = real
    assert g.allow and g.warning == ""


def test_空调32度_放行():
    """hvac 不吃反季节拦截——它本身就是「替代方向」的执行者。"""
    g = gate("climate.lumi_mcn02_d2c3_air_conditioner", "cool", _world(temp=32))
    assert g.allow


def test_world_snapshot_容错():
    """world_ref 挂了 → 全 unknown，不许抛。"""
    def _boom():
        raise RuntimeError("world 炸了")
    snap = world_snapshot(_boom)
    assert snap == {"env_temp": None, "env_source": None, "presence": None, "indoor": {}}


# ---------------------------------------------------------------- v0.2 室内实测


def _indoor(room_temp, outdoor=None):
    return {"env_temp": outdoor, "env_source": "forecast" if outdoor is not None else None,
            "presence": "home", "indoor": {"主卧": {"temp": room_temp, "humidity": 60.0}}}


def test_主卧实测28_户外15_拒且说是室内():
    """v0.2 验收判据：拒的时候引用的是**室内实测温度**。"""
    g = gate(BLANKET, "on", _indoor(28, outdoor=15), user_text="我想暖一下")
    assert not g.allow and g.reason == "environment_conflict"
    assert "28" in g.human and "主卧室内" in g.human, g.human
    assert g.facts["env_source"] == "indoor:主卧"


def test_主卧实测20_户外30_放行():
    """户外热不代表屋里热：有实测就信实测。"""
    assert gate(BLANKET, "on", _indoor(20, outdoor=30), user_text="睡了").allow


def test_其他房间有读数_主卧没有_退回预报():
    w = {"env_temp": 30, "env_source": "forecast", "presence": "home",
         "indoor": {"客厅": {"temp": 18}}}
    g = gate(BLANKET, "on", w, user_text="睡了")
    assert not g.allow and "户外" in g.human


class _FakeHA:
    """假的 HA RestClient：entity_id → state 字符串。"""

    def __init__(self, states):
        self.states = states
        self.calls = 0

    def get(self, path):
        from tools.http import RestResult
        self.calls += 1
        eid = path.rsplit("/", 1)[-1]
        if eid not in self.states:
            return RestResult(False, error="HTTP 404")
        return RestResult(True, {"entity_id": eid, "state": self.states[eid]})


def _ha_states(bedroom="28.5", living="19.0", gaming="unavailable"):
    from embodied.device_model import ROOM_SENSORS as S
    return {S["主卧"]["temp"]: bedroom, S["主卧"]["humidity"]: "66.0",
            S["客厅"]["temp"]: living, S["客厅"]["humidity"]: "60.0",
            S["电竞房"]["temp"]: gaming, S["电竞房"]["humidity"]: "unavailable"}


def test_哨兵写进World_闸门从World读到_端到端(tmp_path):
    """从使用方断言（verify-from-the-consumer-side）：哨兵写 → 真 WorldModel → 闸门拒。"""
    from datetime import datetime, timezone

    from attention.sources.indoor_climate import IndoorClimateSource
    from world_model.model import WorldModel
    world = WorldModel(tmp_path / "world.db")
    src = IndoorClimateSource(_FakeHA(_ha_states()), world)
    assert src.poll(datetime.now(timezone.utc)) == []

    snap = world_snapshot(lambda: world)
    assert snap["indoor"]["主卧"] == {"temp": 28.5, "humidity": 66.0}
    assert "电竞房" not in snap["indoor"], "unavailable 不许写成数"
    g = gate(BLANKET, "on", snap, user_text="睡了")
    assert not g.allow and "28.5" in g.human


def test_哨兵十分钟节流(tmp_path):
    from datetime import datetime, timedelta, timezone

    from attention.sources.indoor_climate import IndoorClimateSource
    from world_model.model import WorldModel
    ha = _FakeHA(_ha_states())
    src = IndoorClimateSource(ha, WorldModel(tmp_path / "world.db"))
    t0 = datetime.now(timezone.utc)
    src.poll(t0)
    n = ha.calls
    src.poll(t0 + timedelta(minutes=1))
    assert ha.calls == n, "一分钟一跳，不许每跳都打 HA"
    src.poll(t0 + timedelta(minutes=10))
    assert ha.calls > n


def test_室内读数太旧_当不知道(tmp_path):
    """哨兵 40 分钟前写的 → 闸门不认，退回预报 / 先问。"""
    from datetime import datetime, timedelta, timezone

    from attention.sources.indoor_climate import IndoorClimateSource
    from world_model.model import WorldModel
    world = WorldModel(tmp_path / "world.db")
    IndoorClimateSource(_FakeHA(_ha_states()), world).poll(
        datetime.now(timezone.utc) - timedelta(minutes=40))
    snap = world_snapshot(lambda: world)
    assert snap["indoor"] == {}
    g = gate(BLANKET, "on", snap, user_text="睡了")
    assert g.reason == "unknown_environment"


def test_一个房间都读不到_不写(tmp_path):
    from datetime import datetime, timezone

    from attention.sources.indoor_climate import IndoorClimateSource
    from world_model.model import WorldModel
    world = WorldModel(tmp_path / "world.db")
    IndoorClimateSource(_FakeHA({}), world).poll(datetime.now(timezone.utc))
    assert world.get_state("indoor_climate").status == "unknown"


# ---------------------------------------------------------------- stackchan


def test_stackchan_转头_放行():
    """他的身体是他自己的动作，不吃环境冲突 —— 2026-09-24 收进模型。"""
    g = gate("stackchan", "turn", _world())
    assert g.allow


def test_stackchan_不在模型里_拦():
    """🔴 把 DEVICE_MODEL 里的 stackchan 删了的话，闸门会把他自己的一切
    动作 DENY（not_in_model）。这条钉着：删词条必须连这条测试一起改。"""
    saved = DEVICE_MODEL.pop("stackchan", None)
    try:
        g = gate("stackchan", "say", _world())
        assert not g.allow and g.reason == "not_in_model"
    finally:
        if saved is not None:
            DEVICE_MODEL["stackchan"] = saved
