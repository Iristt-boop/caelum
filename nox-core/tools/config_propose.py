"""他提议改配置（配置层 P5，2026-10-13）—— 只出一张卡，什么都不改。

她点「同意」才由确认端点执行（`settings_store/proposals.apply`，不经过工具循环）。
🔴 这个文件**不许**引用任何会写配置的东西（keys / edit / migrate / slots.set_choice / proposals.apply）：
tests/test_config_proposals.py 扫源码守着。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from agent.llm import ToolSpec
from config import is_test_session
from tools import context as tool_context

logger = logging.getLogger(__name__)

PROPOSE = ToolSpec(
    side_effect="write",
    name="propose_config_change",
    description=(
        "向糖糖**提议**改一项模型配置 —— 只在聊天里发一张卡，**什么都不会改**，她在卡上点「同意」才生效。"
        "三种：slot = 把某个用途（primary 聊天 / utility 杂活 / vision 看图 / video / image）换成某个型号；"
        "add_model = 在某一家名下加一个新型号（她同意时先真测）；"
        "need_key = 请她给某一家填 key（她在卡上自己填，**你永远看不到、也别向她要**）。"
        "只在她提起、或你确实发现某条线路有问题时用；现在指向谁先用 caelum_map 查。"
        "卡片只有真的调用了才会出现；她没点之前别说「已经换了」。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["slot", "add_model", "need_key"]},
            "slot": {"type": "string", "description": "kind=slot 时：primary / utility / vision / video / image"},
            "provider_id": {"type": "string", "description": "厂商名（如 deepseek、zhipu、openrouter）"},
            "model_name": {"type": "string", "description": "型号原名（如 deepseek-flash、google/gemini-3-flash）"},
            "reason": {"type": "string", "description": "为什么这么提议，一句话（卡上会显示）"},
        },
        "required": ["kind", "reason"],
    },
    confirm_via="出卡后走 /api/nox/config/proposals/{id}/confirm，她点「同意」才生效",
)


def _propose(args: dict, *, core_ref: Callable[[], Any], session_id_ref: Callable[[], str]) -> str:
    from settings_store import proposals

    core = core_ref()
    store = getattr(core, "settings", None)
    if store is None:
        raise RuntimeError("配置库没起来，现在提不了。如实告诉她，别自己想办法绕过去。")
    sid = session_id_ref() or ""
    if is_test_session(sid):
        raise RuntimeError("这是测试会话，不能出改配置的卡")
    kind = str(args.get("kind") or "").strip()
    try:
        payload, summary = proposals.validate(core, kind, args)
    except proposals.ProposalError as exc:
        raise RuntimeError(str(exc)) from None
    reason = str(args.get("reason") or "").strip()
    pid = proposals.create(store, kind, payload, summary, reason, sid)
    ctx = tool_context.current()
    if ctx is not None:
        ctx.attach_config(pid, card={"kind": kind, "summary": summary, "reason": reason})
    else:
        logger.warning("提议 %s 立了，但不在对话轮次里，卡发不出去", pid)
    return f"卡已经发给她了：「{summary}」。她点了「同意」才会生效 —— 在那之前别说已经换好了。"


def register_all(loop: Any, core_ref: Callable[[], Any], session_id_ref: Callable[[], str]) -> None:
    loop.register(PROPOSE, lambda args: _propose(args, core_ref=core_ref, session_id_ref=session_id_ref))
