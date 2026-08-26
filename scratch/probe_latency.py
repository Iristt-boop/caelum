"""「问这一幕」的时间到底花在哪。只读。

拆三段：抽帧 / 看图 / 剩下的。
还要看**思考占了多少** —— 这个视觉模型是会思考的（reasoning_tokens 295~1325），
如果思考占了大头，那关掉它就是最大的一笔提速。
"""
import base64
import sys
import time

sys.path.insert(0, "/root/co-watching")
import app  # noqa: E402
from openai import OpenAI  # noqa: E402

URL = "https://www.bilibili.com/video/av770415011"

t0 = time.time()
info = app._extract(URL)
sid = "lat"
app.sessions[sid] = {"id": sid, "info": info, "title": "", "duration": info.get("duration"),
                     "created": 0, "extracted_at": 0, "source_url": URL}
print(f"=== yt-dlp 解析     {time.time() - t0:5.1f}s（**只在导入时一次**，问问题时不重来）")

t = time.time()
direct = app._fresh_direct_url(sid)
print(f"=== 取直链          {time.time() - t:5.1f}s（90 秒内有缓存）")

for at in (120, 600):
    t = time.time()
    path = app._extract_frame(direct, at, f"{sid}{at}")
    ext = time.time() - t
    print(f"\n--- 第 {at} 秒 ---")
    print(f"    ffmpeg 抽帧    {ext:5.1f}s")
    if not path:
        print("    抽不出来，跳过")
        continue

    b64 = base64.b64encode(open(path, "rb").read()).decode()
    client = OpenAI(api_key=app._vision_key(), base_url=app.VISION_BASE, timeout=120)

    t = time.time()
    r = client.chat.completions.create(
        model=app.VISION_MODEL, max_tokens=app.VISION_MAX_TOKENS,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": app._VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
    )
    vis = time.time() - t
    u = r.usage
    think = getattr(u.completion_tokens_details, "reasoning_tokens", 0) or 0
    body = u.completion_tokens - think
    print(f"    看图           {vis:5.1f}s")
    print(f"      思考 {think:>4} token / 正文 {body:>4} token"
          f"  → 思考占 {think / max(u.completion_tokens, 1) * 100:.0f}%")
    print(f"    合计           {ext + vis:5.1f}s")

# 能不能让它少想一点？
print("\n=== 试试压短思考（max_tokens 不变，加一句「别想太久」）===")
b64 = base64.b64encode(open(path, "rb").read()).decode()
client = OpenAI(api_key=app._vision_key(), base_url=app.VISION_BASE, timeout=120)
t = time.time()
r = client.chat.completions.create(
    model=app.VISION_MODEL, max_tokens=app.VISION_MAX_TOKENS,
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "直接描述这张图，不要思考过程。\n" + app._VISION_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]}],
)
u = r.usage
think = getattr(u.completion_tokens_details, "reasoning_tokens", 0) or 0
print(f"    {time.time() - t:5.1f}s  思考 {think} token"
      f"  正文 {u.completion_tokens - think} token")
