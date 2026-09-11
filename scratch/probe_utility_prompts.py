"""量一下各条 utility 路径的 system 提示词有多长。

GLM 的隐式缓存最小前缀是 **512 token**。短于这个数的提示词
**永远不可能命中缓存** —— 而 utility 一天跑 166+ 次。
"""
import sys

sys.path.insert(0, "/root/nox-core")


def toks(s: str) -> int:
    """粗估 token：中文按字算，英文按 4 字符算。够用来判断在不在 512 附近。"""
    zh = sum(1 for c in s if "一" <= c <= "鿿")
    return zh + (len(s) - zh) // 4


rows = []

try:
    from context import compactor
    for name in ("SUMMARY_PROMPT", "PROMPT", "_PROMPT", "SYSTEM"):
        v = getattr(compactor, name, None)
        if isinstance(v, str) and len(v) > 50:
            rows.append(("压缩 " + name, v))
            break
except Exception as e:
    rows.append(("压缩（读不到）", str(e)))

try:
    from attention import appraisal_llm
    rows.append(("意义推断 _PROMPT", appraisal_llm._PROMPT))
except Exception as e:
    rows.append(("意义推断（读不到）", str(e)))

try:
    from topic_pool import filter as tfilter
    for name in dir(tfilter):
        v = getattr(tfilter, name)
        if isinstance(v, str) and len(v) > 200 and name.isupper():
            rows.append(("话题过滤 " + name, v))
            break
except Exception as e:
    rows.append(("话题过滤（读不到）", str(e)))

try:
    from attention import speaker
    for name in dir(speaker):
        v = getattr(speaker, name)
        if isinstance(v, str) and len(v) > 200 and name.isupper():
            rows.append(("Care 开口 " + name, v))
            break
except Exception as e:
    rows.append(("Care 开口（读不到）", str(e)))

try:
    from personality.prompt import LIGHT_PERSONA
    rows.append(("轻量人设 LIGHT_PERSONA", LIGHT_PERSONA))
except Exception:
    try:
        from router.router import LIGHT_PERSONA
        rows.append(("轻量人设 LIGHT_PERSONA", LIGHT_PERSONA))
    except Exception as e:
        rows.append(("轻量人设（读不到）", str(e)))

print("GLM 隐式缓存最小前缀 = 512 token，低于它的永远不命中\n")
print("%-26s %8s %8s   %s" % ("路径", "字符", "≈token", "能缓存吗"))
print("-" * 62)
for name, s in rows:
    if not isinstance(s, str):
        continue
    t = toks(s)
    mark = "✅ 够" if t >= 512 else "❌ 太短，永不命中"
    print("%-26s %8d %8d   %s" % (name, len(s), t, mark))
