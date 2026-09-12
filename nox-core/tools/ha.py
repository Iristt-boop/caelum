"""家居控制工具 —— 转接 ha-mcp。

不直接打 Home Assistant 的 REST API，而是走糖糖已有的 ha-mcp 服务：
设备清单、entity_id、开关语义都在那边维护过了，这边再抄一份就是
第四处需要手工同步的清单 —— 今天刚因为三处漂移漏了三个设备。

工具描述里刻意**不列 entity_id**。让他先调 ha_list_devices 拿实时清单，
这样你在 HA 里新增设备后，改一处 ha-mcp 就够，Nox Core 不用动。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools import context
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)


LIST_SPEC = ToolSpec(
    name="ha_list_devices",
    description=(
        "列出糖糖家里所有可控设备和它们的 entity_id。"
        "控制任何设备之前**必须先调这个**拿到 entity_id，"
        "绝不能自己编造或凭记忆猜 —— 猜错会开错房间的电器。"
    ),
    parameters={"type": "object", "properties": {}},
)

GET_SPEC = ToolSpec(
    name="ha_get_state",
    description=(
        "查某个设备当前状态。空调会带室温和设定温度，灯会带亮度。"
        "在动手改之前先查一下，别把已经开着的又开一遍。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "entity_id": {"type": "string", "description": "从 ha_list_devices 拿到的 entity_id"}
        },
        "required": ["entity_id"],
    },
)

SWITCH_SPEC = ToolSpec(
    name="ha_switch",
    description=(
        "开关设备：风扇、风扇摆风、电热毯、电视、**蒸蛋器**。"
        "⚠️ 空调用 ha_set_climate，灯用 ha_set_light —— 别用这个。\n"
        "蒸蛋器：糖糖早上做早饭用的，她说「蒸蛋器」「煮蛋」「做早饭」都是指它。"
        "**开了之后 10 分钟 Home Assistant 会自动断电**，你不用记着关，"
        "也别提醒她关 —— 那是加热设备的安全兜底，已经在 HA 里配好了。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "entity_id": {"type": "string"},
            "state": {"type": "string", "enum": ["on", "off"]},
        },
        "required": ["entity_id", "state"],
    },
)

CLIMATE_SPEC = ToolSpec(
    name="ha_set_climate",
    description=(
        "控制空调。家里有三台（主卧 / 客厅 / 电竞房），"
        "**糖糖只说「开空调」没说房间时，先问她哪一间，不要自己挑一台**。"
        "mode 可选 off/auto/cool/heat/dry/fan_only；temperature 16-30。"
        "可以只给模式、只给温度，或者两个都给。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "entity_id": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": ["off", "auto", "cool", "heat", "dry", "fan_only"],
            },
            "temperature": {"type": "integer", "minimum": 16, "maximum": 30},
        },
        "required": ["entity_id"],
    },
)

LIGHT_SPEC = ToolSpec(
    name="ha_set_light",
    description=(
        "控制灯（主卧床头灯）。state 填 on/off；"
        "brightness 亮度 1-100，只在开灯时有效，不传就保持原亮度。"
        "她说「暗一点」这类，给个 10-30 的值。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "entity_id": {"type": "string"},
            "state": {"type": "string", "enum": ["on", "off"]},
            "brightness": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["entity_id", "state"],
    },
)


def make_handlers(client: McpClient) -> dict[str, object]:
    """生成处理函数。

    失败一律 raise —— 让 loop 按「工具失败」原样回传给模型（见 guard.py）。
    在这里 try/except 转成一句"失败了"，正是会让他开始编造的那个错误：
    他会说"灯已经开了"，而实际上根本没开。
    """

    def _call(tool: str, args: dict) -> str:
        r = client.call(tool, args)
        if not r.ok:
            raise RuntimeError(f"家居控制失败: {r.error}")
        return r.text or "（服务没有返回内容）"

    # 状态变更类工具发完命令后立刻查状态，可能还是旧的 ——
    # IR 桥接 / 云桥设备从命令被收到到状态更新有几秒滞后。
    # 等一小拍再读一次，这样返回给他的话就是他动手之后的真实状态。
    def _readback(entity_id: str, delay: float = 1.5) -> str:
        import time
        time.sleep(delay)
        try:
            r = client.call("hass_get_state", {"entity_id": entity_id})
            if r.ok and r.text:
                extra = (r.text or "").strip()
                if len(extra) > 300:
                    extra = extra[:297] + "..."
                return extra
        except Exception:  # noqa: BLE001 —— readback 是锦上添花，不能让它变成故障
            pass
        return ""

    def list_devices(_args: dict) -> str:
        return _call("hass_list_devices", {})

    def get_state(args: dict) -> str:
        eid = str(args.get("entity_id", "")).strip()
        if not eid:
            return "没有给 entity_id。先调 ha_list_devices 拿到设备清单。"
        return _call("hass_get_state", {"entity_id": eid})

    def switch(args: dict) -> str:
        eid = str(args.get("entity_id", "")).strip()
        state = str(args.get("state", "")).strip().lower()
        if not eid or state not in ("on", "off"):
            return "参数不对：需要 entity_id 和 state(on/off)。"
        if eid.startswith("climate."):
            return "这是空调，请改用 ha_set_climate。"
        if eid.startswith("light."):
            return "这是灯，请改用 ha_set_light（能调亮度）。"
        result = _call("hass_set_state", {"entity_id": eid, "state": state})
        # 设备状态变了 → 打掉 home Provider 的缓存。它 TTL 只有 30 秒，
        # 但 30 秒里他会照旧说「那盏灯是关的」。见 tools/context.py 的 wrote()。
        context.wrote("home")
        extra = _readback(eid)
        return f"{result}\n（回读：{extra}）" if extra else result

    def set_climate(args: dict) -> str:
        eid = str(args.get("entity_id", "")).strip()
        if not eid.startswith("climate."):
            return f"{eid or '(空)'} 不是空调。先调 ha_list_devices 确认 entity_id。"
        payload: dict = {"entity_id": eid}
        if args.get("mode"):
            payload["mode"] = str(args["mode"])
        if args.get("temperature"):
            payload["temperature"] = int(args["temperature"])
        if len(payload) == 1:
            return "没指定模式或温度，空调没动。"
        result = _call("hass_set_climate", payload)
        context.wrote("home")     # 同上：空调状态也挂在 home 里
        extra = _readback(eid)
        return f"{result}\n（回读：{extra}）" if extra else result

    def set_light(args: dict) -> str:
        eid = str(args.get("entity_id", "")).strip()
        if not eid.startswith("light."):
            return f"{eid or '(空)'} 不是灯。先调 ha_list_devices 确认 entity_id。"
        payload: dict = {"entity_id": eid, "state": str(args.get("state", "on"))}
        if args.get("brightness"):
            payload["brightness"] = int(args["brightness"])
        result = _call("hass_set_light", payload)
        context.wrote("home")     # 同上
        extra = _readback(eid)
        return f"{result}\n（回读：{extra}）" if extra else result

    return {
        "ha_list_devices": list_devices,
        "ha_get_state": get_state,
        "ha_switch": switch,
        "ha_set_climate": set_climate,
        "ha_set_light": set_light,
    }


def register_all(loop, client: McpClient) -> None:
    """注册五个家居工具。

    注册顺序固定 —— 工具定义是缓存前缀的一部分，顺序变了缓存就失效。
    """
    handlers = make_handlers(client)
    for spec in (LIST_SPEC, GET_SPEC, SWITCH_SPEC, CLIMATE_SPEC, LIGHT_SPEC):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
