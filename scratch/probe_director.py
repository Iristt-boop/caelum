"""真数据下的控制器决定分布。

模拟从头播到尾（每 2 秒 tick 一次），看：
  · 他一共想说几次
  · **每次不说的理由分别占多少** ← 这个最重要

理由分布能看出抑制器是不是有一条把所有东西都吞了。
比如 90% 都是「有台词」，那说明这片子台词太密，得调 DIALOGUE_PAD。
"""
import collections
import json
import re
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:3200"
URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.bilibili.com/video/BV1G48M6XEBt"


def post(path, body=None, timeout=180):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get(path, timeout=60):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
        return json.load(r)


d = post("/api/import", {"url": URL})
sid, dur = d["session_id"], d["duration"]
print(f"=== {d['title']} | {dur:.0f} 秒")

print("=== 跑视觉分析…")
post(f"/api/analyze/{sid}")
for _ in range(120):
    time.sleep(5)
    st = get(f"/api/analyze/{sid}")
    if not st.get("running"):
        break
print("   ", st["status"], f'{st["percent"]}%', "| 门槛", st.get("stats"))

ev = get(f"/api/observations/{sid}")["items"]
mins = dur / 60
print(f"    事件 {len(ev)} 个 → {len(ev)/mins:.1f} 次/分")

print("\n=== 从头 tick 到尾（每 2 秒）")
reasons = collections.Counter()
spoke = []
for pos in range(0, int(dur * 1000), 2000):
    r = post(f"/api/director/{sid}", {"position_ms": pos}, timeout=180)
    if r["speak"]:
        spoke.append(pos)
        ev_ = r.get("evidence") or {}
        print(f"    ★ {pos//60000:02d}:{pos//1000%60:02d} 开口 —— {r['reason']}")
        print(f"       画面：{(ev_.get('frame') or ev_.get('frame_note') or '')[:70]}")
    else:
        # 归并成大类：**按去掉数字来归**，别写模式列表 ——
        # 第一版漏了新加的「才开场 N 秒」，62/64/66 秒各算一种，把汇总淹了
        key = re.sub(r"\d+", "N", r["reason"])
        reasons[key] += 1

print(f"\n=== 结果：{int(dur//60)} 分钟的片子，开口 {len(spoke)} 次")
total = sum(reasons.values()) + len(spoke)
print("=== 没开口的理由分布")
for why, n in reasons.most_common():
    print(f"    {n:>4} 次 ({n/total*100:>5.1f}%)  {why}")
