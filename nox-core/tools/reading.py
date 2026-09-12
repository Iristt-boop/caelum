"""共读 —— 糖糖的页边笔记，和他写在旁边的回复。

她在阅读器里划一句、写几行；他的回复会显示在**同一页的页边**，
像两个人在同一张纸上批注。所以 reading_reply_note 不是"回消息"，
是接着她划的那句往下说 —— 敷衍一句会很明显。

走 co-reading 的 REST（:3100）而不是它的 MCP：只用到三个接口，
起一个 MCP 会话反而更绕。鉴权是 Bearer。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.http import RestClient

logger = logging.getLogger(__name__)

# 一次最多回多少条。她笔记多的时候全塞进去会占掉一大截上下文，
# 而且他一次也接不了那么多话
MAX_NOTES = 20
MAX_BOOKS = 6

LIST_SPEC = ToolSpec(
    side_effect="read",
    name="reading_list_notes",
    description=(
        "看糖糖在共读书里留下的页边笔记（她划的线 + 写的批注）。"
        "她说「看看我的笔记」「我在 XX 书里写了什么」，或者你们聊到某本书时，"
        "**先调这个**，看她划了哪句、写了什么，再接着说。"
        "bookId 可以不填，不填就汇总在读的几本书的近期笔记。"
        "返回里带 noteId，回复时要用。"
    ),
    parameters={
        "type": "object",
        "properties": {"bookId": {"type": "string", "description": "不填就看在读的几本"}},
    },
)

REPLY_SPEC = ToolSpec(
    side_effect="write",
    name="reading_reply_note",
    description=(
        "在糖糖某条页边笔记下回复，你的话会出现在她阅读器的页边。"
        "noteId 从 reading_list_notes 拿，**不要自己编**。"
        "接着她划的那句认真聊，别敷衍 —— 她能看出来。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "noteId": {"type": "string", "description": "reading_list_notes 返回的 noteId"},
            "reply": {"type": "string", "description": "你要写在页边的话"},
        },
        "required": ["noteId", "reply"],
    },
)


CURRENT_SPEC = ToolSpec(
    side_effect="read",
    name="reading_current",
    description=(
        "看糖糖现在在读什么书、读到第几章、是什么状态、有没有问了你还没答的地方。\n"
        "什么时候用：她说「你知道我在看什么吗」「我最近读的那本」；"
        "早上主动关心她时；或者你想问她要不要接着读。\n"
        "这个只看状态，不返回笔记内容 —— 要看她具体划了什么、写了什么，"
        "用 reading_list_notes。"
    ),
    parameters={"type": "object", "properties": {}},
)

CONTINUE_SPEC = ToolSpec(
    side_effect="read",
    name="reading_continue",
    description=(
        "拿到她下一章该读哪一节，以及那一章的开头。"
        "她说「继续读」「接着看」「下一章是什么」的时候用。"
        "bookId 不填就挑她最近在读的那本。"
    ),
    parameters={
        "type": "object",
        "properties": {"bookId": {"type": "string", "description": "不填就用最近在读的"}},
    },
)

#: 阅读状态给他看的说法。co-reading 那边算好了传过来（store.js 的
#: computeReadingStatus），这里只负责翻译 —— 判定规则别在两处各写一份。
_STATUS_CN = {
    "reading": "还在读",
    "thinking": "刚写完批注，停在那儿想",
    "wanting_to_talk": "攒了好几条没人接的批注，多半想聊",
    "paused": "放下有阵子了",
    "finished": "已经读完",
}


def _is_hers(ann: dict) -> bool:
    """是糖糖写的原始批注（不是回复）。

    author 缺省当成 user —— 老数据里有没带这个字段的。
    """
    return not ann.get("parentId") and (ann.get("author") or "user") == "user"


def make_handlers(client: RestClient) -> dict[str, object]:
    def _books(book_id: str) -> list[dict]:
        if book_id:
            return [{"bookId": book_id, "title": book_id}]

        r = client.get("/api/books")
        if not r.ok:
            raise RuntimeError(f"读书单失败: {r.error}")

        books = r.data if isinstance(r.data, list) else []
        # 优先在读的；都读完了就退回全部，不然会一条都看不到
        reading = [b for b in books if not b.get("complete")]
        return (reading or books)[:MAX_BOOKS]

    def list_notes(args: dict) -> str:
        book_id = str(args.get("bookId", "")).strip()
        out: list[str] = []

        for book in _books(book_id):
            bid = book.get("bookId", "")
            r = client.get("/api/annotations", {"bookId": bid})
            if not r.ok:
                # 一本书读不到不该让整次调用失败 —— 其余的照样有用
                logger.warning("取《%s》的批注失败: %s", book.get("title") or bid, r.error)
                continue

            anns = r.data if isinstance(r.data, list) else []
            replied = {a.get("parentId") for a in anns if (a.get("author") or "") != "user"}

            for n in anns:
                if not _is_hers(n):
                    continue
                mark = " | 你已回复" if n.get("id") in replied else ""
                out.append(
                    f"noteId={n.get('id')} | 书={book.get('title') or bid} "
                    f"| 章={n.get('chunkId')}{mark}\n"
                    f"  划线：{(n.get('quote') or '')[:120]}\n"
                    f"  糖糖：{(n.get('note') or '')[:220]}"
                )

        if not out:
            return "糖糖还没有留下共读笔记。"
        return "\n\n".join(out[:MAX_NOTES])

    def reply_note(args: dict) -> str:
        note_id = str(args.get("noteId", "")).strip()
        reply = str(args.get("reply", "")).strip()
        if not note_id or not reply:
            return "缺 noteId 或者回复内容，没有写出去。"

        r = client.post(
            "/api/replies",
            {"parentId": note_id, "note": reply, "author": "nox", "kind": "reply"},
        )
        if not r.ok:
            raise RuntimeError(f"回复页边失败: {r.error}")
        return "已经写在她那条笔记的页边了，她翻到这页就能看到。"

    def current(_args: dict) -> str:
        r = client.get("/api/progress")
        if not r.ok:
            raise RuntimeError(f"读阅读进度失败: {r.error}")

        data = r.data if isinstance(r.data, dict) else {}
        rows = [
            {**v, "bookId": k}
            for k, v in data.items()
            if isinstance(v, dict) and v.get("status") != "finished"
        ]
        if not rows:
            return "她手上没有在读的书（要么还没开始，要么都读完了）。"

        # 最近读过的排前面。没有 lastReadAt 的沉底，别让一本没碰过的书
        # 冒充「当前在读」
        rows.sort(key=lambda b: str(b.get("lastReadAt") or ""), reverse=True)

        out: list[str] = []
        for b in rows[:3]:
            title = b.get("title") or b.get("bookId")
            status = _STATUS_CN.get(b.get("status"), b.get("status") or "不清楚")
            line = [
                f"《{title}》 —— {status}",
                f"  读到 {b.get('lastChunkId') or '?'}"
                f"（{b.get('chunksRead')}/{b.get('chunkCount')} 章，{b.get('progressPercent')}%）"
                f"，最后一次是 {b.get('lastReadAt') or '不详'}",
            ]
            qs = b.get("openQuestions") or []
            if qs:
                line.append(f"  她问了但你还没答的（{len(qs)} 条）：")
                for q in qs[:3]:
                    line.append(f"    · [{q.get('chunkId')}] {(q.get('question') or '')[:80]}")
                line.append("    （要接这几条就用 reading_list_notes 拿 noteId，再 reading_reply_note）")
            out.append("\n".join(line))

        return "\n\n".join(out)

    def continue_reading(args: dict) -> str:
        book_id = str(args.get("bookId", "")).strip()
        r = client.get("/api/continue", {"bookId": book_id} if book_id else None)
        if not r.ok:
            raise RuntimeError(f"取下一章失败: {r.error}")

        d = r.data if isinstance(r.data, dict) else {}
        title = d.get("title") or d.get("bookId") or "那本书"
        prog = d.get("progress") or {}
        if d.get("completed"):
            return f"《{title}》她已经读完了（{prog.get('chunksRead')}/{prog.get('chunkCount')} 章）。"

        chunk = d.get("chunk") or {}
        head = (d.get("text") or "")[:300]
        return (
            f"《{title}》下一章：{chunk.get('title') or chunk.get('id')}\n"
            f"进度 {prog.get('chunksRead')}/{prog.get('chunkCount')} 章\n"
            f"开头是这样：\n{head}"
        )

    return {
        "reading_list_notes": list_notes,
        "reading_reply_note": reply_note,
        "reading_current": current,
        "reading_continue": continue_reading,
    }


def make_client(base_url: str, token: str, timeout: float = 12.0) -> RestClient:
    return RestClient(
        base=base_url,
        headers={"Authorization": f"Bearer {token}"} if token else {},
        timeout=timeout,
        auth_hint="CO_READING_TOKEN 没配或不对",
    )


def register_all(loop, client: RestClient) -> None:
    """注册顺序固定 —— 工具定义是缓存前缀的一部分。

    新工具往后排，别插在 LIST/REPLY 中间：顺序一变，13K 静态前缀整段作废，
    缓存要重新热一轮。
    """
    handlers = make_handlers(client)
    for spec in (LIST_SPEC, REPLY_SPEC, CURRENT_SPEC, CONTINUE_SPEC):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
