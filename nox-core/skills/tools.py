"""`skill_read`：翻一个 skill 的全文（只读）；`skill_propose`：提议装 / 改一个（只出卡，P3）。

命中的短 skill 已经整篇递进这一轮了（skills/render.py）；skill_read 给两种情况：
长的只递了一句话，或者没对上触发词、他自己想起来要用。名字不对不抛，把目录递回去（照 guide_read）。

skill_propose 只写一条提议 + 往聊天里发一张卡，**什么都不装**；她在卡上点「装上」才由确认端点装
（`POST /api/nox/skills/proposals/{id}/confirm`，不经过他的工具循环）。
🔴 这个文件**不许**引用会真装的东西（install / apply_proposal / set_enabled）：tests/test_skills.py 扫源码守着。
理由：做法每轮都可能进他的上下文，等于往他脑子里写指令 —— 谁能往里写，要卡死在她手上。

登记只走 `loop.register`，重名不许静默覆盖（同 guide/tools.py 的理由）。
"""

from __future__ import annotations

import logging

import yaml

from agent.llm import ToolSpec
from config import is_test_session
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


SKILL_PROPOSE_SPEC = ToolSpec(
    side_effect="write",
    name="skill_propose",
    description=(
        "向糖糖**提议**装一个新的做法（skill），或者改一个装着的 —— 只在聊天里发一张卡，**什么都不会装**，"
        "她在卡上点「装上」才生效。什么时候提：她教过你一种做法、说过「以后都这样」；"
        "你发现自己反复在做同一类事、值得记成固定做法；或者她想要某种本事，你在网上找到了合适的 skill（照她的情况改写过再提，"
        "source 填原文地址）。改装着的：name 用原来的名字，写完整的新版本（先 skill_read 看现在的）。"
        "做法只写「怎么做」，不写「你是谁」，也给不了你新权限。一次一张，别刷卡；她没点之前别说「已经学会了」。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "小写字母、数字和 -，如 movie-after-talk；改装着的就用原名"},
            "description": {"type": "string", "description": "一句话：做什么、什么时候用"},
            "when": {"type": "string", "description": "触发词，逗号隔开，每个至少两个字（她会怎么说起这件事）"},
            "body": {"type": "string", "description": "做法正文（markdown，建议 1500 字以内）"},
            "reason": {"type": "string", "description": "为什么提这个，一句话（卡上会显示）"},
            "source": {"type": "string", "description": "可选：从网上找来改写的，原文地址"},
        },
        "required": ["name", "description", "body", "reason"],
    },
    confirm_via="出卡后走 /api/nox/skills/proposals/{id}/confirm，她点「装上」才生效",
)


def compose(args: dict) -> str:
    """把他给的几样拼成一份 SKILL.md。文件头走 yaml.safe_dump —— description 里有冒号也不会把头弄坏。"""
    head = {"name": str(args.get("name") or "").strip(), "description": str(args.get("description") or "").strip()}
    when = str(args.get("when") or "").strip()
    if when:
        head["when"] = when
    return "---\n" + yaml.safe_dump(head, allow_unicode=True, sort_keys=False, width=10_000) + "---\n" + \
        str(args.get("body") or "").strip() + "\n"


def propose(store, args: dict, session_id: str = "") -> str:
    from skills.store import ProposalError

    if is_test_session(session_id):
        raise RuntimeError("这是测试会话，不能出装做法的卡")
    reason = str(args.get("reason") or "").strip()
    if not reason:
        raise RuntimeError("reason 要写一句为什么提（卡上给她看）")
    try:
        p = store.propose(compose(args), reason=reason, source=str(args.get("source") or ""), session_id=session_id)
    except ProposalError as exc:
        raise RuntimeError(str(exc)) from None
    ctx = tool_context.current()
    card = {"action": p["action"], "name": p["name"], "description": p["description"], "reason": p["reason"],
            "source": p.get("source") or ""}
    if ctx is not None:
        ctx.attach_skill(p["id"], card=card)
    else:
        logger.warning("skill 提议 %s 立了，但不在对话轮次里，卡发不出去", p["id"])
    verb = "改「%s」" % p["name"] if p["action"] == "update" else "装「%s」" % p["name"]
    return f"卡已经发给她了：{verb}。她点了「装上」才生效 —— 在那之前别说已经学会了。"


def read(store, name: object) -> str:
    key = name.strip() if isinstance(name, str) else ""
    sk = store.get(key) if key else None
    if sk is not None and sk["enabled"]:
        store.record(key, "read")
        return f"{sk['name']}：{sk['description']}\n\n{sk['body']}"
    on = [s["name"] for s in store.all(enabled_only=True)]
    return f"没有叫「{key or name!r}」的做法（或者她关掉了）。装着的：" + ("、".join(on) if on else "（一个都没有）")


def register_all(loop, store) -> None:
    for spec in (SKILL_READ_SPEC, SKILL_PROPOSE_SPEC):
        if spec.name in loop.tools:
            raise ValueError(f"工具 {spec.name!r} 已经登记过了，不许静默覆盖")

    def handler(args: dict) -> str:
        key = args.get("name")
        out = read(store, key)
        logger.info("skill_read name=%s hit=%s session=%s",
                    key, not out.startswith("没有叫"), tool_context.session_id())
        return out

    def propose_handler(args: dict) -> str:
        sid = tool_context.session_id() or ""
        out = propose(store, args, sid)
        logger.info("skill_propose name=%s session=%s", args.get("name"), sid)
        return out

    loop.register(SKILL_READ_SPEC, handler)
    loop.register(SKILL_PROPOSE_SPEC, propose_handler)
