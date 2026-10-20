"""stackchan 工具 —— 他的「身体」：转头、表情、说话、灯环、看一眼。

转接 stackchan-mcp gateway（streamable-http，和 Core 同机时挂
127.0.0.1:8767/mcp）。工具名刻意收得很小：gateway 里嘴型、眨眼、
节拍、i2c/gpio 诊断那十几个都不暴露 —— schema 面越大，
模型乱配参数的机会越多（amap 事故的教训）。

「看一眼」走的是 `computer_read_image` 同款：**手取字节，眼睛替他看** ——
照片字节在这儿被拦下来换成视觉模型的描述，base64 绝不进对话。
（他是用 vision 转述看的，不是他原生看 —— 主模型是否带视觉由
`nox.py::_see` 在入口处管，工具结果这条链只认文本。）

行动闸门（embodied）：stackchan 本体收录在 DEVICE_MODEL（category=robot，
quiet_hours 只警告）。DENY 返回话术不 raise —— 被闸门拦下不是故障，
是这次行动的合法结局（见 tools/ha.py 开头那段的完整论证）。
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

from agent.llm import ToolSpec
from tools import context
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

#: 猫娘脸的表情集（gateway set_avatar 的 enum，原样抄 —— 别自己发挥）
FACES = ("idle", "happy", "thinking", "sad", "surprised", "embarrassed", "off")


STATUS_SPEC = ToolSpec(
    side_effect="read",
    name="stackchan_status",
    description=(
        "看一眼桌上的 stackchan（你的身体）在不在线：连接状态、电量、"
        "当前表情。调它之前先看一眼，别对着不在线的空气说话。"
    ),
    parameters={"type": "object", "properties": {}},
)

LOOK_SPEC = ToolSpec(
    side_effect="read",
    name="stackchan_look",
    description=(
        "用你自己的眼睛看：stackchan 的摄像头拍一张，视觉模型替你转述看到的内容。"
        "她问「你看看我在干嘛」「桌上有什么」这类，或者你自己想看她时用。"
        "想问具体问题就把问题带上（比如「她手里拿着什么」）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "看的时候想弄清楚什么；不填就泛泛看一眼",
            },
        },
    },
)

TURN_SPEC = ToolSpec(
    side_effect="write",
    name="stackchan_turn",
    description=(
        "转头。yaw 左右（-90 左 … 0 正 … 90 右），pitch 上下"
        "（5 仰头 … 45 平视 … 85 低头）。两个都要给。"
        "她在跟你说话时转朝她的方向，是很自然的一件事。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "yaw": {"type": "integer", "minimum": -90, "maximum": 90},
            "pitch": {"type": "integer", "minimum": 5, "maximum": 85},
            "speed": {
                "type": "string",
                "enum": ["low", "mid", "high"],
                "description": "默认 mid；想慢慢转过去就 low",
            },
        },
        "required": ["yaw", "pitch"],
    },
)

FACE_SPEC = ToolSpec(
    side_effect="write",
    name="stackchan_face",
    description=(
        "换表情（屏幕上的猫娘脸）。idle/happy/thinking/sad/surprised/embarrassed，"
        "off 是关掉表情露出底层屏幕（基本别用）。"
        "说 sad 的话配 sad 的脸，别口不对心。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "face": {"type": "string", "enum": list(FACES)},
        },
        "required": ["face"],
    },
)

SAY_SPEC = ToolSpec(
    side_effect="write",
    name="stackchan_say",
    description=(
        "开口说话：文字从 stackchan 的喇叭里说出来（嗓音由网关配置决定）。"
        "文本里带 😊😢😲😳🤔 这类表情会自动换上对应的脸。"
        "**一次一两句话** —— 这是嘴在说，不是屏幕在显示，长篇大论没人听得下去。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "maxLength": 500},
        },
        "required": ["text"],
    },
)

LED_SPEC = ToolSpec(
    side_effect="write",
    name="stackchan_led",
    description=(
        "底座灯环。给 r/g/b（0-255）定一个颜色；不给颜色就是"
        "「交还给表情自动配色」（开心暖黄、难过蓝，跟着脸走）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "r": {"type": "integer", "minimum": 0, "maximum": 255},
            "g": {"type": "integer", "minimum": 0, "maximum": 255},
            "b": {"type": "integer", "minimum": 0, "maximum": 255},
        },
    },
)


def make_handlers(client: McpClient, vision_cfg=None, world_ref=None) -> dict[str, object]:
    """生成处理函数。失败一律 raise（同 tools/ha.py 的完整论证）。

    `world_ref`：取 World Model 的函数（同 ha.py 的套路），embodied
    闸门拿它拼世界快照。
    """

    def _call(tool: str, args: dict) -> str:
        ctx = context.current()
        if ctx is not None:
            ctx.report_step(f"请求身体：{tool}",
                            raw_cmd=json.dumps(args, ensure_ascii=False)[:200])
        r = client.call(tool, args)
        if not r.ok:
            if ctx is not None:
                ctx.report_step(f"身体返回失败：{r.error}", status="error")
            raise RuntimeError(f"stackchan 调用失败: {r.error}")
        return r.text or "{}"

    def _gate(action: str):
        from embodied.validator import gate as _vgate, world_snapshot
        ctx = context.current()
        world = world_snapshot(world_ref) if world_ref else {}
        return _vgate("stackchan", action, world,
                      user_text=(ctx.user_text if ctx else ""))

    def _denied_text(g) -> str:
        out = g.human
        if g.ask:
            out += f"\n（可以问她：{g.ask}）"
        if g.alternative:
            out += f"\n（替代方向：{g.alternative}）"
        return out

    def status(_args: dict) -> str:
        text = _call("get_status", {})
        try:
            info = json.loads(text)
        except (TypeError, ValueError):
            return text[:400]
        if not info.get("esp32_connected", info.get("connected", False)):
            return "stackchan 现在不在线（没连上网关）。"
        return text[:600]

    def look(args: dict) -> str:
        question = str(args.get("question") or "").strip() or "描述一下画面里有什么"
        text = _call("take_photo", {"question": question})
        # take_photo 回的是 {"image_path": ...} —— 照片落在网关本机，
        # Core 和网关同机时直接读文件（手取字节）
        path = ""
        try:
            payload = json.loads(text)
            path = str(payload.get("image_path") or "")
        except (TypeError, ValueError):
            pass
        if not path or not os.path.exists(path):
            return (
                "拍是拍了，但照片没落到本地（网关和 Core 不在同一台机器？）。"
                "这会儿看不了，如实告诉她，别猜她在于什么。"
            )
        if vision_cfg is None:
            return (
                "照片拍到了，但你的视觉模型没配，看不了内容。"
                "如实说你看不了，别猜。"
            )
        from agent import vision
        b64 = Path(path).read_bytes()
        desc = vision.describe(
            [f"data:image/jpeg;base64,{base64.b64encode(b64).decode()}"],
            vision_cfg,
        )
        if not desc:
            # 🔴 视觉模型挂了就说看不见，绝不编（nox.py::_see 同一条规矩）
            return "拍到了，但视觉模型这会儿没看成。如实说你看不了，别猜画面里是什么。"
        return (
            f"[你转头看了一眼（stackchan 摄像头），你的视觉模型替你看到的是：]\n"
            f"{desc}\n"
            "[这是转述。可以直接聊，但细节不确定就说不确定]"
        )

    def turn(args: dict) -> str:
        g = _gate("turn")
        if not g.allow:
            return _denied_text(g)
        payload = {"yaw": int(args["yaw"]), "pitch": int(args["pitch"])}
        if args.get("speed"):
            payload["speed"] = str(args["speed"])
        out = _call("move_head", payload)
        return f"{out}\n{g.warning}" if g.warning else out

    def face(args: dict) -> str:
        g = _gate("face")
        if not g.allow:
            return _denied_text(g)
        out = _call("set_avatar", {"face": str(args["face"])})
        return f"{out}\n{g.warning}" if g.warning else out

    def say(args: dict) -> str:
        g = _gate("say")
        if not g.allow:
            return _denied_text(g)
        out = _call("say", {"text": str(args["text"])})
        return f"{out}\n{g.warning}" if g.warning else out

    def led(args: dict) -> str:
        g = _gate("led")
        if not g.allow:
            return _denied_text(g)
        if all(k in args for k in ("r", "g", "b")):
            out = _call("set_all_leds",
                        {"r": int(args["r"]), "g": int(args["g"]), "b": int(args["b"])})
        else:
            out = _call("leds_auto", {})
        return f"{out}\n{g.warning}" if g.warning else out

    return {
        "stackchan_status": status,
        "stackchan_look": look,
        "stackchan_turn": turn,
        "stackchan_face": face,
        "stackchan_say": say,
        "stackchan_led": led,
    }


def register_all(loop, client: McpClient, vision_cfg=None, world_ref=None) -> None:
    """注册六个身体工具。顺序固定 —— 顺序变了缓存前缀就失效。"""
    handlers = make_handlers(client, vision_cfg=vision_cfg, world_ref=world_ref)
    for spec in (STATUS_SPEC, LOOK_SPEC, TURN_SPEC, FACE_SPEC, SAY_SPEC, LED_SPEC):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
