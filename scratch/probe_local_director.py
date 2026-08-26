"""本地那条 director 通不通。只读，不碰真视频 ——
造一段「浏览器算出来的帧差」喂进去，看它认不认。
"""
import json
import urllib.request

BASE = "http://127.0.0.1:3200"
KEY = "probe-local-1"


def post(body):
    req = urllib.request.Request(
        f"{BASE}/api/director/local", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def samples(now_s, spike_at=None):
    """造一段滚动窗口：安静底噪，可选一个尖峰（模拟切镜头）。"""
    out = []
    t = now_s - 60
    while t < now_s:
        v = 60.0 if (spike_at and abs(t - spike_at) < 0.3) else 2.0
        out.append([round(t, 2), v])
        t += 0.5
    return out


print("=== 1. 样本太少 → 不该说")
print("  ", post({"key": KEY, "duration_s": 3600, "position_ms": 600_000,
                  "samples": [[1, 2], [1.5, 3]]}))

print("\n=== 2. 一段安静之后有尖峰 → 该说")
r = post({"key": KEY, "duration_s": 3600, "position_ms": 600_000,
          "samples": samples(600, spike_at=596)})
print("  ", {k: r[k] for k in ("speak", "reason", "at_ms", "events")})
if r.get("evidence"):
    print("   证据：", r["evidence"])

print("\n=== 3. 刚说完，紧接着再问 → 该被冷却挡住")
r2 = post({"key": KEY, "duration_s": 3600, "position_ms": 610_000,
           "samples": samples(610, spike_at=606)})
print("  ", {k: r2[k] for k in ("speak", "reason")})

print("\n=== 4. 开场热身（换一个 key，位置放在第 10 秒）")
r3 = post({"key": KEY + "-warm", "duration_s": 3600, "position_ms": 10_000,
           "samples": samples(10, spike_at=6)})
print("  ", {k: r3[k] for k in ("speak", "reason")})

print("\n=== 5. 她刚说过话 → 让她看")
r4 = post({"key": KEY + "-user", "duration_s": 3600, "position_ms": 600_000,
           "samples": samples(600, spike_at=596), "user_active": True})
print("  ", {k: r4[k] for k in ("speak", "reason")})
