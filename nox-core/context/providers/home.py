"""HomeProvider —— 家里现在什么样。

## 复用 ha-mcp，绝不新建第二套 HA 客户端

设备清单（entity_id、房间名、开关语义）只有一处真源：`ha-mcp/main.py` 的 `DEVICES`。
这边再抄一份就是第四处需要手工同步的清单 ——
2026-07-27 三处漂移漏了三个设备、还把电竞房空调标成卧室的，
糖糖说「HA 没接全」才发现（第二十节）。

所以这里走现成的 `McpClient`，和 `tools/ha.py` 用的是同一个客户端。

## 为它给 ha-mcp 加了一个 `hass_snapshot`

架构文档的示例是 `call("hass_list_devices")` 然后直接读 `occupied` / `rooms` ——
**但那个工具只返回名字和 entity_id，不带状态**。真要状态得对 8 个设备
逐个 `hass_get_state`，8 次串行往返。

所以在 ha-mcp 那边加了一个只读的 `hass_snapshot`：服务端并发查一次，
一个往返拿全部。设备清单仍然只有 `DEVICES` 这一处真源。

## 不进每轮名单

架构文档第八节本来就只在「家居控制」类请求里加载 home，普通聊天不加载。
在 Router 的 Context 分级做好之前（Day 7），它不会被调用 ——
这是**符合**文档的，不是偷懒。

顺带一提，即使将来接进去也别每轮拉：设备状态一变（她开了灯），
`dynamic_system` 就变，DeepSeek 的前缀缓存会从这里往后全部错位。
30 秒 TTL 就是为这个留的。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from context.base import BaseContextProvider, Turn
from tools.mcp_client import McpClient

logger = logging.getLogger(__name__)


class HomeProvider(BaseContextProvider):
    """家里的设备状态。只读。"""

    name = "home"
    section = "environment"
    # 30 秒（架构文档的缓存策略表）。HA 状态变化快，但也没必要每轮都拉
    ttl = timedelta(seconds=30)

    def __init__(self, client: McpClient, **kw: Any) -> None:
        super().__init__(**kw)
        self.client = client

    def _fetch(self, turn: Turn) -> dict[str, Any]:
        r = self.client.call("hass_snapshot", {})
        if not r.ok:
            # 失败就抛，基类会退回上一次的旧状态并标 stale。
            # 吞成空字典的话，他会以为家里什么都没开（第十九节第 3 条）
            raise RuntimeError(f"读家居状态失败: {r.error}")

        text = (r.text or "").strip()
        lines = [ln.strip("- ").strip() for ln in text.splitlines()[1:] if ln.strip()]

        # 只统计「明确查到状态」的设备。离线的那些 ha-mcp 会标「查不到」，
        # 不能当成 off —— HA 在设备离线时保留断电前的最后状态，
        # 看着跟正常的一模一样，这个坑 2026-07-27 踩过（第二十节）
        unknown = [ln for ln in lines if "查不到" in ln]
        on = [ln for ln in lines if "：on" in ln or "：heat" in ln or "：cool" in ln]

        return {
            "devices": lines,
            "on_count": len(on),
            "unknown_count": len(unknown),
            "raw": text,
        }

    def render(self, state: dict[str, Any]) -> str:
        if state.get("available") is False or not state.get("devices"):
            return ""
        head = "【家里】"
        if state.get("stale"):
            head = f"【家里（{int(state.get('stale_age_s', 0))} 秒前的状态）】"
        body = "\n".join(state["devices"])
        tail = ""
        if state.get("unknown_count"):
            # 明说有几个查不到，别让他把「查不到」当成「关着」
            tail = f"\n（有 {state['unknown_count']} 个设备查不到状态，可能离线，别当成关着的）"
        return f"{head}\n{body}{tail}"
