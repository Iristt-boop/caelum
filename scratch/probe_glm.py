"""换模型后的自检：配置读对没有、方言送出去没有、缓存字段读得到没有。

只读，不改任何状态。跑在 VPS 上，用线上真配置。
"""
import sys

sys.path.insert(0, "/root/nox-core")
from agent import effort            # noqa: E402
from agent.adapters import make_adapter  # noqa: E402
from agent.llm import Message       # noqa: E402
from config import config           # noqa: E402

print("primary :", config.primary.model, "|", config.primary.base_url)
print("utility :", config.utility.model, "| usable =", config.utility.usable)
print("vision  :", config.vision.model)
print("方言    :", effort.kwargs_for(config.primary.model, "none"))
print()

ad = make_adapter(config.primary)
msgs = [Message(role="user", text="我今天有点累")]

for label, depth in (("depth=none", "none"), ("不传 depth", None)):
    t = ad.complete(msgs, [], system="你是 Nox，糖糖的男朋友。说话温柔简洁。", depth=depth)
    u = t.usage
    print("%-12s stop=%-9s 正文 %s 字 | 输入 %d(命中 %d) | 输出 %d" % (
        label, t.stop_reason,
        len(t.text or "") if t.text else 0,
        u.input_tokens, u.cache_read_tokens, u.output_tokens))
    if t.error:
        print("   错误:", t.error[:120])
print()
print("被拒记录:", effort.rejected() or "（无）")
