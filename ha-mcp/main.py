# Nox HA-MCP — 把家里 Home Assistant 设备暴露成 MCP 工具，供 SC / Claude Code 等多端共享
# 一份服务多端连：SC(xiaozhi) 和 Claude Code 都指向 noxtang.com/ha-mcp/ 即可控家电
import asyncio
import os
import httpx
from fastmcp import FastMCP

HA_URL = os.environ.get("HA_URL", "http://localhost:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
if not HA_TOKEN:
    raise RuntimeError("HA_TOKEN 环境变量未设置，拒绝启动")

mcp = FastMCP("nox-ha")

# 设备清单。
#
# ⚠️ 同一份清单目前散在三处，改这里记得同步另外两处：
#   1. bridge/server.js 的家居控制 prompt
#   2. xiaozhi-server 的 .config.yaml → plugins.home_assistant.devices
# 三处手工同步必然漂移 —— 2026-07-27 就漏了三个设备，糖糖说"没接全"
# 才发现。根治办法是让另外两处改调 hass_list_devices 动态取，见 README。
DEVICES = {
    "主卧 风扇": "fan.dmaker_p5c_6d3e_fan",
    "主卧 风扇摆风": "switch.dmaker_p5c_6d3e_horizontal_swing",
    "主卧 电热毯": "switch.xiaomi_mj1_00f2_electric_blanket",
    "客厅 电视": "media_player.xiaomi_eaffh1_9a43_play_control",
    # 这台原本被标成「卧室 空调」，但 HA 里它叫「电竞房 空调」。
    # 三台空调并存后必须区分，否则「开卧室空调」会开到电竞房去。
    "电竞房 空调": "climate.gua_shi_kong_diao_climate",
    "主卧 空调": "climate.lumi_mcn02_d2c3_air_conditioner",
    "客厅 空调": "climate.lumi_mcn02_d7b8_air_conditioner",
    "主卧 床头灯": "light.yeelink_mbulb3_0170_light",
}

def _h():
    return {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}


def _affected(r) -> int:
    """HA 服务调用返回「被改变的实体列表」，空列表 = 什么都没发生。

    为什么必须看这个：设备离线时（比如床头灯的物理开关被关掉），HA 照样
    回 HTTP 200，但返回体是 []。只看状态码就会报「已开灯」——而灯根本没亮。
    这种假成功比报错更糟：Nox 会理直气壮地告诉糖糖灯开好了。
    2026-07-27 实测踩过，错误码 -704042011（设备离线）。
    """
    if r.status_code not in (200, 201):
        return -1
    try:
        body = r.json()
    except Exception:
        return -1
    return len(body) if isinstance(body, list) else 1


def _offline_hint(entity_id: str) -> str:
    return (
        f"指令已发出，但没有任何设备响应 —— {entity_id} 很可能离线了。"
        "如果它有物理开关，先确认开关是打开的。"
    )

@mcp.tool()
async def hass_list_devices() -> str:
    """列出家里所有可控的智能设备及其 entity_id。控制任何设备前先看这个清单，entity_id 必须从这里严格选取、绝不编造。"""
    return "家里可控设备：\n" + "\n".join(f"- {k} → {v}" for k, v in DEVICES.items())

@mcp.tool()
async def hass_get_state(entity_id: str) -> str:
    """查询某个设备当前状态。entity_id 从 hass_list_devices 的清单里取。"""
    async with httpx.AsyncClient() as c:
        try:
            r = await c.get(f"{HA_URL}/api/states/{entity_id}", headers=_h(), timeout=10)
            if r.status_code != 200:
                return f"查询失败(HTTP {r.status_code})"
            d = r.json(); a = d.get("attributes", {})
            extra = ""
            if entity_id.startswith("climate."):
                extra = f"，室温{a.get('current_temperature')}°，设定{a.get('temperature')}°"
            elif entity_id.startswith("light.") and d.get("state") == "on":
                b = a.get("brightness")
                if b is not None:
                    extra = f"，亮度{round(int(b) / 255 * 100)}%"
            return f"{a.get('friendly_name', entity_id)} 当前：{d.get('state')}{extra}"
        except Exception as e:
            return f"HA 连接失败: {e}"

@mcp.tool()
async def hass_snapshot() -> str:
    """一次拿到家里所有设备的当前状态（给 Nox Core 的 HomeProvider 用）。

    为什么要有它：`hass_list_devices` 只给名字和 entity_id，不带状态；
    要状态就得对 8 个设备逐个 `hass_get_state`，那是 8 次串行往返。
    这里在服务端并发查一次就够 —— 设备清单仍然只有 DEVICES 这一处真源。

    离线的设备如实标「查不到」，**不猜、不省略** ——
    HA 在设备离线时会保留断电前的最后状态，看着跟正常的一模一样（第二十节）。
    """
    async def one(name: str, eid: str):
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{HA_URL}/api/states/{eid}", headers=_h(), timeout=8)
            if r.status_code != 200:
                return f"- {name}：查不到(HTTP {r.status_code})"
            d = r.json()
            a = d.get("attributes", {})
            st = d.get("state")
            extra = ""
            if eid.startswith("climate.") and st not in ("off", "unavailable", None):
                # 不是每台空调都上报室温 —— 主卧那台就没有。
                # 缺的字段直接不提，别把「室温 None°」摆到他面前当真值看
                bits = []
                cur, want = a.get("current_temperature"), a.get("temperature")
                if cur is not None:
                    bits.append(f"室温 {cur}°")
                if want is not None:
                    bits.append(f"设定 {want}°")
                if bits:
                    extra = "（" + "，".join(bits) + "）"
            elif eid.startswith("light.") and st == "on":
                b = a.get("brightness")
                if b is not None:
                    extra = f"（亮度 {round(int(b) / 255 * 100)}%）"
            return f"- {name}：{st}{extra}"
        except Exception as e:
            return f"- {name}：查不到（{type(e).__name__}）"

    rows = await asyncio.gather(*(one(k, v) for k, v in DEVICES.items()))
    return "家里现在的状态：\n" + "\n".join(rows)


@mcp.tool()
async def hass_set_light(entity_id: str, state: str = "on", brightness: int = 0) -> str:
    """控制灯（如主卧床头灯）。state 填 on/off；brightness 亮度 1-100，只在开灯时有效。
    只想开关就别传 brightness；想调暗一点就传 brightness=20 这类。⚠️只对 light.* 设备有效。"""
    if not entity_id.startswith("light."):
        return f"{entity_id} 不是灯（light.*），请用 hass_set_state 或 hass_set_climate。"

    on = state.lower() in ("on", "开", "true", "1", "打开")
    if not on:
        async with httpx.AsyncClient() as c:
            try:
                r = await c.post(f"{HA_URL}/api/services/light/turn_off", json={"entity_id": entity_id}, headers=_h(), timeout=10)
                n = _affected(r)
                if n < 0:
                    return f"关灯失败(HTTP {r.status_code})"
                return f"已关灯：{entity_id}" if n else _offline_hint(entity_id)
            except Exception as e:
                return f"HA 连接失败: {e}"

    payload = {"entity_id": entity_id}
    note = ""
    if brightness:
        b = max(1, min(100, int(brightness)))
        # HA 的 brightness_pct 就是百分比，比 0-255 的 brightness 好懂
        payload["brightness_pct"] = b
        note = f"，亮度{b}%"
    async with httpx.AsyncClient() as c:
        try:
            r = await c.post(f"{HA_URL}/api/services/light/turn_on", json=payload, headers=_h(), timeout=10)
            n = _affected(r)
            if n < 0:
                return f"开灯失败(HTTP {r.status_code})"
            return f"已开灯：{entity_id}{note}" if n else _offline_hint(entity_id)
        except Exception as e:
            return f"HA 连接失败: {e}"

@mcp.tool()
async def hass_set_state(entity_id: str, state: str) -> str:
    """开关设备（风扇/风扇摆风/电热毯/电视）。state 填 on(开) 或 off(关)。
    ⚠️空调用 hass_set_climate，灯用 hass_set_light（能调亮度）。"""
    on = state.lower() in ("on", "开", "true", "1", "打开")
    domain = entity_id.split(".")[0]
    service = "turn_on" if on else "turn_off"
    async with httpx.AsyncClient() as c:
        try:
            r = await c.post(f"{HA_URL}/api/services/{domain}/{service}", json={"entity_id": entity_id}, headers=_h(), timeout=10)
            n = _affected(r)
            if n < 0:
                return f"操作失败(HTTP {r.status_code})"
            return f"已{'打开' if on else '关闭'}：{entity_id}" if n else _offline_hint(entity_id)
        except Exception as e:
            return f"HA 连接失败: {e}"

@mcp.tool()
async def hass_set_climate(entity_id: str, mode: str = "", temperature: int = 0) -> str:
    """控制空调。mode 可选：off(关) auto(自动) cool(制冷) heat(制热) dry(除湿) fan_only(送风)；temperature 目标温度16-30整数。可只给模式、只给温度或都给。"""
    done = []
    names = {"off": "关", "auto": "自动", "cool": "制冷", "heat": "制热", "dry": "除湿", "fan_only": "送风"}
    async with httpx.AsyncClient() as c:
        try:
            offline = False
            if mode:
                r = await c.post(f"{HA_URL}/api/services/climate/set_hvac_mode", json={"entity_id": entity_id, "hvac_mode": mode}, headers=_h(), timeout=10)
                n = _affected(r)
                if n < 0:
                    done.append(f"模式失败({r.status_code})")
                elif n == 0:
                    offline = True
                else:
                    done.append(f"模式→{names.get(mode, mode)}")
            if temperature and 16 <= int(temperature) <= 30:
                r = await c.post(f"{HA_URL}/api/services/climate/set_temperature", json={"entity_id": entity_id, "temperature": int(temperature)}, headers=_h(), timeout=10)
                n = _affected(r)
                if n < 0:
                    done.append(f"温度失败({r.status_code})")
                elif n == 0:
                    offline = True
                else:
                    done.append(f"温度→{int(temperature)}°")
            if offline and not done:
                return _offline_hint(entity_id)
            return "空调已设置：" + "，".join(done) if done else "没指定模式或温度，空调没动"
        except Exception as e:
            return f"HA 连接失败: {e}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8004"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
