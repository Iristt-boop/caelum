"""他在对话里看到的周期 vs World Model 里的真相。只读。"""
import json
import sqlite3
import urllib.request
from datetime import date

print("=== 他现在在对话里看到的（health-mcp 的 get_menstrual_cycle）===")
try:
    req = urllib.request.Request(
        "http://127.0.0.1:8101/mcp",
        data=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_menstrual_cycle", "arguments": {}},
        }).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        print("  ", r.read().decode()[:500])
except Exception as e:
    print("   打不通：", e, "\n   （直接看它读的那张表）")

print("\n=== health.db 的 menstrual（health-mcp 读的就是它）===")
con = sqlite3.connect("file:/root/data/health.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
for r in con.execute("SELECT date, day_number, cycle_start FROM menstrual ORDER BY date DESC LIMIT 3"):
    print("  ", dict(r))
con.close()

print("\n=== World Model（App 和 record_period 写的地方）===")
con = sqlite3.connect("file:/root/nox-core/data/world.db?mode=ro", uri=True)
starts = []
for (o,) in con.execute("SELECT observed FROM observations WHERE type='menstrual'"):
    d = json.loads(o)
    if d.get("event") == "start":
        starts.append(d["date"])
con.close()
starts.sort()
print("   start：", starts)
if starts:
    last = date.fromisoformat(starts[-1])
    print(f"   今天是周期第 {(date.today() - last).days + 1} 天")
    if len(starts) >= 2:
        prev = date.fromisoformat(starts[-2])
        print(f"   上个周期 {(last - prev).days} 天")
