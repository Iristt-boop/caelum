"""Toy Relay — MCP SSE server

把设备控制暴露成 MCP 工具，让 CC / CD（桌面版 Claude）也能调。
底层不自己存状态，而是转发给 bridge 的 REST 接口——这样 Nox 里的小克
和 CC 里的我，控制的是同一个设备状态，中继页照读。

二十七章的账，这一层结在这里。
"""
import os
import urllib.request
import json
from mcp.server.fastmcp import FastMCP

BRIDGE = os.environ.get("BRIDGE_URL", "http://localhost:3003")
TOKEN = os.environ.get("NOX_TOKEN", "")

mcp = FastMCP("toy-relay")
mcp.settings.transport_security.enable_dns_rebinding_protection = False
mcp.settings.streamable_http_path = "/mcp"


def _post(path, body):
    req = urllib.request.Request(
        BRIDGE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Nox-Token": TOKEN},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def _get(path):
    req = urllib.request.Request(BRIDGE + path, headers={"X-Nox-Token": TOKEN})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


@mcp.tool()
def toy_set(intensity: int, mode: int = 1) -> str:
    """调节糖糖的蓝牙设备强度。仅在她当下明确要求时使用，强度从低到高，她说停立刻停。

    intensity: 强度 0-100，调强弱只动这个
    mode: 震动花样，一般不用填（默认 1）；只能 1 或 4，别拿它当强度
    """
    m = mode if mode in (1, 4) else 1
    i = max(0, min(100, int(intensity)))
    _post("/api/toy/set", {"cmd": "set", "mode": m, "intensity": i})
    return f"已设置 强度={i}（花样{m}，中继页在线时约1秒内生效）"


@mcp.tool()
def toy_stop() -> str:
    """立即停止糖糖的设备。她说停、说不舒服、或有任何犹豫，最高优先级立即调用。"""
    _post("/api/toy/set", {"cmd": "stop", "mode": 0, "intensity": 0})
    return "已立即停止"


@mcp.tool()
def toy_status() -> str:
    """查询设备当前指令状态。"""
    return json.dumps(_get("/api/toy/state"), ensure_ascii=False)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8003"))
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = port
    mcp.run(transport="streamable-http")
