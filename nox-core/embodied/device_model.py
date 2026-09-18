"""设备语义 —— Device Model v0.1（十个设备硬编码）。

「现在验证的是 Validator 这个思想是否正确，不是验证设备生态。」
设备多了再外置 YAML（届时 hass_list_devices 自动生成草稿）。

🔴 **不在表里的设备 = not_in_model = 降权限**：查询类放行，
set 状态一律 DENY + 问「它是什么」。新设备默认不安全 ——
Unknown 原则的另一种形状。
"""

from __future__ import annotations

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
        "deny_when": {"env_temp_above": 26},
        "alternative": "cooling_possible",   # 夏天想暖 → 该往制冷走
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
    # ---- B 类：物理负载型（requires unknown → ask）----
    "switch.cuco_v3_7680_switch": {
        "name": "蒸蛋器",
        "category": "cooking",
        "requires": ["水", "蛋"],
        "unknown_policy": "ask",
    },
    # 「蒸蛋器 10 分钟自动断电」automation 故意不入表 ——
    # 它走 forbidden_domain 被拒；真要动家里的自动化，人工加白并写明理由。
}

#: B 类就绪证据的形状。**她说过的话就是证据**（同 Mem0 attributed_to 的思想）：
#: 本轮她的话里出现「放好/加/打蛋」和对应名词，该项视为已就绪。
READY_EVIDENCE: dict[str, str] = {
    "水": r"(加|放|倒|灌|接)[了]?[^。]{0,6}水|水[了]?[已]?(加好|放好|倒好|接好)",
    "蛋": r"(放|加|打|备)[了]?[^。]{0,6}蛋|蛋[已]?(放好|备好|打好)",
}

#: 安静时段（小时，本地时间）。只影响 WARNING，不做硬拦
QUIET_HOURS = (1, 9)
