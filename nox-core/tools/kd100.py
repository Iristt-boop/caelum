"""快递100 MCP（https://api.kuaidi100.com/mcp/streamable?key=NOX_KD100_KEY）—— 包裹轨迹与时效。

全部只读。按单扣费、40 天内同单号不重复扣 —— 她随口问「我的快递呢」
不算成本压力，但**别循环轮询**同一个单号，问一次查一次就够了。
寄件下单类（create_order / cancel_order / order_price）**不接**。

鉴权：Key 走 URL 参数（.env 的 NOX_KD100_MCP_URL 里已带上），
customer/secret/userid 绑定在账号侧，调用时不需要。
工具名 2026-09-05 list_tools 实测。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)

SERVER_TOOLS = {
    "kd100_track": "query_trace",
    "kd100_auto_number": "auto_number",
    "kd100_timeliness": "estimate_time",
    "kd100_price": "estimate_price",
}


#: 每个工具的副作用声明（审计 3.1）。**新加工具必须在这里登记**，
#: 否则 `_spec()` 直接抛 —— 炸在启动，好过某天悄悄下了一单。
_EFFECTS: dict[str, tuple[str, str | None]] = {
    "kd100_track": ("read", None),
    "kd100_auto_number": ("read", None),
    "kd100_timeliness": ("read", None),
    "kd100_price": ("read", None),
}


def _spec(name: str, description: str, params: dict) -> ToolSpec:
    try:
        effect, via = _EFFECTS[name]
    except KeyError:  # noqa: PERF203
        raise ValueError(
            f"{name} 没有在 kd100._EFFECTS 里声明副作用。"
            "拿不准就往重了标：花钱填 spend，撤不回来填 irreversible。"
        ) from None
    return ToolSpec(
        name=name, description=description, parameters=params,
        side_effect=effect, confirm_via=via,
    )


TRACK = _spec(
    "kd100_track",
    "查快递轨迹：包裹到哪了、什么状态。她说「我的快递呢」「怎么还没到」时用。"
    "只需要快递单号（顺丰/中通还要手机号）；不确定是哪家快递就先调 kd100_auto_number 识别。",
    {
        "type": "object",
        "properties": {
            "kuaidiNum": {"type": "string", "description": "快递单号"},
            "phone": {"type": "string", "description": "手机号后四位以上，顺丰/中通必填，其他快递不用"},
        },
        "required": ["kuaidiNum"],
    },
)

AUTO_NUMBER = _spec(
    "kd100_auto_number",
    "智能识别单号属于哪家快递。她只给了单号没说快递公司时，先用这个。",
    {
        "type": "object",
        "properties": {
            "kuaidiNum": {"type": "string", "description": "快递单号"},
        },
        "required": ["kuaidiNum"],
    },
)

TIMELINESS = _spec(
    "kd100_timeliness",
    "预估快递送达时间（寄件前）。「今天寄，得等到哪天」时用。"
    "kuaidicom 用小写编码，如 yuantong / zhongtong / shunfeng。",
    {
        "type": "object",
        "properties": {
            "kuaidicom": {"type": "string", "description": "快递公司小写编码"},
            "from": {"type": "string", "description": "出发地，如「广东省深圳市南山区」"},
            "to": {"type": "string", "description": "目的地，如「北京海淀区」"},
        },
        "required": ["kuaidicom", "from", "to"],
    },
)

PRICE = _spec(
    "kd100_price",
    "预估寄件运费。「寄这个多少钱」时用。顺丰/京东/德邦三家。",
    {
        "type": "object",
        "properties": {
            "kuaidicom": {"type": "string", "description": "shunfeng / jd / deban"},
            "recAddr": {"type": "string", "description": "收件地址"},
            "sendAddr": {"type": "string", "description": "寄件地址"},
            "weight": {"type": "string", "description": "重量 kg，默认 1.0"},
        },
        "required": ["kuaidicom", "recAddr", "sendAddr", "weight"],
    },
)

_SPECS = (TRACK, AUTO_NUMBER, TIMELINESS, PRICE)


def make_handlers(client: McpClient) -> dict[str, object]:
    def _call(tool: str, args: dict) -> str:
        r = client.call(SERVER_TOOLS[tool], args)
        if not r.ok:
            raise RuntimeError(f"快递100 查询失败: {r.error}")
        return r.text or "（快递100 没返回内容）"

    return {spec.name: (lambda args, _t=spec.name: _call(_t, args)) for spec in _SPECS}


def register_all(loop, client: McpClient) -> None:
    handlers = make_handlers(client)
    for spec in _SPECS:  # 顺序固定
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
