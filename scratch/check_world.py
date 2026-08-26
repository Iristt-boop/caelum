"""World Model 里现在有什么。只读。"""
import json
import sqlite3

con = sqlite3.connect("file:/root/nox-core/data/world.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row

print("=== 事实类型 ===")
for r in con.execute(
    "SELECT type, COUNT(*) n, MIN(observed_at) a, MAX(observed_at) b "
    "FROM observations GROUP BY type ORDER BY n DESC"
):
    print(f"  {r['type']:16} {r['n']:4} 条   {str(r['a'])[:10]} → {str(r['b'])[:10]}")

print("\n=== 经期（按时间正序）===")
for r in con.execute(
    "SELECT observed_at, observed FROM observations "
    "WHERE type='menstrual' ORDER BY observed_at"
):
    o = json.loads(r["observed"])
    bits = [o.get("event", ""), o.get("flow", "")]
    if o.get("note"):
        bits.append(o["note"])
    if o.get("demoted_from"):
        bits.append(f"（原本记成 {o['demoted_from']}，已降级）")
    print(f"  {str(r['observed_at'])[:10]}  " + " · ".join(b for b in bits if b)
          + f"   ← {o.get('origin', '?')}")

print("\n=== 周期长度（只数 start）===")
starts = []
for r in con.execute(
    "SELECT observed_at, observed FROM observations "
    "WHERE type='menstrual' ORDER BY observed_at"
):
    if json.loads(r["observed"]).get("event") == "start":
        starts.append(str(r["observed_at"])[:10])
print("  开始日：", starts)
if len(starts) >= 2:
    from datetime import date
    a = date.fromisoformat(starts[-2])
    b = date.fromisoformat(starts[-1])
    print(f"  上一个周期：{(b - a).days} 天")
con.close()
