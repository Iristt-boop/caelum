"""房间工具 —— 他在我们家里的身体。

转接 `caelum-room` 的 MCP（私有仓库，基于 CairoIan/room-mcp-kit，MIT）。
房间是一间像素小屋，糖糖用网页控制自己（owner），他走这四个工具控制自己
（companion），两边共享同一份房间状态。

## 🔴 他只能动他自己

房间那边把这条焊死了：MCP 只写 `companion`，网页只写 `owner`
（`room_move` 的描述原话：「This tool cannot move the owner.」）。
**这不是限制，是这件事该有的样子** —— 替她操作她的身体，
和陪她待在一个房间里，是两回事。

## 走到她那儿 = 移动到 character owner

`room_move` 的 `target.kind` 有三种：`tile`（坐标）/ `furniture`（某件家具）
/ `character`（目前只能填 owner，也就是「到她身边去」）。

## ⚠️ 不为每件家具加一个工具

上游的规矩，我们照办：家具再多也只有 `room_use_furniture` 一个入口，
具体做什么由 `interaction` 决定。加十件家具不该让他的工具清单长十条 ——
那是白烧缓存前缀。

## ⚠️ 房间可能不在

它是**本机 loopback 服务**（`Room MCP must remain loopback-only`，
上游源码里写死的）。够不到的时候如实说够不到，
**不猜是没启动还是网不通** —— 同 `tools/computer.py` 那条教训。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.llm import ToolSpec
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)


STATE_SPEC = ToolSpec(
    name="room_get_state",
    description=(
        "看一眼我们家里现在什么样：她在哪、我在哪、各自在做什么。"
        "想动之前先看这个，别凭记忆猜位置。"
        "家具清单、地图、最近发生的事默认不带，需要时再单独要。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "include_furniture": {"type": "boolean", "description": "带上家具清单和它们的 id"},
            "include_map": {"type": "boolean", "description": "带上可行走网格（要算路线时才需要）"},
            "include_recent_events": {"type": "boolean", "description": "带上最近发生的事"},
        },
    },
)

MOVE_SPEC = ToolSpec(
    name="room_move",
    description=(
        "在房间里走到某处。三种去处：某个格子（tile，给 x/y）、某件家具旁边"
        "（furniture，给 furniture_id）、或者**到她身边去**（character，character_id 填 owner）。"
        "⚠️ 只能移动我自己 —— 她的身体归她自己控制，我动不了，也不该动。"
    ),
    parameters={
        "type": "object",
        "required": ["target"],
        "properties": {
            "target": {
                "type": "object",
                "required": ["kind"],
                "properties": {
                    "kind": {"type": "string", "enum": ["tile", "furniture", "character"]},
                    "x": {"type": "integer", "minimum": 0, "maximum": 99},
                    "y": {"type": "integer", "minimum": 0, "maximum": 99},
                    "furniture_id": {"type": "string", "description": "从 room_get_state 的家具清单里取"},
                    "character_id": {"type": "string", "enum": ["owner"], "description": "到她身边"},
                },
            }
        },
    },
)

USE_SPEC = ToolSpec(
    name="room_use_furniture",
    description=(
        "用一件家具：坐到沙发上、躺到床上、开电视、在电脑前坐下。"
        "房间会自己判断能不能用、挑一个位置、走过去。"
        "furniture_id 从 room_get_state 拿，别编。"
        "一件家具有多个动作时用 interaction 指定。"
    ),
    parameters={
        "type": "object",
        "required": ["furniture_id"],
        "properties": {
            "furniture_id": {"type": "string"},
            "interaction": {"type": "string", "description": "家具只有一个动作时可以不填"},
        },
    },
)

STOP_SPEC = ToolSpec(
    name="room_stop",
    description="停下来：取消正在走的路和正在用的家具，把位置让出来，回到空闲。",
    parameters={"type": "object", "properties": {}},
)

SPECS = [STATE_SPEC, MOVE_SPEC, USE_SPEC, STOP_SPEC]


def make_client(url: str) -> McpClient:
    return McpClient(url, name="room")


#: 两条路，两套名字。
#:
#: 🔴 **生产走链路，不走直连。** 房间的 MCP 只允许绑回环（上游写死的），
#: 而 Core 跑在 VPS 上 —— 直连那条只有「Core 和房间同机」时成立，
#: 也就是本地开发。糖糖 2026-09-02 定的「跑在本地，私密一点」，
#: 所以线上这条是：Core → wss 反向链路 → 她电脑上的手 → 回环的房间。
#:
#: ⚠️ 名字必须跟着路走：直连时是房间 MCP 的原名，走链路时是网关
#: catalog 里的 Caelum 名（`room.*`）。写错的表现是「工具不存在」，
#: 而那看起来像房间挂了。
VIA_MCP = {s.name: s.name for s in SPECS}
VIA_LINK = {
    STATE_SPEC.name: "room.get_state",
    MOVE_SPEC.name: "room.move",
    USE_SPEC.name: "room.use_furniture",
    STOP_SPEC.name: "room.stop",
}


def make_handlers(client: Any, names: dict[str, str] | None = None) -> dict[str, Any]:
    """生成处理函数。

    🔴 **失败一律 raise**，不要在这里转成一句「没做成」的正常返回 ——
    那正是会让他开始编造的那个错误（同 ha.py：他会说「灯已经开了」
    而灯根本没开）。raise 之后 loop 按「工具失败」原样回传（见 guard.py）。

    ⚠️ 错误话术里**不断言原因**：够不到就是够不到，
    是房间没启动还是网不通，这边分不出来（同 tools/computer.py，2026-08-31）。
    """

    wire = names or VIA_MCP

    def _call(spec_name: str, args: dict[str, Any] | None = None) -> str:
        r = client.call(wire[spec_name], args or {})
        if not r.ok:
            raise RuntimeError(
                f"够不到房间（{r.error or '没有回应'}）——"
                "分不出是房间服务没起、端口不对，还是网不通。"
            )
        #: ⚠️ 挑字段按**工具语义**，不按线上那个名字 ——
        #: 两条路的名字不一样，用 wire 后的名字去判会漏掉一整条路
        return _humanize(spec_name, r.text or "")

    return {
        STATE_SPEC.name: lambda **kw: _call(STATE_SPEC.name, kw),
        MOVE_SPEC.name: lambda **kw: _call(MOVE_SPEC.name, kw),
        USE_SPEC.name: lambda **kw: _call(USE_SPEC.name, kw),
        STOP_SPEC.name: lambda **kw: _call(STOP_SPEC.name, kw),
    }


def register_all(loop, client: Any, names: dict[str, str] | None = None) -> None:
    """注册四个房间工具。

    ⚠️ 顺序固定 —— 工具定义是缓存前缀的一部分，顺序变了缓存当场失效
    （同 ha.py 那条注释；话题池那次也为此专门挑了注册位置）。
    """
    handlers = make_handlers(client, names)
    for spec in SPECS:
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]


def _humanize(tool: str, raw: str) -> str:
    """房间回的是紧凑 JSON。原样丢给模型能读，但读一次要花不少 token，
    而且**关键的一两个字段会淹在里面**。这里只把最要紧的挑出来说一句人话，
    原始 JSON 附在后面 —— 挑漏了他还能自己看。

    ⚠️ 解析失败不吞：JSON 变了形状的时候，宁可把原文交上去，
    也不要装作看懂了（那会让他一本正经地转述一个不存在的状态）。
    """
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001
        return raw
    if not isinstance(d, dict):
        return raw
    if d.get("error"):
        return f"房间说这件事做不了：{d['error']}"

    if tool == "room_get_state":
        # ⚠️ `characters` 是**数组**不是按 id 索引的字典（2026-09-02 真跑一次才发现，
        # 第一版按字典取，安静地走了兜底分支、一个人也没挑出来）。
        chars = d.get("characters")
        who = {}
        if isinstance(chars, list):
            who = {c.get("id"): c for c in chars if isinstance(c, dict)}
        elif isinstance(chars, dict):
            who = chars

        bits = []
        for cid, cn in (("companion", "我"), ("owner", "糖糖")):
            c = who.get(cid)
            if not isinstance(c, dict):
                continue
            act = c.get("activity") or "空闲"
            where = c.get("furniture") or ""
            pos = c.get("position") or {}
            at = where or (f"({pos.get('x')},{pos.get('y')})" if pos else "")
            bits.append(f"{cn}：{act}{('·' + at) if at else ''}")
        head = "；".join(bits) if bits else "拿到了房间状态"
        return f"{head}\n\n{raw}"
    return raw
