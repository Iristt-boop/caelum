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


def test_温度unknown_不因温度拦():
    """env_temp 拿不到就是 unknown——规则不触发，不假装知道。"""
    g = gate(BLANKET, "on", _world(temp=None), user_text="")
    assert g.allow
    assert g.facts["env_temp"] is None


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


def test_模型里确实有九个设备():
    """清单 10 行里 automation 故意不入表（forbidden_domain）。
    家里真加了设备 → 给它补语义并改这个数；数量变了说不清原因就是误删。"""
    assert len(DEVICE_MODEL) == 9


# ---------------------------------------------------------------- quiet hours


def test_凌晨开灯_放行但警告():
    import embodied.validator as v
    real = v.datetime

    class _FakeDT:
        @staticmethod
        def now():
            return type("T", (), {"hour": 3})()

    v.datetime = _FakeDT
    try:
        g = gate(LIGHT, "on", _world(presence="home"))
    finally:
        v.datetime = real
    assert g.allow
    assert "凌晨" in g.warning


def test_白天开灯_无警告():
    import embodied.validator as v
    real = v.datetime

    class _FakeDT:
        @staticmethod
        def now():
            return type("T", (), {"hour": 15})()

    v.datetime = _FakeDT
    try:
        g = gate(LIGHT, "on", _world(presence="home"))
    finally:
        v.datetime = real
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
    assert snap == {"env_temp": None, "presence": None}
