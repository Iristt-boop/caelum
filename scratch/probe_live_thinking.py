"""线上此刻到底思不思考 —— 按主聊天真实走的那条路量。

AgentLoop 的默认是 depth="low"（agent/loop.py:90），所以这里就传 low。
"""
import sys

sys.path.insert(0, "/root/nox-core")
from agent import effort            # noqa: E402
from config import config           # noqa: E402
from openai import OpenAI           # noqa: E402

m = config.primary.model
print("主模型:", m)
print("loop 默认 depth = low  →  实际发出去的是:", effort.kwargs_for(m, "low") or "（什么都不发）")
print()

c = OpenAI(api_key=config.primary.api_key, base_url=config.primary.base_url)
msgs = [{"role": "system", "content": "你是 Nox，糖糖的男朋友。说话温柔简洁。"},
        {"role": "user", "content": "我今天有点累"}]

for label, kw in (("主聊天实际（depth=low）", effort.kwargs_for(m, "low")),
                  ("对照：什么都不发", {})):
    r = c.chat.completions.create(model=m, messages=msgs, max_tokens=600, **kw)
    u = r.usage
    det = getattr(u, "completion_tokens_details", None)
    rt = getattr(det, "reasoning_tokens", 0) if det else 0
    pct = 100.0 * rt / max(1, u.completion_tokens)
    print("%-24s 正文 %3d 字 | 输出 %3d tok | reasoning %3d (%.0f%%)"
          % (label, len(r.choices[0].message.content or ""), u.completion_tokens, rt, pct))
