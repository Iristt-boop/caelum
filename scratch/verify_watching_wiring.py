"""线上验证共影 P1 的接线。**只读，不写任何东西。**

验三件：
  1. Core 能打通 bridge 的 /api/watch/state（拿不到就该放行，不是拦）
  2. WatchingCheck 的判断和 bridge 的回答一致
  3. watching_history 工具真的注册了，而且「没看过」说的是人话

⚠️ 不往 watch_sessions 里写测试数据 —— 那会在她的「一起看过」里留一条垃圾。
「她在看片时被拦」那条路由 9 个单元测试覆盖，不在生产上造场景。

跑完请删掉自己。
"""
import sys

sys.path.insert(0, "/root/nox-core")

from datetime import datetime  # noqa: E402

from attention.care.watching import WatchingCheck  # noqa: E402
from nox import Nox  # noqa: E402
from personality.mood import now_cst  # noqa: E402

core = Nox()
print("=== bridge 接上了吗 ===")
print("    core.bridge =", type(core.bridge).__name__ if core.bridge else None)

print("\n=== bridge 怎么说 ===")
r = core.bridge.get("/api/watch/state")
print("    ok =", r.ok, "| data =", r.data if r.ok else r.error)

print("\n=== WatchingCheck 怎么判 ===")
check = WatchingCheck(core.bridge)
reason = check(now_cst())
print("    拦截理由 =", repr(reason), "（空字符串 = 放行）")
watching = bool((r.data or {}).get("watching")) if r.ok else False
assert bool(reason) == watching, "判断和 bridge 的回答对不上！"
print("    ✓ 和 bridge 的回答一致")

print("\n=== watching_history 工具注册了吗 ===")
names = list(core.loop.tools)   # dict: 名字 → Tool
print("    工具总数 =", len(names))
print("    watching_history 在不在 =", "watching_history" in names)
print("    最后一个是不是 remind_myself =", names[-1])

print("\n=== 工具真跑一次（只读）===")
handler = core.loop.tools["watching_history"].handler
print(handler({}))
