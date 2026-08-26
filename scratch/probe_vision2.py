"""同一张图跑 4 次，看 content 空不空是不是随机的。

怀疑：这是个会「思考」的模型，reasoning 和 content 共用 max_tokens。
思考长了就把额度吃光，content 变成空 —— 而 `_describe_frame` 把空当 None 静默返回。
"""
import base64
import sys

sys.path.insert(0, "/root/co-watching")
import app  # noqa: E402
from openai import OpenAI  # noqa: E402

path = "/tmp/watching-frames/af88d380dbb8_30.jpg"
b64 = base64.b64encode(open(path, "rb").read()).decode()
client = OpenAI(api_key=app._vision_key(), base_url=app.VISION_BASE, timeout=90)

for cap in (600, 600, 1500, 1500):
    r = client.chat.completions.create(
        model=app.VISION_MODEL,
        max_tokens=cap,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": app._VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
    )
    ch = r.choices[0]
    u = r.usage
    reasoning = getattr(u.completion_tokens_details, "reasoning_tokens", None)
    content = ch.message.content or ""
    print(f"max_tokens={cap:<5} finish={ch.finish_reason:<8} "
          f"completion={u.completion_tokens:<5} reasoning={reasoning:<5} "
          f"content={len(content):<5} → {'空的！' if not content.strip() else content[:40]}")
