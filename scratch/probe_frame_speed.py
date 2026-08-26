"""关掉思考之后，「问这一幕」端到端要多久。走真的 /api/frame。只读。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:3200"
URL = "https://www.bilibili.com/video/av770415011"


def post(path, body, timeout=180):
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


sid = post("/api/import", {"url": URL})["session_id"]
print("=== 走真的 /api/frame 端点（抽帧 + 看图，端到端）")

times = []
for t in (240, 480, 900):
    s = time.time()
    with urllib.request.urlopen(f"{BASE}/api/frame/{sid}?t={t}", timeout=180) as r:
        d = json.load(r)
    took = time.time() - s
    times.append(took)
    n = len(d.get("description") or "")
    bad = "" if n else f"  ✗ 空的！note={d.get('note')}"
    print(f"    第 {t:>4} 秒：{took:5.1f}s   描述 {n:>3} 字{bad}")

print(f"\n=== 平均 {sum(times)/len(times):.1f}s（改之前实测 12~15s）")
print("=== 描述样本（确认没因为不思考而变差）")
print("   ", (d.get("description") or "")[:200].replace("\n", " "))
