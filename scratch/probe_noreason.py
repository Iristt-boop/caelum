"""能不能让这个视觉模型少思考？看图 12~15 秒里 78% 是思考。

试三条：
  1. reasoning_effort 参数（OpenAI 那套）
  2. thinking / enable_thinking（各家自定义的那套）
  3. 换一个不思考的视觉模型名
"""
import base64
import sys
import time

sys.path.insert(0, "/root/co-watching")
import app  # noqa: E402
from openai import OpenAI  # noqa: E402

import glob
path = sorted(glob.glob("/tmp/watching-frames/*.jpg"))[0]
b64 = base64.b64encode(open(path, "rb").read()).decode()
client = OpenAI(api_key=app._vision_key(), base_url=app.VISION_BASE, timeout=120)

MSG = [{"role": "user", "content": [
    {"type": "text", "text": app._VISION_PROMPT},
    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
]}]


def run(label, **kw):
    t = time.time()
    try:
        r = client.chat.completions.create(
            model=kw.pop("model", app.VISION_MODEL),
            max_tokens=2400, messages=MSG, **kw)
    except Exception as exc:
        print(f"  {label:<34} ✗ {type(exc).__name__}: {str(exc)[:90]}")
        return
    u = r.usage
    think = getattr(u.completion_tokens_details, "reasoning_tokens", 0) or 0
    body = (r.choices[0].message.content or "").strip()
    print(f"  {label:<34} {time.time()-t:5.1f}s  思考 {think:>5}  正文 {len(body):>4} 字"
          f"  {'✓' if body else '✗ 空'}")


print("=== 基线 ===")
run("默认")

print("\n=== reasoning_effort ===")
for lvl in ("minimal", "low", "none"):
    run(f"reasoning_effort={lvl}", reasoning_effort=lvl)

print("\n=== 各家自定义参数 ===")
run("extra_body thinking=disabled",
    extra_body={"thinking": {"type": "disabled"}})
run("extra_body enable_thinking=False",
    extra_body={"enable_thinking": False})

print("\n=== 换个模型名试试 ===")
for m in ("deepseek-v4-flash-vision", "deepseek-vl2", "deepseek-v4-vision"):
    run(f"model={m}", model=m)
