"""目标（2026-10-22，《Caelum-记忆目标主动-设计稿》第三节）。

她 10-20 定：**判断就立，事后能删**。她说的是一件要跟一段时间的事（「我要减肥」「每三天提醒我浇水」）
—— 他直接立，不先问；立完告诉她立了什么，她在 Todo 页能看、能删。

目标拆出来的事落进她的 Todo（bridge 的 todos，带 goal_id）：「每周三次力量训练」→ 一条 weekly_count 的 Todo。
她划掉一条，bridge 自动给目标记一笔进展；所以这里不需要「完成一条」的工具，照旧用 complete_todo。

数据在 bridge（`/api/goals`），和 Todo 一个库 —— 一页显示、删目标能连带删。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.llm import ToolSpec
from tools import context
from tools.bridge_client import BridgeClient

logger = logging.getLogger(__name__)

CATEGORIES = ["健康", "关系", "财务", "事业", "兴趣", "效率", "其他"]

_TODO_ITEM = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "要做的事，如「力量训练」"},
        "repeat": {"type": "string", "enum": ["anytime", "once", "daily", "weekly", "weekly_count"]},
        "at": {"type": "string", "description": "几点，如 19:00；没有就留空（那样到点不会追她）"},
        "weekdays": {"type": "string", "description": "repeat=weekly：1,3,5 = 周一三五"},
        "times": {"type": "integer", "description": "repeat=weekly_count：每周几次"},
        "due": {"type": "string", "description": "repeat=once：YYYY-MM-DD"},
    },
    "required": ["text"],
}

GOAL_CREATE_SPEC = ToolSpec(
    side_effect="write",
    name="goal_create",
    description=(
        "立一个目标。她说的是**一件要跟一段时间的事**（「我要减肥」「每三天提醒我浇水」「我想把英语捡起来」）时用 —— "
        "判断是就直接立，不用先问她（她定的：判断就立，事后能删）。\n"
        "一句「今天要买菜」这种一次性的事不是目标，用 add_todo。\n"
        "\n"
        "plan 写几步（第一步通常是把目标定具体）。要她定期去做的事放进 todos —— "
        "它们会落进她的 Todo 具体日子里，带着这个目标的标记；她划掉一条，目标自动记一笔进展。\n"
        "例：减脂 → todos=[{text:\"力量训练\", repeat:\"weekly\", weekdays:\"1,3,5\", at:\"19:00\"}]\n"
        "立完跟她说一声立了什么、落进了哪几天的 Todo。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "短名字，如「减脂」「浇花」"},
            "category": {"type": "string", "enum": CATEGORIES},
            "origin": {"type": "string", "description": "她的原话（立项的依据）"},
            "plan": {"type": "array", "items": {"type": "string"}, "description": "几步计划"},
            "todos": {"type": "array", "items": _TODO_ITEM, "description": "要落进她 Todo 的事，可以空"},
        },
        "required": ["title", "origin"],
    },
)

GOAL_UPDATE_SPEC = ToolSpec(
    side_effect="write",
    name="goal_update",
    description=(
        "推进一个已经立了的目标。goal 给目标名里的几个字。\n"
        "· log：记一笔进展（她报了体重、说今天练了什么、你替她做了什么）。text 写那一笔\n"
        "· plan：改计划（给完整的新 plan）\n"
        "· add_todo：再往她 Todo 里落一件属于它的事（todo 同 goal_create 的 todos 里一项）\n"
        "· pause / resume / done：暂停 / 继续 / 完成\n"
        "她划掉属于目标的 Todo 时进展会自动记，不用再 log。要删目标让她在 Todo 页删（会问她连不连带那些 Todo）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "目标名里的几个字"},
            "action": {"type": "string", "enum": ["log", "plan", "add_todo", "pause", "resume", "done"]},
            "text": {"type": "string"},
            "plan": {"type": "array", "items": {"type": "string"}},
            "todo": _TODO_ITEM,
        },
        "required": ["goal", "action"],
    },
)


def _todo_body(t: dict, goal_id: str) -> dict:
    body: dict[str, Any] = {"text": str(t.get("text", "")).strip(), "goal_id": goal_id}
    for k in ("repeat", "at", "weekdays", "due"):
        if t.get(k):
            body[k] = str(t[k])
    if t.get("times"):
        body["times"] = int(t["times"])
    return body


def make_handlers(bridge: BridgeClient) -> dict:
    def _find(keyword: str) -> dict | None:
        r = bridge.get("/api/goals", {"status": "all"})
        if not r.ok:
            raise RuntimeError(f"读目标失败: {r.error}")
        items = (r.data or {}).get("items") or []
        kw = keyword.strip()
        return next((g for g in items if g.get("id") == kw), None) or \
            next((g for g in items if kw and kw in str(g.get("title", ""))), None)

    def _add_todos(goal_id: str, todos: list) -> tuple[list[str], list[str]]:
        ok, bad = [], []
        for t in todos or []:
            if not isinstance(t, dict) or not str(t.get("text", "")).strip():
                continue
            r = bridge.post("/api/today", _todo_body(t, goal_id))
            if r.ok and (r.data or {}).get("ok"):
                ok.append(str(t["text"]).strip())
            else:
                bad.append(str(t.get("text")))
                logger.warning("目标的 Todo 没落进去：%s（%s）", t.get("text"), r.error)
        if ok:
            context.wrote("todo", "goals")
        return ok, bad

    def goal_create(args: dict) -> str:
        title = str(args.get("title", "")).strip()
        if not title:
            return "没给目标名，没立。"
        r = bridge.post("/api/goals", {
            "title": title, "category": args.get("category") or "其他",
            "origin": str(args.get("origin", "")), "plan": list(args.get("plan") or []),
        })
        if not r.ok or not (r.data or {}).get("id"):
            raise RuntimeError(f"立目标失败: {r.error}")
        gid = r.data["id"]
        context.wrote("goals")
        ok, bad = _add_todos(gid, list(args.get("todos") or []))
        out = f"立好了：{title}"
        if args.get("plan"):
            out += f" · 计划 {len(args['plan'])} 步"
        if ok:
            out += " · 落进她 Todo 的：" + "、".join(ok)
        if bad:
            out += f"（{'、'.join(bad)} 没落进去，再试一次或者让她自己加）"
        return out + "。她在 Todo 页能看到、能删。"

    def goal_update(args: dict) -> str:
        g = _find(str(args.get("goal", "")))
        if g is None:
            return f"没找到「{args.get('goal')}」这个目标。"
        gid, title, action = g["id"], g.get("title"), args.get("action")
        if action == "log":
            text = str(args.get("text", "")).strip()
            if not text:
                return "没写进展是什么，没记。"
            r = bridge.post(f"/api/goals/{gid}/log", {"text": text})
            if not r.ok:
                raise RuntimeError(f"记进展失败: {r.error}")
            context.wrote("goals")
            return f"「{title}」记了一笔：{text}"
        if action == "add_todo":
            ok, bad = _add_todos(gid, [args.get("todo") or {}])
            return f"「{title}」往 Todo 里落了：{'、'.join(ok)}" if ok else "没落进去。"
        body: dict[str, Any] = {}
        if action == "plan":
            body["plan"] = list(args.get("plan") or [])
            if not body["plan"]:
                return "新计划是空的，没改。"
        elif action in ("pause", "resume", "done"):
            body["status"] = {"pause": "paused", "resume": "active", "done": "done"}[action]
        else:
            return f"不认识的 action：{action}"
        r = bridge.patch(f"/api/goals/{gid}", body)
        if not r.ok:
            raise RuntimeError(f"改目标失败: {r.error}")
        context.wrote("goals")
        return {"plan": f"「{title}」的计划改好了", "pause": f"「{title}」先停一停",
                "resume": f"「{title}」继续", "done": f"「{title}」完成了"}[action] + "。"

    return {"goal_create": goal_create, "goal_update": goal_update}


def register_all(loop, bridge: BridgeClient) -> None:
    handlers = make_handlers(bridge)
    for spec in (GOAL_CREATE_SPEC, GOAL_UPDATE_SPEC):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
