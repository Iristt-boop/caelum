"""探一下：不装 OpenCV，只用 ffmpeg 的 scdet 能不能拿到我们要的信号？

要回答三件事：
  1. metadata 输出长什么样（好写解析）
  2. scdet 的 score 在真实片子上是什么量级（好定阈值）
  3. **2 核的机器上，分析 1 分钟视频要多久** —— 这决定 P2 可不可行

只读。跑完删掉自己。
"""
import re
import subprocess
import sys
import time

sys.path.insert(0, "/root/co-watching")

import app  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.bilibili.com/video/BV1G48M6XEBt"
START = 30
DUR = 60
FPS = 2

print("=== 解析直链 ===")
info = app._extract(URL)
print("   ", info.get("title"), "|", info.get("duration"), "秒")
sid = "probe"
app.sessions[sid] = {"id": sid, "title": info.get("title"), "duration": info.get("duration"),
                     "info": info, "created": time.time(), "extracted_at": time.time()}
direct = app._fresh_direct_url(sid)
print("    直链拿到了，长度", len(direct))

headers = "Referer: https://www.bilibili.com/\r\nUser-Agent: Mozilla/5.0\r\n"
cmd = [
    "ffmpeg", "-hide_banner", "-nostdin",
    "-ss", str(START), "-t", str(DUR),
    "-headers", headers, "-i", direct,
    "-an",
    "-vf", f"fps={FPS},scale=160:-2,scdet=threshold=0,metadata=print:file=-",
    "-f", "null", "-",
]

print(f"\n=== 跑 ffmpeg（{DUR} 秒素材，{FPS} fps，缩到 160 宽）===")
t0 = time.time()
r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
elapsed = time.time() - t0
print(f"    耗时 {elapsed:.1f} 秒，returncode={r.returncode}")
print(f"    实时倍数 = {DUR / elapsed:.1f}x（>1 就是比看片快）")

out = r.stdout or ""
print("\n=== stdout 前 12 行（看格式）===")
for line in out.splitlines()[:12]:
    print("   ", line)

scores = [float(m) for m in re.findall(r"lavfi\.scd\.score=([\d.]+)", out)]
print(f"\n=== score 分布（共 {len(scores)} 帧）===")
if scores:
    s = sorted(scores)
    def pct(p):
        return s[min(len(s) - 1, int(len(s) * p))]
    print(f"    min={s[0]:.2f}  p50={pct(.5):.2f}  p90={pct(.9):.2f} "
          f"p99={pct(.99):.2f}  max={s[-1]:.2f}")
    for th in (5, 10, 20, 30, 50):
        n = sum(1 for x in scores if x >= th)
        print(f"    >= {th:>2}：{n:>3} 帧（{n / len(scores) * 100:.1f}%）"
              f" ≈ 每分钟 {n / (DUR / 60):.0f} 次")
else:
    print("    一个都没解析出来 —— 看看 stderr：")
    print(r.stderr[-800:])
