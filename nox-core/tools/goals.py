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

_ROUTINE = {
    "type": "object",
    "description": "到点你要去做的事（她说「每天 9 点给我推新闻」「每周日帮我看看有什么新片」）",
    "properties": {
        "at": {"type": "string", "description": "几点，HH:MM（她那边的时间）"},
        "weekdays": {"type": "string", "description": "哪几天，1=周一…7=周日，逗号隔开；每天就写 1,2,3,4,5,6,7"},
        "task": {"type": "string", "description": "到点你去做什么、做完怎么跟她说，如「搜今天的 AI 新闻，挑一两条讲给她」"},
    },
    "required": ["at", "task"],
}

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
        "立完跟她说一声立了什么、落进了哪几天的 Todo。\n"
        "\n"
        "**owner=nox：你自己要记着跟进的事**（她 10-23 定）—— 她这阵子的状态需要你盯着："
        "「姨妈推迟 42 天了」「腰刚缓过来」「在等面试结果」。这种是提醒你的，不是提醒她的："
        "不落进她的 Todo，every_days 写你隔几天问她一声；事情了了用 goal_update done 收掉。"
        "这类「最近的事」不要写进档案（profile_update 只放长期的）。\n"
        "\n"
        "**routine：到点你去做一件事，再来告诉她**（她 10-23 要的）——「每天 9 点给我推新闻」「每周日看看有什么新电影」。"
        "到点你会被叫醒、拿着工具先把事做了，再跟她说。不是提醒她做事（那是 todos）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "短名字，如「减脂」「浇花」"},
            "category": {"type": "string", "enum": CATEGORIES},
            "origin": {"type": "string", "description": "她的原话（立项的依据）"},
            "plan": {"type": "array", "items": {"type": "string"}, "description": "几步计划"},
            "todos": {"type": "array", "items": _TODO_ITEM, "description": "要落进她 Todo 的事，可以空（owner=nox 时不用）"},
            "owner": {"type": "string", "enum": ["her", "nox"], "description": "her=提醒她的（默认）；nox=你自己跟进的"},
            "every_days": {"type": "integer", "description": "owner=nox：隔几天跟进一次，默认 2"},
            "routine": _ROUTINE,
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
        "· routine：设 / 改到点你去做的事（给 routine）；清掉就给 routine={}\n"
        "她划掉属于目标的 Todo 时进展会自动记，不用再 log。要删目标让她在 Todo 页删（会问她连不连带那些 Todo）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "目标名里的几个字"},
            "action": {"type": "string", "enum": ["log", "plan", "add_todo", "pause", "resume", "done", "routine"]},
            "text": {"type": "string"},
            "plan": {"type": "array", "items": {"type": "string"}},
            "todo": _TODO_ITEM,
            "routine": _ROUTINE,
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
        mine = args.get("owner") == "nox"
        body: dict[str, Any] = {
            "title": title, "category": args.get("category") or "其他",
            "origin": str(args.get("origin", "")), "plan": list(args.get("plan") or []),
        }
        if mine:
            body.update(owner="nox", every_days=int(args.get("every_days") or 2))
        if args.get("routine"):
            body["routine"] = dict(args["routine"])
        r = bridge.post("/api/goals", body)
        if not r.ok or not (r.data or {}).get("id"):
            if body.get("routine") and str(r.error).startswith("HTTP 400"):
                return "到点的事格式不对：at 要 HH:MM，task 要写清做什么。改好再立一次。"
            raise RuntimeError(f"立目标失败: {r.error}")
        gid = r.data["id"]
        context.wrote("goals")
        if mine:
            #: 他自己跟进的不落她的 Todo（bridge 也会拒）—— 给了 todos 就照实说没落
            skipped = "（这是你自己跟进的，没往她 Todo 里放）" if args.get("todos") else ""
            return f"记下了，你自己跟进：{title} · 隔 {body['every_days']} 天问一声{skipped}。她在 Todo 页「我记着的」那栏看得到。"
        ok, bad = _add_todos(gid, list(args.get("todos") or []))
        out = f"立好了：{title}"
        if body.get("routine"):
            rt = body["routine"]
            out += f" · 每到 {rt.get('at')} 你会去做：{rt.get('task')}"
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
        elif action == "routine":
            rt = dict(args.get("routine") or {})
            body["routine"] = rt or None
        elif action in ("pause", "resume", "done"):
            body["status"] = {"pause": "paused", "resume": "active", "done": "done"}[action]
        else:
            return f"不认识的 action：{action}"
        r = bridge.patch(f"/api/goals/{gid}", body)
        if not r.ok:
            if action == "routine" and str(r.error).startswith("HTTP 400"):
                return "到点的事格式不对：at 要 HH:MM，task 要写清做什么。"
            raise RuntimeError(f"改目标失败: {r.error}")
        context.wrote("goals")
        if action == "routine":
            rt = body["routine"]
            return f"「{title}」：每到 {rt.get('at')} 你会去做 —— {rt.get('task')}。" if rt else f"「{title}」到点的事清掉了。"
        return {"plan": f"「{title}」的计划改好了", "pause": f"「{title}」先停一停",
                "resume": f"「{title}」继续", "done": f"「{title}」完成了"}[action] + "。"

    return {"goal_create": goal_create, "goal_update": goal_update}


def register_all(loop, bridge: BridgeClient) -> None:
    handlers = make_handlers(bridge)
    for spec in (GOAL_CREATE_SPEC, GOAL_UPDATE_SPEC):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
