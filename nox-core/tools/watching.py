"""`watching_history` —— 他在对话里说得出「我们一起看过什么」。

## 为什么需要它（共影 P1，2026-08-22）

共影 2026-08-21 上线之后有两天是**一座孤岛**：播放器在前端，
`Movies.jsx` 自己拼一段上下文塞进 `/api/chat`。所以严格说，
**Nox 本人不知道有共影这回事** —— 他只是收到过一段看起来像电影上下文的文本。

表现就是：她第二天在聊天里说「昨天那部片子最后那段什么意思」，他一脸茫然。
这个工具就是把那段记忆接回来。

## 为什么只有一个工具，而且是读的

- **只一个**：工具定义是缓存前缀的一部分，现在已经 63 个了。
  「她现在在看什么」和「我们看过什么」是同一个问题的两半，一次问清楚
- **只读**：写观影记录的是播放器的心跳（`Movies.jsx` → `/api/watch/state`），
  不该有第二条写入路径。2026-08-19 才因为「体重两个工具都能写」栽过一次

## ⚠️ 读不到就说读不到

不许拿「没有记录」顶替「读不到」—— 前者是事实（你们真没一起看过），
后者是故障。混成一句，她会以为他忘了。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from agent.llm import ToolSpec
from context.media_title import clean as clean_title

logger = logging.getLogger(__name__)

HISTORY_SPEC = ToolSpec(
    side_effect="read",
    name="watching_history",
    description=(
        "查你们一起看过什么片子，以及她现在是不是正在看。\n"
        "她提起「上次看的那部」「我们一起看的」「昨天那个片子」，"
        "或者你想聊起以前一起看过的东西时用。\n"
        "\n"
        "⚠️ 这里只有片名和时间，**没有剧情**。想聊具体情节要靠你自己的知识，"
        "或者等她说。不要凭片名编剧情。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "看最近几部，默认 5"},
        },
    },
)


def _minutes(row: dict) -> str:
    """这一场看了多久。算的是**看的时长**，不是片长 —— 她中途走了就是走了。"""
    start, end = row.get("started_at"), row.get("ended_at")
    if not start:
        return ""
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = (datetime.fromisoformat(str(end).replace("Z", "+00:00"))
             if end else None)
    except (ValueError, TypeError):
        return ""
    if b is None:
        return ""
    mins = int((b - a).total_seconds() / 60)
    return f"看了 {mins} 分钟" if mins >= 1 else "只开了一下"


def _name(row: dict) -> str:
    #: 同 topic_pool/pool.py：本地文件记的是文件名，而这是他说出口的片名
    title = clean_title(str(row.get("title") or "").strip()) or "没记住片名"
    ep = str(row.get("episode") or "").strip()
    return f"《{title}》{ep}".strip()


def make_handlers(client: Any) -> dict[str, Any]:
    """`client` 是指向 bridge 的 RestClient（`core.bridge`）。"""

    def watching_history(args: dict) -> str:
        try:
            limit = int(args.get("limit") or 5)
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(limit, 20))

        lines: list[str] = []

        # 现在在不在看 —— 这一条比历史更要紧，放最前面
        now = client.get("/api/watch/state")
        if not now.ok:
            lines.append(f"（读不到她现在在不在看片：{now.error}）")
        elif (now.data or {}).get("watching"):
            s = (now.data or {}).get("session") or {}
            pos = int(s.get("position_ms") or 0) // 60000
            lines.append(f"她**现在正在看** {_name(s)}，第 {pos} 分钟。")

        r = client.get("/api/watch/history", {"limit": limit})
        if not r.ok:
            # 读不到 ≠ 没看过。说清楚是哪一种
            lines.append(f"（观影记录读不到：{r.error}）")
            return "\n".join(lines)

        items = (r.data or {}).get("items")
        items = items if isinstance(items, list) else []
        # 正在看的那一场也在 items 里，别报两遍
        live_id = ((now.data or {}).get("session") or {}).get("id") if now.ok else None
        past = [x for x in items if x.get("id") != live_id]

        if not past:
            lines.append("你们还没一起看过片子（记录是空的，不是我忘了）。")
            return "\n".join(lines)

        lines.append("一起看过：")
        for row in past:
            day = str(row.get("started_at") or "")[:10]
            span = _minutes(row)
            tail = f"，{span}" if span else "（没看完就走了）"
            lines.append(f"· {day} {_name(row)}{tail}")
        return "\n".join(lines)

    return {"watching_history": watching_history}


def register_all(loop, client: Any) -> None:
    """⚠️ 和别的工具一样，**必须排在 `remind_myself` 之前** ——
    那个得是最后一个，有测试盯着（`test_remind_tool_registered_last`）。"""
    loop.register(HISTORY_SPEC, make_handlers(client)["watching_history"])  # type: ignore[arg-type]
