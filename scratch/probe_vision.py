"""vision 返回 200 但描述是空的 —— 看看响应里到底有什么。只读。"""
import base64
import glob
import sys

sys.path.insert(0, "/root/co-watching")
import app  # noqa: E402
from openai import OpenAI  # noqa: E402

frames = sorted(glob.glob("/tmp/watching-frames/*.jpg"))
if not frames:
    print("没有现成的帧，先跑一次 /api/frame")
    raise SystemExit(1)
path = frames[-1]
print("用这张帧：", path)

key = app._vision_key()
print("key 长度：", len(key))

client = OpenAI(api_key=key, base_url=app.VISION_BASE, timeout=90)
b64 = base64.b64encode(open(path, "rb").read()).decode()

r = client.chat.completions.create(
    model=app.VISION_MODEL,
    max_tokens=600,
    messages=[{"role": "user", "content": [
        {"type": "text", "text": app._VISION_PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]}],
)

print("\n=== 响应 ===")
print("  model      =", r.model)
ch = r.choices[0]
print("  finish     =", ch.finish_reason)
msg = ch.message
print("  content    =", repr(msg.content)[:300])
for extra in ("reasoning_content", "reasoning"):
    if hasattr(msg, extra):
        print(f"  {extra:<10} =", repr(getattr(msg, extra))[:300])
print("  用量       =", r.usage)
print("\n=== message 上都有什么字段 ===")
print(" ", [k for k in msg.model_dump().keys()])
