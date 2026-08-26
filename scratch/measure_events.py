"""量一部片子分析完的事件密度。只读。

这是 P2 唯一还没验的那件事：**阈值在真实素材上合不合理。**
纯逻辑测试证明不了 —— 得看真实的每分钟事件数。
"""
import json
import sys
import urllib.request

sid = sys.argv[1]
base = "http://127.0.0.1:3200"

st = json.load(urllib.request.urlopen(f"{base}/api/analyze/{sid}", timeout=30))
print("=== 分析状态 ===")
print("   ", st["status"], f'{st["percent"]}%',
      f'完成到 {st["completed_through_ms"]/1000:.0f} 秒')
print("    分布：", st.get("stats"))

d = json.load(urllib.request.urlopen(f"{base}/api/observations/{sid}", timeout=30))
items = d["items"]
mins = d["to_ms"] / 60000
cuts = [e for e in items if e["kind"] == "scene_change"]
peaks = [e for e in items if e["kind"] == "motion_peak"]

print(f"\n=== 密度（片长 {mins:.1f} 分钟）===")
print(f"    事件共 {len(items)} 个")
print(f"    切镜头 {len(cuts):>3} → 每分钟 {len(cuts)/mins:.1f} 次")
print(f"    运动峰 {len(peaks):>3} → 每分钟 {len(peaks)/mins:.1f} 次")

print("\n=== 前 10 个 ===")
for e in items[:10]:
    t = e["at"] / 1000
    v = e.get("sceneScore") or e.get("motionScore")
    kind = e["kind"]
    raw = e["raw"]
    print(f"    {int(t//60):02d}:{t%60:05.2f}  {kind:<13} 归一 {v}  原始 {raw}")

gaps = sorted(items[i + 1]["at"] - items[i]["at"] for i in range(len(items) - 1))
if gaps:
    print(f"\n=== 间隔 ===")
    print(f"    最小 {gaps[0]/1000:.1f}s  中位 {gaps[len(gaps)//2]/1000:.1f}s  "
          f"最大 {gaps[-1]/1000:.1f}s")
    print(f"    间隔 < 5 秒的：{sum(1 for g in gaps if g < 5000)} 对")

# ⚠️ 检查原始分数有没有漏出去 —— 架构 14.4 说它该留在服务端
leaked = [k for e in items for k in e if k in ("p50", "p90", "stats", "scores")]
print(f"\n=== 原始遥测有没有漏出去：{'漏了！' + str(leaked) if leaked else '没有 ✓'}")
