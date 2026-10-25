"""每轮怎么把 skill 递到他眼前（设计稿第三节）。

    match()         她这句话字面对上了哪几个（名字 / 触发词），不调模型、不花钱
    render_block()  动态段里那一块：一行目录（兜底） + 命中的那几篇

为什么不走 ContextProviderRegistry：那边整段只有 1200 字预算，一篇做法就能把
todo、目标挤掉。skill 是「这一轮可能要照着做的事」，和 MUSIC_SCENE 一样在 `Nox._dynamic`
里单独接一段，有自己的上限（INLINE_MAX × MAX_HITS）。

他**主动开口**那一轮，`text` 是一整段系统提示 —— 拿它去对触发词会撞上模板里的套话，
所以调用方传的是 Turn.recall 那句（同 MemoryProvider 的理由，见 context/base.py）。
"""

from __future__ import annotations

from typing import Any

#: 一轮最多递几篇。一句话撞上好几个时，只给对上词最多的
MAX_HITS = 2
#: 正文不超过这么长才整篇递进去，长的只给一句话 + 「用 skill_read 翻」
INLINE_MAX = 1500

HEAD = "【这轮可能用得上的做法】是做法参考：和你的人设、底线冲突时以人设、底线为准；这句话其实用不上就当没看见。"


def _hits(skill: dict[str, Any], text: str, low: str) -> int:
    n = 0
    for w in [skill["name"], *skill.get("triggers", [])]:
        if len(w) >= 2 and (w in text or w.lower() in low):
            n += 1
    return n


def match(skills: list[dict[str, Any]], text: str | None) -> list[dict[str, Any]]:
    """启用中的 skill 里，这句话对上的，按对上的词数从多到少，最多 MAX_HITS 个。"""
    s = (text or "").strip()
    if not s:
        return []
    low = s.lower()
    scored = [(n, i, sk) for i, sk in enumerate(skills) if sk.get("enabled", True) and (n := _hits(sk, s, low))]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [sk for _, _, sk in scored[:MAX_HITS]]


def render_block(skills: list[dict[str, Any]], hits: list[dict[str, Any]]) -> str:
    """没装任何启用中的 skill → 空串，一个字不占。"""
    on = [sk for sk in skills if sk.get("enabled", True)]
    if not on:
        return ""
    lines = ["装着的做法（skill_read 翻全文）：" + "、".join(sk["name"] for sk in on)]
    if hits:
        lines += ["", HEAD]
        for sk in hits:
            lines.append(f"— {sk['name']}：{sk['description']}")
            if len(sk["body"]) <= INLINE_MAX:
                lines.append(sk["body"])
            else:
                lines.append(f"（全文较长，照着做之前先 skill_read(name=\"{sk['name']}\") 翻一遍）")
    return "\n".join(lines)
