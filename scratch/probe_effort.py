"""探一下当前主模型对 reasoning_effort 的真实反应。只读，不改任何状态。"""
import sys

sys.path.insert(0, "/root/nox-core")
from config import config          # noqa: E402
from openai import OpenAI          # noqa: E402

c = OpenAI(api_key=config.primary.api_key, base_url=config.primary.base_url)
msgs = [{"role": "system", "content": "你是 Nox。"},
        {"role": "user", "content": "我今天有点累"}]

print("模型:", config.primary.model, "|", config.primary.base_url)
for label, kw in (("默认（不传）", {}),
                  ("effort=none", {"reasoning_effort": "none"}),
                  ("effort=low", {"reasoning_effort": "low"})):
    try:
        r = c.chat.completions.create(model=config.primary.model, messages=msgs,
                                      max_tokens=300, **kw)
        u = r.usage
        det = getattr(u, "completion_tokens_details", None)
        rt = getattr(det, "reasoning_tokens", 0) if det else 0
        print("  %-14s 正文 %3d 字 | 输出 %3d tok | reasoning %s"
              % (label, len(r.choices[0].message.content or ""), u.completion_tokens, rt))
    except Exception as e:
        print("  %-14s 拒绝: %s" % (label, str(e)[:90]))
