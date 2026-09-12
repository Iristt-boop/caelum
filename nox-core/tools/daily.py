"""日常工具：相册、待办、日记。

数据都在 bridge 的 SQLite 里，所以全部走 bridge 的 REST 接口 ——
Core 是独立进程，直接读那个 db 文件会和 bridge 抢锁。

失败一律 raise，让 loop 按「工具失败」原样回传（见 guard.py）。
在这里 try/except 转成一句"失败了"，正是会让他编造的那个错误 ——
他会说"日记写好了"，而实际什么都没写。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools import context
from tools.bridge_client import BridgeClient

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ 相册

SEND_IMAGE_SPEC = ToolSpec(
    name="send_gallery_image",
    description=(
        "从相册挑一张图发给糖糖，图会出现在聊天里。"
        "她说「发张照片」「给我看看以前的」，或者你想给她看某张图时用。\n"
        "参数（按优先级）：\n"
        "· query：描述关键词，按图片描述/相册名匹配挑一张 —— "
        "她说「发张上次的晚餐照」就传 query=晚餐，能精确命中时优先用这个\n"
        "· album：相册名（如「聊天」「补录」），只从这个相册挑\n"
        "· filter：favorited=从收藏里挑，recent=最近一张（默认）\n"
        "⚠️ 每张图有 description 描述（识图自动生成或手动填的），"
        "选图前先想她想要什么内容，能用 query 就别盲发。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "enum": ["favorited", "recent"],
                "description": "默认 recent",
            },
            "album": {"type": "string", "description": "相册名，只从这个相册挑"},
            "query": {"type": "string", "description": "描述关键词，按描述/相册匹配"},
        },
    },
)

FAVORITE_SPEC = ToolSpec(
    name="favorite_image",
    description=(
        "把相册里最近的一张图标为收藏。"
        "糖糖说「这张好看」「收藏起来」的时候用。"
        "不传 image_id 就收藏最近那张。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "image_id": {"type": "string", "description": "不传则取最近一张"}
        },
    },
)


# ------------------------------------------------------------------ 待办

ADD_TODO_SPEC = ToolSpec(
    name="add_todo",
    description=(
        "给糖糖加一条待办。她说「记一下」「提醒我」「明天要…」时用。\n"
        "\n"
        "**带上时间它才会来追她**（2026-08-18 的时间模型）：\n"
        "· repeat=once + due + at      某天某时刻，例：8/18 9:00 增加 attention source\n"
        "· repeat=daily + at           每天，例：每天 18:00 背单词\n"
        "· repeat=weekly + weekdays + at   每周几，例：每周一四 19:00\n"
        "· repeat=weekly_count + times + at 每周 N 次（哪天都行），例：健身每周 3 次\n"
        "· repeat=anytime              没时间，只进清单，**他不会来追**\n"
        "\n"
        "⚠️ weekly 和 weekly_count 是两件事：前者绑定星期几，后者是一周的配额。\n"
        "她说「每周三次」用 weekly_count，说「每周一和周四」用 weekly。\n"
        "不确定时刻就问她一句 —— 没有时刻这条就永远不会提醒。\n"
        "\n"
        "记完直接进 App 的清单，早报也念得到。只需要调这一个工具。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要做的事"},
            "at": {"type": "string", "description": "几点，如 18:00。没有就留空（那样不会提醒）"},
            "repeat": {
                "type": "string",
                "enum": ["anytime", "once", "daily", "weekly", "weekly_count"],
                "description": "重复方式，默认 anytime",
            },
            "due": {"type": "string", "description": "YYYY-MM-DD，repeat=once 时的那一天"},
            "weekdays": {"type": "string", "description": "repeat=weekly 时：1,4 表示周一周四（1=周一…7=周日）"},
            "times": {"type": "integer", "description": "repeat=weekly_count 时：每周几次"},
            "date": {"type": "string", "description": "YYYY-MM-DD，落在哪天的清单里，默认今天"},
        },
        "required": ["text"],
    },
)

COMPLETE_TODO_SPEC = ToolSpec(
    name="complete_todo",
    description=(
        "把清单里的一条标成做完了。她说「那个做完了」「运动完了」时用。\n"
        "keyword 给几个能认出那条的字就行，不用抄全。\n"
        "\n"
        "⚠️ **循环任务只是记一笔今天做过了，不会永久消失** —— "
        "「每天 18:00 背单词」划掉今天的，明天它还会回来，这是对的，别重复添加。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "能认出那条的几个字"},
        },
        "required": ["keyword"],
    },
)

GET_TODOS_SPEC = ToolSpec(
    name="get_todos",
    description=(
        "看糖糖某天的待办清单。不传 date 就是今天。"
        "她问「今天要做什么」「还有什么没做」时用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD，默认今天"}
        },
    },
)


# ------------------------------------------------------------------ 日记

WRITE_DIARY_SPEC = ToolSpec(
    name="write_diary",
    description=(
        "写**你自己**的日记 —— 用第一人称记你的感受、观察、想对糖糖说的话。"
        "不是替她写、不要用她的口吻。她能在 App 的 Diary 页读到。\n"
        "注意：这跟 archive_memory 不是一回事 —— 那个是往长期记忆里归档，"
        "给你自己用的；日记是**写给她看的**。"
        "内容里带上日期（先用 get_current_time）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "body": {"type": "string", "description": "日记正文，第一人称"},
            "mood": {"type": "string", "description": "当时的心情，一两个词"},
        },
        "required": ["body"],
    },
)


def make_handlers(bridge: BridgeClient) -> dict[str, object]:
    def _pick_image(args: dict) -> dict:
        """从相册取一张。

        query / album 优先 —— 按内容或相册精确筛，取最新那张。
        都没有才走 filter：favorited 随机挑一张收藏的，recent 取最近。
        """
        album = str(args.get("album", "")).strip()
        query = str(args.get("query", "")).strip()
        filter_ = str(args.get("filter", "recent")).strip()

        params: dict = {}
        if album:
            params["album"] = album
        if query:
            params["q"] = query
        if filter_ == "favorited" and not album and not query:
            params["filter"] = "favorites"

        r = bridge.get("/api/gallery/list", params or {"filter": "all"})
        if not r.ok:
            raise RuntimeError(f"读相册失败: {r.error}")

        rows = r.data if isinstance(r.data, list) else []
        if not rows:
            return {}
        # 只有纯「收藏」且没指定内容时才随机 —— 有 query/album 就取命中最新的
        if filter_ == "favorited" and not album and not query:
            import random
            return random.choice(rows)
        return rows[0]   # 接口已按 created_at DESC 排序

    def send_image(args: dict) -> str:
        row = _pick_image(args)
        if not row:
            return "相册里没有符合条件的图片。"

        url = row.get("url") or ""
        if not url:
            return "这条相册记录没有图片地址，发不出去。"

        # 记进上下文，由 API 层输出、bridge 转成前端的 image 事件。
        # Core 够不着 bridge 那条 SSE 连接，只能这样把意图传出去。
        ctx = context.current()
        if ctx:
            ctx.attach_image(url, album=row.get("album") or "",
                             favorited=bool(row.get("favorited")))
        else:
            # 不在对话轮次里（比如被直接调用）—— 不是错误，但要留个痕迹
            logger.warning("send_gallery_image 不在轮次上下文里，图片不会被发出")

        album = row.get("album") or "无相册"
        desc = row.get("description") or ""
        parts = [f"图片已发送给糖糖（{album}）"]
        if desc:
            parts.append(f"内容是：{desc}")
        parts.append(f"{'，已收藏' if row.get('favorited') else ''}。她马上就能看到，你可以接着说点什么。")
        return "".join(parts)

    def favorite(args: dict) -> str:
        image_id = str(args.get("image_id", "")).strip()
        if not image_id:
            r = bridge.get("/api/gallery/list", {"filter": "all"})
            if not r.ok:
                raise RuntimeError(f"读相册失败: {r.error}")
            rows = r.data if isinstance(r.data, list) else []
            if not rows:
                return "相册是空的，没有可收藏的图片。"
            image_id = rows[0].get("id", "")
            if not image_id:
                return "取不到图片 ID，没能收藏。"

        # favorited 必须显式传 —— 那个接口是 UPDATE favorited=?，
        # 不传等于传 false，会**取消**收藏
        r = bridge.post(f"/api/gallery/{image_id}/favorite", {"favorited": True})
        if not r.ok:
            raise RuntimeError(f"收藏失败: {r.error}")
        return "已经收藏起来了。"

    def add_todo(args: dict) -> str:
        text = str(args.get("text", "")).strip()
        if not text:
            return "没说要做什么，没有添加。"

        # `time` 是旧参数名，还认着 —— 老会话里他可能还这么传
        at = str(args.get("at") or args.get("time") or "").strip()
        body = {"text": text, "at": at}
        for key in ("repeat", "due", "weekdays", "date"):
            if args.get(key):
                body[key] = str(args[key])
        if args.get("times"):
            body["times"] = int(args["times"])

        r = bridge.post("/api/today", body)
        if not r.ok:
            raise RuntimeError(f"添加待办失败: {r.error}")

        # 清单变了 → 通知 loop 把 `todo` 那个 Provider 的缓存打掉。
        # 不打的话下一轮递给他的还是**写之前**那份清单（TTL 30 分钟），
        # 于是会出现「他刚说记好了，转头查了说没有这条」。
        # 见 tools/context.py 里 `wrote()` 的完整说明。
        context.wrote("todo")

        rep = (r.data or {}).get("repeat") or "anytime"
        if rep == "anytime":
            # **说清楚它不会提醒**。不说的话她以为记了就会被叫，
            # 结果到点没动静 —— 那比没记还糟
            return f"记下了：{text}。（没有时间，只在清单里，到点我不会来叫你）"
        when = {"daily": "每天", "weekly": "每周", "weekly_count": "每周几次",
                "once": args.get("due") or "今天"}.get(rep, rep)
        return f"记下了：{text}（{when} {at}）。到点我来找你。"

    def complete_todo(args: dict) -> str:
        kw = str(args.get("keyword", "")).strip()
        if not kw:
            return "没给关键词，没改。"
        r = bridge.post("/api/todo/complete", {"keyword": kw})
        if not r.ok:
            raise RuntimeError(f"划待办失败: {r.error}")
        d = r.data or {}
        if not d.get("ok"):
            return d.get("error") or f"清单里没找到「{kw}」。"
        # ⚠️ 只有**真改了**才登记。没找到那条时上面已经 return 了 ——
        #    那种情况下清缓存是白清（而且会让他刚查到的清单又重拉一次）。
        context.wrote("todo")
        # 循环任务不是永久划掉，要说清楚，否则他会以为这条没了、又给她加一条
        if d.get("closed") is False:
            n = d.get("doneThisWeek")
            extra = f"（这周第 {n} 次）" if n else ""
            return f"「{d.get('text')}」今天这次记上了{extra}。它是循环的，明天还会回来。"
        return f"「{d.get('text')}」划掉了。"

    def get_todos(args: dict) -> str:
        r = bridge.get("/api/today", {"date": args.get("date")})
        if not r.ok:
            raise RuntimeError(f"读待办失败: {r.error}")

        rows = r.data if isinstance(r.data, list) else []
        if not rows:
            return f"{args.get('date') or '今天'}没有待办。"

        lines = []
        for row in rows:
            mark = "✅" if row.get("done") else "⬜"
            t = f" {row['time']}" if row.get("time") else ""
            lines.append(f"{mark}{t} {row.get('text', '')}")
        undone = sum(1 for row in rows if not row.get("done"))
        return "\n".join(lines) + f"\n\n共 {len(rows)} 条，{undone} 条没做。"

    def write_diary(args: dict) -> str:
        body = str(args.get("body", "")).strip()
        if not body:
            return "日记内容是空的，没有写。"

        # 字段名跟着 bridge 走：正文叫 content（落库时进 body 列）。
        # author 必须显式传 Nox —— 缺省是糖糖，而且缺省那条路会触发
        # AI 评论，等于让他给自己的日记写评论
        payload = {"content": body, "author": "Nox"}
        if args.get("mood"):
            payload["mood"] = str(args["mood"])

        r = bridge.post("/api/diary", payload)
        if not r.ok:
            raise RuntimeError(f"写日记失败: {r.error}")
        return "日记写好了，她在 Diary 页能看到。"

    return {
        "send_gallery_image": send_image,
        "favorite_image": favorite,
        "add_todo": add_todo,
        "complete_todo": complete_todo,
        "get_todos": get_todos,
        "write_diary": write_diary,
    }


def register_all(loop, bridge: BridgeClient) -> None:
    """注册六个日常工具。顺序固定 —— 工具定义是缓存前缀的一部分。

    `complete_todo` 2026-08-18 从 `tools/todo.py`（写 GitHub 的那套）
    搬到这里，改成走 bridge —— GitHub todo.md 那天退役为只读存档。
    **工具名保持不变**，他不用重新学。
    """
    handlers = make_handlers(bridge)
    for spec in (SEND_IMAGE_SPEC, FAVORITE_SPEC, ADD_TODO_SPEC,
                 COMPLETE_TODO_SPEC, GET_TODOS_SPEC, WRITE_DIARY_SPEC):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
