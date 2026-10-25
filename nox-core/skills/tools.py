"""`skill_read`：翻一个 skill 的全文（只读）。

命中的短 skill 已经整篇递进这一轮了（skills/render.py）；这个工具给两种情况：
长的只递了一句话，或者没对上触发词、他自己想起来要用。名字不对不抛，把目录递回去（照 guide_read）。

登记只走 `loop.register`，重名不许静默覆盖（同 guide/tools.py 的理由）。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools import context as tool_context

logger = logging.getLogger(__name__)

SKILL_READ_SPEC = ToolSpec(
    side_effect="none",
    name="skill_read",
    description=(
        "翻一个装着的做法（skill）的全文。系统提示里「装着的做法」那行列着有哪些；"
        "这一轮递给你的只有一句话、或者你觉得某个做法用得上但没递给你时，先翻再照着做。"
        "做法只管怎么做，不给你新的权限：要花钱、发东西、改东西，照样走原来的工具和确认卡。"
    ),
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "做法的名字，例如 xhs-topics"}},
        "required": ["name"],
    },
)


def read(store, name: object) -> str:
    key = name.strip() if isinstance(name, str) else ""
    sk = store.get(key) if key else None
    if sk is not None and sk["enabled"]:
        store.record(key, "read")
        return f"{sk['name']}：{sk['description']}\n\n{sk['body']}"
    on = [s["name"] for s in store.all(enabled_only=True)]
    return f"没有叫「{key or name!r}」的做法（或者她关掉了）。装着的：" + ("、".join(on) if on else "（一个都没有）")


def register_all(loop, store) -> None:
    if SKILL_READ_SPEC.name in loop.tools:
        raise ValueError(f"工具 {SKILL_READ_SPEC.name!r} 已经登记过了，不许静默覆盖")

    def handler(args: dict) -> str:
        key = args.get("name")
        out = read(store, key)
        logger.info("skill_read name=%s hit=%s session=%s",
                    key, not out.startswith("没有叫"), tool_context.session_id())
        return out

    loop.register(SKILL_READ_SPEC, handler)
