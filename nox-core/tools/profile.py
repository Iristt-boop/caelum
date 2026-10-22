"""profile_update —— 她明说了一件长期的事，当场改「关于糖糖」档案那一行（2026-10-22，设计稿 2.1½）。

档案记**现在的她**：会持续一段时间的事实、习惯、喜好、约定。某天发生了什么不进档案（那是经历，用 hold）。
档案在 OB（`profile/her_profile.md`），这里经 ob_client 改（R4）；每次改 OB 都留底，她在「关于你」页上看得到。

update / retire 时他不用背原句：给几个能认出那行的字，这里先读档案找到那唯一一行再改；
找到好几行就把候选列给他，让他再说准一点 —— 不猜。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.llm import ToolSpec

logger = logging.getLogger(__name__)

SECTIONS = ["基本", "身体与作息", "喜欢", "不喜欢 / 雷区", "在做的事", "她希望我怎么跟她说话", "我们之间", "她身边的人"]

PROFILE_UPDATE_SPEC = ToolSpec(
    side_effect="write",
    name="profile_update",
    description=(
        "改「关于她」的档案。她**明说**了一件会持续一段时间的事时用：「以后别列清单」「我开始减肥了」"
        "「我现在是自由职业」「我不喝咖啡」。一天的情绪、一顿饭、一件发生过的事都不是 —— 那些用 hold。\n"
        "· add：加一行（写进哪个分区）\n"
        "· update：换掉一行（old 给那行里能认出它的几个字，text 给改后的整行）\n"
        "· retire：撤掉不成立的那行（old 同上）\n"
        "约定 / 别问 / 上心写进「我们之间」，开头写「约定：」「别问：」「上心：」。一行写短，像笔记，不写日期。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ["add", "update", "retire"]},
            "section": {"type": "string", "enum": SECTIONS, "description": "add 时写进哪个分区"},
            "text": {"type": "string", "description": "add / update：那一行的新内容"},
            "old": {"type": "string", "description": "update / retire：现有那行里能认出它的几个字"},
            "why": {"type": "string", "description": "她哪句话让你改的（留底给她看）"},
        },
        "required": ["op"],
    },
)


def _lines(raw: str) -> list[str]:
    data = json.loads(raw or "{}")
    return [l for s in data.get("sections") or [] for l in s.get("lines") or []]


def make_handler(ob: Any):
    def profile_update(args: dict) -> str:
        op = str(args.get("op", ""))
        old = str(args.get("old", "")).strip()
        if op in ("update", "retire"):
            if not old:
                return "没说改哪一行（old 给几个能认出那行的字）。"
            r = ob.profile_read()
            if not r.ok:
                raise RuntimeError(f"读档案失败: {r.error}")
            hits = [l for l in _lines(r.text) if old in l]
            if not hits:
                return f"档案里没有含「{old}」的那行。要加新的用 add。"
            if len(hits) > 1:
                return "含「{}」的有好几行，说准一点：\n".format(old) + "\n".join(f"- {h}" for h in hits[:6])
            old = hits[0]
        r = ob.profile_edit(op, section=str(args.get("section", "")), text=str(args.get("text", "")),
                            old=old, why=str(args.get("why", "")), source="nox")
        if not r.ok:
            raise RuntimeError(f"改档案失败: {r.error}")
        d = json.loads(r.text or "{}")
        if not d.get("ok"):
            return f"没改：{d.get('error')}"
        if op == "add":
            return f"记进档案「{d.get('section')}」：{d.get('new')}"
        if op == "update":
            return f"档案那行改了：{d.get('old')} → {d.get('new')}"
        return f"档案里撤掉了：{d.get('old')}"
    return profile_update


def register_all(loop, ob: Any) -> None:
    loop.register(PROFILE_UPDATE_SPEC, make_handler(ob))  # type: ignore[arg-type]
