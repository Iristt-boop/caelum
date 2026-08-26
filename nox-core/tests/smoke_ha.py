"""家居工具冒烟。**只读**，不动糖糖家里任何一个开关。

    .venv\\Scripts\\python.exe tests\\smoke_ha.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import config  # noqa: E402
from tools.ha import make_handlers  # noqa: E402
from tools.mcp_client import McpClient  # noqa: E402


def main() -> int:
    if not config.ha_url:
        print("NOX_HA_URL 没配，跳过")
        return 1

    client = McpClient(config.ha_url, name="ha", timeout=config.ha_timeout)

    print("--- 服务端有哪些工具 ---")
    t0 = time.time()
    r = client.list_tools()
    print(f"  {r.text if r.ok else r.error}   ({time.time() - t0:.1f}s)")
    if not r.ok:
        return 1

    handlers = make_handlers(client)

    print("\n--- ha_list_devices ---")
    t0 = time.time()
    devices = handlers["ha_list_devices"]({})  # type: ignore[operator]
    print(f"  ({time.time() - t0:.1f}s)")
    for line in devices.splitlines():
        print("  " + line)

    print("\n--- ha_get_state 床头灯（只读）---")
    t0 = time.time()
    state = handlers["ha_get_state"]({"entity_id": "light.yeelink_mbulb3_0170_light"})  # type: ignore[operator]
    print(f"  {state}   ({time.time() - t0:.1f}s)")

    print("\n--- 防呆检查：拿空调调用 ha_switch 应该被挡下 ---")
    wrong = handlers["ha_switch"]({"entity_id": "climate.lumi_mcn02_d2c3_air_conditioner", "state": "on"})  # type: ignore[operator]
    print(f"  {wrong}")
    assert "ha_set_climate" in wrong, "防呆没生效"

    print("\n--- 防呆检查：拿灯调用 ha_switch 应该被挡下 ---")
    wrong2 = handlers["ha_switch"]({"entity_id": "light.yeelink_mbulb3_0170_light", "state": "on"})  # type: ignore[operator]
    print(f"  {wrong2}")
    assert "ha_set_light" in wrong2, "防呆没生效"

    print("\n✅ 家居工具通了，且没有动任何设备")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
