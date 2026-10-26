"""设备语义 —— Device Model v0.1（十个设备硬编码）。

「现在验证的是 Validator 这个思想是否正确，不是验证设备生态。」
设备多了再外置 YAML（届时 hass_list_devices 自动生成草稿）。

🔴 **不在表里的设备 = not_in_model = 降权限**：查询类放行，
set 状态一律 DENY + 问「它是什么」。新设备默认不安全 ——
Unknown 原则的另一种形状。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r 不是数字，按默认 %s 算", name, raw, default)
        return default


#: 三个房间的米家温湿度计（miaomiaoce.sensor_ht.t2，2026-10-24 接进 HA）。
#: embodied v0.2：闸门判断 A 类设备时读**它所在房间的实测**，不是户外预报。
#: 房间名要和 DEVICE_MODEL 里的 `room` 对得上
ROOM_SENSORS: dict[str, dict[str, str]] = {
    "主卧": {"temp": "sensor.miaomiaoce_t2_066f_temperature",
             "humidity": "sensor.miaomiaoce_t2_066f_relative_humidity"},
    "客厅": {"temp": "sensor.miaomiaoce_t2_d829_temperature",
             "humidity": "sensor.miaomiaoce_t2_d829_relative_humidity"},
    "电竞房": {"temp": "sensor.miaomiaoce_t2_467c_temperature",
               "humidity": "sensor.miaomiaoce_t2_467c_relative_humidity"},
}

#: 不许 Nox 设状态的域前缀（排期 3.5）。automation 是家里的自动化规则，
#: sensor 是只读事实 —— 设它们的状态等于污染数据或改掉别人的行为。
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "automation.", "homeassistant.", "sensor.", "binary_sensor.",
    "device_tracker.", "person.", "group.", "input_", "timer.",
)

#: 每台设备：它是什么（category）、动它的效果、什么时候不许动（deny_when）、
#: 启动前需要什么（requires，unknown_policy=ask）。
DEVICE_MODEL: dict[str, dict] = {
    # ---- A 类：环境型（时机/季节要对）----
    "switch.xiaomi_mj1_00f2_electric_blanket": {
        "name": "主卧电热毯",
        "category": "heating",
        "effect": "temperature_up",
        "room": "主卧",
        # 户外（预报）和室内（实测）是两个数，阈值分开（v0.2 启动卡「已知约束」）。
        # 室内阈值 22°C：她 10-24 定的（主卧实测超过 22 就不开）。临时调用 NOX_BLANKET_INDOOR_MAX_C
        "deny_when": {"env_temp_above": 26,
                      "indoor_temp_above": _env_float("NOX_BLANKET_INDOOR_MAX_C", 22)},
        "alternative": "cooling_possible",   # 夏天想暖 → 该往制冷走
    },
    # 米家暖风机（2026-10-26 接进 HA，放在衣帽间）。和电热毯同一类事故面：反季节开加热。
    # 衣帽间没有温度计（机身自带的那个贴着出风口，开着就偏高，不能当室温）→ 只用户外那条 26°C
    "climate.xiaomi_ma8_5534_heater": {
        "name": "衣帽间暖风机",
        "category": "heating",
        "effect": "temperature_up",
        "room": "衣帽间",
        "deny_when": {"env_temp_above": 26},
        "alternative": "cooling_possible",
    },
    "fan.dmaker_p5c_6d3e_fan": {
        "name": "主卧风扇",
        "category": "cooling",
    },
    "climate.gua_shi_kong_diao_climate": {
        "name": "电竞房空调", "category": "hvac",
    },
    "climate.lumi_mcn02_d2c3_air_conditioner": {
        "name": "主卧空调", "category": "hvac",
    },
    "climate.lumi_mcn02_d7b8_air_conditioner": {
        "name": "客厅空调", "category": "hvac",
    },
    # HA 里是 kai_guan_**ji**_zhuang_tai。原来少写了 ji，闸门对真实体一直 not_in_model（10-24 修）
    "switch.gun_tong_xi_yi_ji_kai_guan_ji_zhuang_tai": {
        "name": "滚筒洗衣机", "category": "appliance",
    },
    # ---- C 类：在场/时间型（只警告不拦 —— 半夜她醒了开灯是合理的，
    #      validator 看不到对话，拦了会把正当关怀一起拦掉）----
    "light.yeelink_mbulb3_0170_light": {
        "name": "主卧床头灯", "category": "light", "quiet_hours": True,
    },
    "media_player.xiaomi_eaffh1_9a43_play_control": {
        "name": "客厅电视", "category": "media", "quiet_hours": True,
    },
    # ---- E 类：附属 ----
    "switch.dmaker_p5c_6d3e_horizontal_swing": {
        "name": "主卧风扇摆风", "category": "accessory",
        "parent": "fan.dmaker_p5c_6d3e_fan",
    },
    "switch.xiaomi_ma8_5534_horizontal_swing": {
        "name": "衣帽间暖风机摆风", "category": "accessory",
        "parent": "climate.xiaomi_ma8_5534_heater",
    },
    # ---- B 类：物理负载型（requires unknown → ask）----
    "switch.cuco_v3_7680_switch": {
        "name": "蒸蛋器",
        "category": "cooking",
        "requires": ["水", "蛋"],
        "unknown_policy": "ask",
    },
    # 「蒸蛋器 10 分钟自动断电」automation 故意不入表 ——
    # 它走 forbidden_domain 被拒；真要动家里的自动化，人工加白并写明理由。
    # ---- F 类：他的身体（stackchan，2026-09-24）----
    # 转头/表情/说话/灯环都是他自己的动作，不是「她家的设备」——
    # 不设 environment_conflict / requires。quiet_hours 只警告：
    # 深夜对着一台会说话的机器人说话，该有人提醒他小声点
    "stackchan": {
        "name": "stackchan（他的身体）",
        "category": "robot",
        "quiet_hours": True,
    },
}

#: B 类就绪证据的形状。**她说过的话就是证据**（同 Mem0 attributed_to 的思想）：
#: 本轮她的话里出现「放好/加/打蛋」和对应名词，该项视为已就绪。
READY_EVIDENCE: dict[str, str] = {
    "水": r"(加|放|倒|灌|接)[了]?[^。]{0,6}水|水[了]?[已]?(加好|放好|倒好|接好)",
    "蛋": r"(放|加|打|备)[了]?[^。]{0,6}蛋|蛋[已]?(放好|备好|打好)",
}

#: 安静时段（小时，本地时间）。只影响 WARNING，不做硬拦
QUIET_HOURS = (1, 9)
