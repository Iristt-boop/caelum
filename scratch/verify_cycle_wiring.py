"""线上验证：他在对话里看到的周期，现在是不是 World Model 那一份。

**只读。** 走的是 `_build_attention` —— 和服务启动时同一段装配代码，
所以验的是真接线，不是我在脚本里自己接一遍。
不 tick、不 chat、不推送，一个字都不会发给糖糖。

跑完请删掉自己。
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/root/nox-core")

from api.server import Sessions, _build_attention  # noqa: E402
from data.store import Store  # noqa: E402
from nox import Nox  # noqa: E402

print("=== World Model 里的 start（真源）===")
con = sqlite3.connect("file:/root/nox-core/data/world.db?mode=ro", uri=True)
starts = sorted(
    json.loads(o).get("date")
    for (o,) in con.execute("SELECT observed FROM observations WHERE type='menstrual'")
    if json.loads(o).get("event") == "start"
)
con.close()
print("   ", starts)

print("\n=== 装配（和服务同一段代码）===")
core = Nox()
db = Store(core.cfg.db_path)
sessions = Sessions(
    db, history_limit=core.cfg.history_limit,
    recent_window_tokens=core.cfg.recent_window_tokens,
)
_build_attention(core, sessions, db)      # 这里面那行 core.world = world
print("    core.world =", type(getattr(core, "world", None)).__name__)

p = core.context.get("health")
print("    provider  =", type(p).__name__, "| world_ref 给了没：", p._world_ref is not None)

print("\n=== 他会看到的经期 ===")
print("   _cycle() →", p._cycle())

print("\n=== 他会看到的整段健康文本 ===")
print(p.render(p.get_state()))
