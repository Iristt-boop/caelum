"""长任务的对话接口 —— 立任务 / 看进度 / 停。

三个工具，`agent/tasks.py` 是它们背后的实体和 runner。这里只做
「把模型的意图翻译成对 TaskStore 的操作」。

🔴 **start_long_task 永远不执行任务**（R8）：它只落一条 proposed +
发一张确认卡，真跑在她点「跑」之后的确认端点（`/api/nox/tasks/{id}/confirm`）
→ runner 手里。模型物理上够不到 running —— 长任务的破坏力是累积性的
（批量改 481 条数据），和瑞幸下单是同一条纪律。

加工具三处同步（SPEC / handler / register_all 的表）—— 2026-09-22
delete_todo 漏了第三处，服务崩循环被管线回滚（PROJECT.md 53.2）。
`tests/test_tasks.py` 有守卫盯着这三个名字真的注册上了。
"""

from __future__ import annotations

import logging

from agent import tasks as task_mod
from agent.llm import ToolSpec
from config import is_test_session
from tools import context as tool_context

logger = logging.getLogger(__name__)


START = ToolSpec(
    side_effect="write",
    name="start_long_task",
    description=(
        "立一个**长任务**：要干很久、活过一条回复的活。"
        "比如「把记忆里的 481 本书理一遍」「把这周的日记归档」"
        "「把购物清单和历史订单对一遍」。"
        "🔴 它不会立刻开跑 —— 只发一张确认卡给糖糖，她点了「跑」"
        "才在后台执行（并发一次一个，进度随时可用 task_status 查）。"
        "**卡片只有真的调用了这个工具才会出现**；没调用就别说"
        "「已经在弄了」—— 那是骗她。"
        "一句话能答完、一两个工具就够的小事**不要**用它。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "她要的是什么，一句话（任务卡上就这么显示）",
            },
            "steps_hint": {
                "type": "string",
                "description": "怎么做的大致提示（她提过的话或你打算的顺序），可选",
            },
        },
        "required": ["goal"],
    },
    confirm_via="出卡后走 /api/nox/tasks/{id}/confirm，她点「跑」才真的开跑",
)

STATUS = ToolSpec(
    side_effect="read",
    name="task_status",
    description=(
        "看长任务干到哪了：状态 + 最近的进度步骤 + 结果。"
        "不带 task_id 看最近的一个。她问「弄完了吗」「跑到哪了」时用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 id，不带就看最近的"},
        },
    },
)

CANCEL = ToolSpec(
    side_effect="write",
    name="task_cancel",
    description=(
        "停一个长任务。她在对话里说「别弄了」「停下来」时用 —— "
        "running 的任务是优雅停（手头的工具做完就收）；还没开的直接取消。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 id"},
        },
        "required": ["task_id"],
    },
)

_SPECS = (START, STATUS, CANCEL)

#: 状态 → 给模型看的话（他转述给她时要能说清「现在到底在不在跑」）
_STATE_TEXT = {
    task_mod.PROPOSED: "等她确认（还没开始）",
    task_mod.CONFIRMED: "排队中，马上开跑",
    task_mod.RUNNING: "正在后台跑",
    task_mod.DONE: "已完成",
    task_mod.FAILED: "没跑成",
    task_mod.CANCELLED: "已取消",
    task_mod.INTERRUPTED: "中途断了（服务重启），可以让她点「接着跑」",
}


def _store(store_ref):
    store = store_ref() if callable(store_ref) else store_ref
    if store is None:
        # 🔴 fail-closed（同 luckin）：链路没接上就拒绝，绝不「那就直接跑吧」
        raise RuntimeError(
            "长任务链路没接上（tasks store 缺失），现在立不了任务。"
            "如实告诉她，别自己想办法绕过去。"
        )
    return store


def _brief(row: dict) -> str:
    state = _STATE_TEXT.get(row["status"], row["status"])
    out = [f"任务 {row['id']}「{row['goal'][:80]}」：{state}。"]
    result = (row.get("result") or "").strip()
    if result:
        out.append(f"结果：{result[:400]}")
    return "\n".join(out)


def _start(args: dict, *, store_ref, session_id_ref) -> str:
    store = _store(store_ref)
    goal = str(args.get("goal") or "").strip()
    if not goal:
        raise RuntimeError("goal 不能为空 —— 用一句话说清她要的是什么")
    sid = (session_id_ref() if callable(session_id_ref) else session_id_ref) or ""
    # 🔴 R6：测试会话不得立任务 —— 任务是全局副作用，跟着部署走
    if is_test_session(sid):
        raise RuntimeError("这是测试会话，不能立长任务（会真的在后台跑）")

    steps_hint = str(args.get("steps_hint") or "").strip()
    tid = store.create(session_id=sid, goal=goal, steps_hint=steps_hint)

    ctx = tool_context.current()
    if ctx is not None:
        ctx.attach_task(tid, card={"goal": goal, "steps_hint": steps_hint})
    else:
        # 不在轮次里（单测直接调）。任务照立，卡发不出去 —— 留痕
        logger.warning("不在对话轮次里，任务 %s 的确认卡没发出去", tid)

    return (
        f"任务 {tid} 已立，确认卡发给她了。🔴 **还没开始跑** —— "
        "她点「跑」之后才会在后台执行。不要说「已经在弄了」。"
        "她问进度就用 task_status。"
    )


def _status(args: dict, *, store_ref, session_id_ref) -> str:
    store = _store(store_ref)
    tid = str(args.get("task_id") or "").strip()
    row = store.get(tid) if tid else store.latest()
    if row is None:
        return "还没有长任务。"
    out = _brief(row)
    steps = store.progress(row["id"], limit=5)
    if steps:
        out += "\n最近的进度：\n" + "\n".join(
            f"  · {s['step'][:120]}" for s in steps)
    elif row["status"] == task_mod.RUNNING:
        out += "\n（刚开跑，还没有上报步骤）"
    return out


def _cancel(args: dict, *, store_ref, session_id_ref) -> str:
    store = _store(store_ref)
    tid = str(args.get("task_id") or "").strip()
    if not tid:
        raise RuntimeError("task_id 不能为空")
    row = store.cancel(tid)
    if row is None:
        raise RuntimeError(f"没有 {tid} 这个任务")
    if row["status"] == task_mod.CANCELLED:
        return (
            f"任务 {tid} 已停。"
            + ("它本来在跑 —— 手头这步做完就会收尾，不会再继续。"
               if row.get("started_at") else "它还没开跑，不会跑了。")
        )
    return f"任务 {tid} 已经是终态（{_STATE_TEXT.get(row['status'], row['status'])}），不用停。"


def register_all(loop, *, store_ref, session_id_ref) -> None:
    handlers = {
        START.name: lambda args: _start(
            args, store_ref=store_ref, session_id_ref=session_id_ref),
        STATUS.name: lambda args: _status(
            args, store_ref=store_ref, session_id_ref=session_id_ref),
        CANCEL.name: lambda args: _cancel(
            args, store_ref=store_ref, session_id_ref=session_id_ref),
    }
    for spec in _SPECS:  # 🔴 三处同步之三：这里漏一个，模型看得见调不了
        loop.register(spec, handlers[spec.name])
