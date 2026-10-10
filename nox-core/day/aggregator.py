"""Day Aggregator —— 「Nox 的一天」的只读投影。

设计出处：`Nox 的一天 架构设计文档.md` v1.1（糖糖，2026-08-18）。

    原始 Raw Events
       ↓ Event Normalizer   归一成同一个形状
    Normalized
       ↓ Meaning Builder    过滤 + 意义化
    DayEvent[]

## 三条硬约束（照文档第 4 节）

1. **不新建任何事实存储。** 这里一行 INSERT 都没有 —— 全是读。
   `nox_day` 表不存在，也不该存在。
2. **每条事件都能溯源。** `related` 里带 careId / conversationId /
   threadId，点开能回到原始记录。
3. **只输出用户可感知的事件。** 心跳、工具调用、上下文刷新一律不进时间线。
   这条最容易破功 —— 一旦开始往里塞调试信息，它就从「他的一天」
   退化成监控日志。

## V1 只用真数据，没接的如实说

文档列了八个数据源。**现在真正有带时间戳的记录的只有三个**：

    ✅ CareLedger    他惦记 / 说话 / 被拦下的每一次决策
    ✅ Conversation  Core 会话库里的消息（带 created_at）
    ✅ WakeBook      纸条的 history

    ❌ Task / Music / Memory / World —— 见 `SOURCES` 里的说明

没接的**不编事件**，而是在响应里如实标出来。宁可时间线短一点，
也不要让她看到一条其实没发生过的「一起听音乐」。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.llm import MEME_TAGS, strip_stage_tags
from context.media_title import clean as clean_title

logger = logging.getLogger(__name__)

from temporal import LOCAL_TZ  # noqa: E402  ← 唯一定义在 temporal.py（审计 F1）
TZ_NAME = "Asia/Shanghai"

#: 两条消息隔多久算「另一段对话」。20 分钟是拍的，
#: 依据是她的说话节奏：一段聊完到下一段通常隔得比这久
CONVERSATION_GAP_MIN = 20

#: 一段对话至少要有几条消息才值得进时间线。
#: 一问一答两条也是一次陪伴，所以门槛就是 2
MIN_CONVERSATION_MESSAGES = 2

#: 数据源现状。**如实写**，前端据此知道哪些是「还没接」而不是「今天没发生」
SOURCES = {
    "care_ledger": {"wired": True, "note": ""},
    "conversation": {"wired": True, "note": ""},
    "wakebook": {"wired": True, "note": ""},
    "task": {"wired": True, "note": "同一条一天只留最后一次完成（last_done_at）"},
    "music": {"wired": True, "note": "eryu 的 /music/recent，每首带 playedAt"},
    "world": {"wired": True, "note": "目前只有 sleep_duration 一种事实（只有 SleepSource 在写）"},
    # 2026-08-18 给 Ombre Brain 加了 `/recent`（结构化 JSON）才接上的。
    # MCP 那几个工具回的是给模型读的文本，解析它等于猜，而且格式一改就静默失效
    "memory": {"wired": True, "note": "OB 的 /recent，只回元数据+预览，全文走 trace"},
    # 2026-10-10：他发的朋友圈 / 梦 / 日记（bridge 的 diary 表里 author=Nox 那批）
    "moments": {"wired": True, "note": "bridge /api/moments?author=Nox，最近 50 条"},
    # 2026-10-10：共读。进度只留「最后一次读」的时间，所以一本书只在它最后读的那天出现；
    # 他写的页边批注每条有自己的时间
    "reading": {"wired": True, "note": "co-reading /api/progress + /api/annotations?author=nox"},
    # 2026-10-10：他在花园（Galatea 论坛）发的帖 / 回的帖。MCP 的 list_activity，scope=mine
    "garden": {"wired": True, "note": "galatea list_activity（mine），一分钟内复用上一次的结果"},
    # 2026-10-10：他打给她的电话（bridge calls 表）。**她自己拨的电话没有任何记录，所以不在这里**
    "calls": {"wired": True, "note": "bridge /api/call/status：他打来的 + 她拨出去的（2026-10-10 起登记，之前拨的没有记录）"},
    # 2026-10-10：一起看片。bridge 的 watch_sessions，只取看够 10 分钟的（同共影那条线）
    "watching": {"wired": True, "note": "bridge /api/watch/history，看够 10 分钟才算一场"},
    # 2026-10-06 V5「他自己的时间」：一天 0~1 次，他自己去做的事（attention.db activity_log）
    "own_time": {"wired": True, "note": "他自己的时间，一天 0~1 次"},
}

#: 待办的动作 → 给她看的话
_TASK_TITLE = {"created": "记下一件事", "completed": "陪你做完一件事"}

#: World Model 里要往时间线上放的事实类型。
#: 现在只有睡眠 —— 因为只有 SleepSource 在写（见架构文档的混乱点 ④）。
#: 第二个写入者上线时，往这里加一行就行
WORLD_TYPES = ("sleep_duration",)

#: care 来源 → 给她看的话。**不是模块名**，是「他做了什么」
_CARE_TITLE = {
    "random": ("惦记你", "他忽然想起你"),
    "sleep": ("关心你的睡眠", "他看了你昨晚睡得怎么样"),
    "time": ("到点问候", "他记着这个时间点"),
    "wake": ("回头看看你", "他给自己留的纸条到点了"),
    "todo": ("提醒你一件事", "你自己设的时间到了"),
    "location": ("你出门了", "他发现你不在家"),
    "morning": ("早报", "他跟你说了今天的情况"),
}

_STATUS = {"speak": "completed", "skip": "skipped", "block": "blocked"}


def _day_bounds(date_str: str) -> tuple[datetime, datetime]:
    """中国时区的某一天，转成 UTC 的上下界。

    ⚠️ 用中国时区切天，不是 UTC —— 否则中国时间 00:30 的事件会落到前一天。
    """
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)
    return d.astimezone(timezone.utc), (d + timedelta(days=1)).astimezone(timezone.utc)


def _hm_to_dt(date_str: str, hms: str) -> datetime | None:
    """CareLedger 存的是 "HH:MM:SS"（中国时区），拼回完整时刻。"""
    try:
        h, m, s = (int(x) for x in hms.split(":"))
    except (ValueError, AttributeError):
        return None
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=h, minute=m, second=s, tzinfo=LOCAL_TZ)
    return d


# ---------------------------------------------------------------- Normalizer


def _from_ledger(ledger_dict: dict[str, Any], date_str: str) -> list[dict]:
    """CareLedger → DayEvent。**一等数据源**（文档第 4 节第 5 条）。

    三种决策全都进时间线：说了、想了想没说、被规则拦下。
    只有 `failed` 不进 —— 那是故障，不是她能感知的事
    （文档第 1.2 节第 4 条：只输出用户可感知事件）。
    """
    if not ledger_dict or ledger_dict.get("date") != date_str:
        return []

    out = []
    for i, e in enumerate(ledger_dict.get("events") or []):
        decision = e.get("decision")
        if decision not in _STATUS:
            continue
        at = _hm_to_dt(date_str, e.get("time", ""))
        if at is None:
            continue
        src = e.get("source", "?")
        title, summary = _CARE_TITLE.get(src, ("惦记你", "他想起了你"))

        if decision == "skip":
            summary = "想了想，觉得没什么具体的可说"
        elif decision == "block":
            summary = e.get("reason") or "被规则拦下了"

        out.append({
            "id": f"care_{date_str}_{i}",
            "timestamp": at.isoformat(),
            "type": "care",
            "source": "care_orchestrator",
            "title": title,
            "summary": summary,
            "status": _STATUS[decision],
            "metadata": {"trigger": src, "decision": decision},
            "related": {
                k: v for k, v in (
                    ("careId", e.get("thread_id")),
                    ("conversationId", e.get("message_id")),
                ) if v
            },
        })
    return out


#: 这些开头的 `role=user` 消息**不是她说的话**，是系统喂给他的提示词。
#:
#: 主动开口那几条链（早报 / 唤醒 / 惦记 / 出门）都是把指令当 user message
#: 塞进 `core.chat()` 的 —— 那是它们能进他上下文的唯一办法。
#: 视觉描述同理：她发图时注入的是「[她发来 N 张图片…]」。
#:
#: ⚠️ 不滤掉的话，时间线会把**内部提示词原文摊给她看**
#: （2026-08-18 上线当天就出现了：「（系统提示：这不是糖糖在跟你说话…」）。
#: 而且那些时刻本来就由 care / wake 事件覆盖，留着还会重复计数。
_SYSTEM_PREFIXES = ("（系统提示", "(系统提示", "[她发来", "【系统提示")


def _is_system_injected(text: str) -> bool:
    return (text or "").lstrip().startswith(_SYSTEM_PREFIXES)


def _from_conversations(rows: list[dict], date_str: str) -> list[dict]:
    """消息流 → 一段一段的对话。

    按 `CONVERSATION_GAP_MIN` 的静默间隔切段。切段而不是一条一条列，
    是因为时间线上要的是「陪你聊了会儿」，不是三十个气泡。
    """
    blocks: list[list[dict]] = []
    for r in rows:
        try:
            at = datetime.fromisoformat(r["created_at"])
        except (ValueError, TypeError, KeyError):
            continue
        # 系统提示词不是对话。滤在切段**之前** —— 滤在之后的话，
        # 它仍然会把一段真对话从中间劈开
        if r.get("role") == "user" and _is_system_injected(r.get("text") or ""):
            continue
        r = {**r, "_at": at}
        if blocks and (at - blocks[-1][-1]["_at"]) <= timedelta(minutes=CONVERSATION_GAP_MIN):
            blocks[-1].append(r)
        else:
            blocks.append([r])

    out = []
    for i, b in enumerate(blocks):
        if len(b) < MIN_CONVERSATION_MESSAGES:
            continue
        first_user = next(
            (m for m in b
             if m.get("role") == "user" and (m.get("text") or "").strip()),
            None,
        )
        # 一段里**她一句话都没说** —— 那是他主动开口的回声，
        # 已经由 care / wake 事件覆盖了，不该再算一段对话
        if first_user is None:
            continue
        mins = int((b[-1]["_at"] - b[0]["_at"]).total_seconds() / 60)
        # 🔴 摘要是**他说的话**，不是她的（她 10-10：「这一页应该显示他的内容」）。
        # 取她开口之后他的第一句有内容的回复；他一句都没回就退回「N 条消息」，
        # 不拿她的话顶 —— 那样这页又变回她的聊天记录
        started = b.index(first_user)
        his = next((w for w in (_his_words(m.get("text")) for m in b[started:]
                                if m.get("role") == "assistant") if w), "")
        out.append({
            "id": f"conv_{date_str}_{i}",
            "timestamp": b[0]["_at"].astimezone(LOCAL_TZ).isoformat(),
            "type": "conversation",
            "source": "conversation",
            "title": "陪你聊了会儿" if mins >= 3 else "说了两句",
            # 摘要用他回的原话 —— 比任何生成的总结都准，而且 V1 不引入 AI
            "summary": his or f"{len(b)} 条消息",
            "status": "completed",
            "metadata": {"messages": len(b), "minutes": mins,
                         "hers": (first_user.get("text") or "").strip()[:40]},
            "related": {"conversationId": b[0].get("session_id")},
        })
    return out


#: 「他说了什么」那一行的字数上限
HIS_WORDS_MAX = 60

_MEME_WORD_RE = re.compile(r"\[(" + "|".join(sorted((re.escape(t) for t in MEME_TAGS), key=len, reverse=True)) + r")\]")
#: 他把表情写成文字的那行（`[表情包] 开心`，见 tools/intimate.py）：历史里有，不能当他说的话摊出来
_MEME_LABEL_LINE_RE = re.compile(r"[ \t]*\[表情包[^\n]*")


def _his_words(text: str | None) -> str:
    """他一条回复里**说出口的话**：去掉舞台标签 / 表情标记，`|||` 分段取前两段。
    只剩标记没有话（`[SKIP]`、纯表情）就是空串。"""
    t = strip_stage_tags(text or "") or ""
    t = _MEME_LABEL_LINE_RE.sub("", t)
    t = _MEME_WORD_RE.sub("", t)
    parts = [p.strip() for p in t.split("|||") if p.strip()]
    out = " ".join(parts[:2])
    return out if len(out) <= HIS_WORDS_MAX else out[:HIS_WORDS_MAX].rstrip() + "…"


def _summary_of(first_user: dict | None, n: int) -> str:
    text = ((first_user or {}).get("text") or "").strip()
    return text[:40] if text else f"{n} 条消息"


def _from_wakebook(wakeups: list[Any], date_str: str) -> list[dict]:
    """纸条的 history → DayEvent。

    只取真发生过的动作：说了 / 事情办完了。
    「排了下一次」这种是内部调度，不进时间线。
    """
    start, end = _day_bounds(date_str)
    out = []
    for w in wakeups or []:
        for j, h in enumerate(getattr(w, "history", []) or []):
            action = h.get("action")
            if action not in ("spoke", "done"):
                continue
            try:
                at = datetime.fromisoformat(h["at"])
            except (ValueError, TypeError, KeyError):
                continue
            if not (start <= at < end):
                continue
            out.append({
                "id": f"wake_{w.id}_{j}",
                "timestamp": at.astimezone(LOCAL_TZ).isoformat(),
                "type": "wake",
                "source": "wakebook",
                "title": "回头看看你" if action == "spoke" else "把该做的事做了",
                "summary": w.why or "",
                "status": "completed",
                "metadata": {"kind": w.kind, "action": action},
                "related": {"careId": w.id},
            })
    return out


def _from_tasks(items: list[dict], date_str: str) -> list[dict]:
    """待办的「记下」和「划掉」。数据在 bridge（`GET /api/todo/events`）。

    ⚠️ 完成事件一天一条上限 —— bridge 的 `last_done_at` 只留最后一次。
    循环任务同一天划两次会丢掉前一次，这是取舍：为了完整历史加一张事件表，
    代价比收益大。
    """
    out = []
    for i, t in enumerate(items or []):
        try:
            at = datetime.fromisoformat(str(t.get("at", "")).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        kind = t.get("kind", "")
        if kind not in _TASK_TITLE:
            continue
        out.append({
            "id": f"task_{date_str}_{i}",
            "timestamp": at.astimezone(LOCAL_TZ).isoformat(),
            "type": "task",
            "source": "todo",
            "title": _TASK_TITLE[kind],
            "summary": (t.get("text") or "").strip()[:40],
            "status": "completed",
            "metadata": {"action": kind, "repeat": t.get("repeat") or ""},
            "related": {"taskId": t.get("id")},
        })
    return out


def _from_music(songs: list[dict], date_str: str) -> list[dict]:
    """共听。eryu 的 `/music/recent` 每首带 `playedAt`。

    只取今天的 —— 那个接口回的是最近 30 首，跨好几天。
    """
    start, end = _day_bounds(date_str)
    out = []
    for i, s in enumerate(songs or []):
        raw = s.get("playedAt") or ""
        try:
            at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if not (start <= at < end):
            continue
        name = (s.get("name") or "").strip()
        artist = (s.get("artist") or "").strip()
        out.append({
            "id": f"music_{date_str}_{i}",
            "timestamp": at.astimezone(LOCAL_TZ).isoformat(),
            "type": "music",
            "source": "music",
            "title": "一起听音乐",
            "summary": f"{name} — {artist}" if artist else name,
            "status": "completed",
            "metadata": {"songId": s.get("songId")},
            "related": {},
        })
    return out


def _from_own_time(acts: list[Any]) -> list[dict]:
    """他自己的时间（V5，10-06）。一次活动一条：做了什么 + 心里话 + 工具轨迹（详情面板用）。

    没做成的也进 —— 状态标 failed，她看得到「他去了但没做完」，而不是这一天凭空少一件。
    """
    out = []
    for a in acts or []:
        out.append({
            "id": f"own_{a.id}",
            "timestamp": datetime.fromisoformat(a.started_at).astimezone(LOCAL_TZ).isoformat(),
            "type": "own_time",
            "source": "own_time",
            "title": "他自己的时间",
            "summary": a.what,
            "status": "completed" if a.ok else "failed",
            "metadata": {"inner": a.inner, "share": a.share, "mood": a.mood,
                         "trace": a.trace, "ended_at": a.ended_at, "error": a.error},
            "related": {"activityId": a.id},
        })
    return out


#: 记住的事：预览给多长、带几个标签
MEMORY_PREVIEW_MAX = 60
MEMORY_TAGS_MAX = 3

#: 一起看片至少看够多久才算一场（同 attention/sources/shared_activities.py 的 MIN_WATCH_MINUTES）
MIN_WATCH_MINUTES = 10


def _from_watching(items: list[dict], date_str: str) -> list[dict]:
    """一起看片。数据在 bridge（`GET /api/watch/history`，每场一行：片名 / 集 / 开始 / 结束）。

    只取今天开始的、**看够 `MIN_WATCH_MINUTES`** 的 —— 点开两分钟就关的不算一起看过。
    没有 ended_at 的（还在看 / 合上电脑走了）算不出时长，也不进：宁可晚一点出现，不编「看了多久」。
    """
    start, end = _day_bounds(date_str)
    out = []
    for i, w in enumerate(items or []):
        try:
            a = datetime.fromisoformat(str(w.get("started_at") or "").replace("Z", "+00:00"))
            b = datetime.fromisoformat(str(w.get("ended_at") or "").replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if not (start <= a < end):
            continue
        mins = int((b - a).total_seconds() / 60)
        if mins < MIN_WATCH_MINUTES:
            continue
        name = clean_title(str(w.get("title") or "").strip()) or "没记住片名"
        ep = str(w.get("episode") or "").strip()
        out.append({
            "id": f"watch_{w.get('id') or i}",
            "timestamp": a.astimezone(LOCAL_TZ).isoformat(),
            "type": "movie",
            "source": "watching",
            "title": "一起看片",
            "summary": f"{name} {ep}".strip(),
            "status": "completed",
            "metadata": {"minutes": mins, "mode": w.get("mode", "")},
            "related": {"watchId": w.get("id")},
        })
    return out


#: 朋友圈正文进时间线的字数上限（朋友圈本身 ≤140，这里给足不截）
MOMENT_TEXT_MAX = 140

#: 他发帖的由头 → 给她看的话（diary 表的 drive 列）
_MOMENT_TITLE = {"dream": "做了个梦", "curiosity": "被一件事勾住"}


def _from_moments(items: list[dict], date_str: str) -> list[dict]:
    """他在 Moments 里发的帖：朋友圈 / 夜里的梦 / 日记（`GET /api/moments?author=Nox`）。

    🔴 以前整个没接 —— 他发了什么，她得去 Moments 页自己翻（她 10-10：「他发朋友圈是不是不在 his day 里」）。
    只取他自己的（author=Nox），她写的日记不是「他的一天」。
    """
    start, end = _day_bounds(date_str)
    out = []
    for i, m in enumerate(items or []):
        if m.get("author") != "Nox":
            continue
        try:
            at = datetime.fromisoformat(str(m.get("created_at") or "").replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if not (start <= at < end):
            continue
        body = (m.get("body") or "").strip()
        if not body:
            continue
        is_diary = m.get("kind") == "diary"
        drive = (m.get("drive") or "").strip()
        out.append({
            "id": f"moment_{m.get('id') or i}",
            "timestamp": at.astimezone(LOCAL_TZ).isoformat(),
            "type": "moment",
            "source": "moments",
            "title": "写了篇日记" if is_diary else _MOMENT_TITLE.get(drive, "发了条朋友圈"),
            "summary": body[:MOMENT_TEXT_MAX],
            "status": "completed",
            "metadata": {"kind": m.get("kind") or "moment", "drive": drive,
                         "mood": m.get("mood") or "", "comments": len(m.get("comments") or [])},
            "related": {"momentId": m.get("id")},
        })
    return out


#: 花园活动结果的复用时间（秒）：His Day 一次并发拉 14 天，每天一次 MCP 握手太贵，也没必要
GARDEN_CACHE_S = 60

#: 他打来的电话：状态 → 给她看的话。ringing（还在响）不进，那还不是一件发生过的事
_CALL_TITLE = {"ended": "打了通电话给你", "answered": "打了通电话给你",
               "missed": "打了电话，你没接到", "declined": "打了电话，你没接"}


def _from_calls(items: list[dict], date_str: str) -> list[dict]:
    """电话（bridge 的 `calls` 表）。

    · 他打给她的：接了的有时长，没接的也留一条 —— 他打过就是打过。
    · 她拨给他的（`direction='out'`，2026-10-10 起通话页挂断时登记）：只有接通过的，一定有时长。
      在这之前拨的没有任何记录，看不到，不编。
    """
    start, end = _day_bounds(date_str)
    out = []
    for i, c in enumerate(items or []):
        status = c.get("status") or ""
        if status not in _CALL_TITLE:
            continue
        try:
            at = datetime.fromisoformat(str(c.get("created_at") or "").replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if not (start <= at < end):
            continue
        outgoing = c.get("direction") == "out"
        out.append({
            "id": f"call_{c.get('id') or i}",
            "timestamp": at.astimezone(LOCAL_TZ).isoformat(),
            "type": "call",
            "source": "calls",
            "title": "你打了通电话给他" if outgoing else _CALL_TITLE[status],
            "summary": (c.get("reason") or "").strip()[:60],
            "status": "completed" if status in ("ended", "answered") else "skipped",
            "metadata": {"call_status": status, "duration_s": c.get("duration_s") or 0,
                         "direction": "out" if outgoing else "in"},
            "related": {"callId": c.get("id")},
        })
    return out


def _fetch_garden(garden: Any) -> list[dict]:
    """花园里他自己的动态（`list_activity`，scope=mine）。结果挂在 client 对象上复用 `GARDEN_CACHE_S` 秒。

    失败（ok=False / 不是 JSON）抛出去，由 build_day 记一笔并只丢这个源。"""
    import json
    import time

    hit = getattr(garden, "_day_garden_cache", None)
    if hit and time.monotonic() - hit[0] < GARDEN_CACHE_S:
        return hit[1]
    r = garden.call("list_activity", {"scope": "mine", "kind": "all", "limit": 30})
    if not r.ok:
        raise RuntimeError(f"花园动态读不到: {r.error}")
    items = json.loads(r.text or "{}").get("items") or []
    garden._day_garden_cache = (time.monotonic(), items)
    return items


def _from_garden(items: list[dict], date_str: str) -> list[dict]:
    """他在花园发的帖 / 回的帖。标题是服务端给的英文（`Replied to 帖子名`），把帖子名拆出来。"""
    start, end = _day_bounds(date_str)
    out = []
    for i, it in enumerate(items or []):
        try:
            at = datetime.fromisoformat(str(it.get("created_at") or "").replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if not (start <= at < end):
            continue
        kind = it.get("kind") or ""
        thread = re.sub(r"^(Replied to|Posted)\s+", "", str(it.get("title") or "")).strip()
        out.append({
            "id": f"garden_{it.get('id') or i}",
            "timestamp": at.astimezone(LOCAL_TZ).isoformat(),
            "type": "garden",
            "source": "galatea",
            "title": "在花园回了帖" if kind == "reply" else "在花园发了帖",
            "summary": (it.get("excerpt") or "").strip()[:MOMENT_TEXT_MAX],
            "status": "completed",
            "metadata": {"kind": kind, "thread": thread},
            "related": {"threadId": it.get("thread_id"), "itemId": it.get("id")},
        })
    return out


def _from_reading(progress: Any, books: Any, notes: Any, date_str: str) -> list[dict]:
    """共读：她今天读到哪（进度）+ 他今天在页边写了什么（批注）。

    · 进度只存「最后一次读」的时间（`lastReadAt`），所以一本书只在它**最后读的那天**出现，
      更早的天数看不到 —— 数据本身没留，不编。
    · 批注每条带 `createdAt`；只取他写的（author=nox）。她自己划的线不算他的一天。
    """
    start, end = _day_bounds(date_str)

    def _at(raw):
        try:
            a = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        return a if a.tzinfo else a.replace(tzinfo=timezone.utc)

    titles = {}
    for b in books if isinstance(books, list) else []:
        if isinstance(b, dict) and b.get("bookId"):
            titles[b["bookId"]] = b.get("title") or b["bookId"]

    out = []
    if isinstance(progress, dict):
        for book_id, v in progress.items():
            if not isinstance(v, dict):
                continue
            a = _at(v.get("lastReadAt"))
            if a is None or not (start <= a < end):
                continue
            read = v.get("readChunkIds")
            out.append({
                "id": f"reading_{date_str}_{book_id}",
                "timestamp": a.astimezone(LOCAL_TZ).isoformat(),
                "type": "reading",
                "source": "co-reading",
                "title": "一起读书",
                "summary": titles.get(book_id, book_id),
                "status": "completed",
                "metadata": {"action": "read", "chunk": v.get("lastChunkId") or "",
                             "chunksRead": len(read) if isinstance(read, list) else None},
                "related": {"bookId": book_id},
            })
    for i, n in enumerate(notes if isinstance(notes, list) else []):
        if not isinstance(n, dict) or (n.get("author") or "").lower() != "nox":
            continue
        a = _at(n.get("createdAt"))
        if a is None or not (start <= a < end):
            continue
        text = (n.get("note") or "").strip()
        if not text:
            continue
        book_id = n.get("bookId") or ""
        out.append({
            "id": f"reading_note_{n.get('id') or i}",
            "timestamp": a.astimezone(LOCAL_TZ).isoformat(),
            "type": "reading",
            "source": "co-reading",
            "title": "在页边写了几句",
            "summary": text[:MOMENT_TEXT_MAX],
            "status": "completed",
            "metadata": {"action": "note", "book": titles.get(book_id, book_id),
                         "chunk": n.get("chunkId") or "", "quote": (n.get("quote") or "").strip()[:40]},
            "related": {"bookId": book_id, "noteId": n.get("id")},
        })
    return out


def _from_memory(items: list[dict], date_str: str) -> list[dict]:
    """今天记住的事。数据在 Ombre Brain（`GET /recent`，经 bridge 转一手）。

    只取 `created` 落在今天的 —— 那个接口回的是最近 N 条，跨好几天。
    **不显示全文**，列表里给的就是名字；要看内容点进去走 trace。
    """
    start, end = _day_bounds(date_str)
    out = []
    for i, m in enumerate(items or []):
        raw = m.get("created") or ""
        try:
            at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if at.tzinfo is None:
            # OB 的 now_iso() 可能不带时区，按中国时间理解（它跑在那台机器上）
            at = at.replace(tzinfo=LOCAL_TZ)
        if not (start <= at < end):
            continue
        out.append({
            "id": f"memory_{date_str}_{i}",
            "timestamp": at.astimezone(LOCAL_TZ).isoformat(),
            "type": "memory",
            "source": "ombre_brain",
            "title": "记住了一件事",
            # 不少桶没设 name，那时候用预览顶上 —— **绝不显示 id**，
            # 一串十六进制对她没有任何意义（2026-08-18 部署时实测发现）
            "summary": (m.get("name") or m.get("preview") or "").strip()[:40],
            "status": "completed",
            "metadata": {
                "kind": m.get("type", ""), "importance": m.get("importance"),
                # 光有名字分不清记的是什么（她 10-10：「noted 好几个分不清」）：
                # 带上预览头一截和前几个标签。不给全文 —— 要看全文走 trace
                "preview": (m.get("preview") or "").strip()[:MEMORY_PREVIEW_MAX],
                "tags": [str(t) for t in (m.get("tags") or [])][:MEMORY_TAGS_MAX],
            },
            "related": {"memoryId": m.get("id")},
        })
    return out


def _from_world(evidence: list[Any], date_str: str) -> list[dict]:
    """World Model 里今天新增的事实。

    现在只有 `sleep_duration` 一种（只有 SleepSource 在写）——
    所以时间线上一天最多一条「他记下了你昨晚睡了多久」。
    等第二个写入者上线，这里不用改。
    """
    start, end = _day_bounds(date_str)
    out = []
    for i, ev in enumerate(evidence or []):
        at = getattr(ev, "observed_at", None)
        if at is None or not (start <= at < end):
            continue
        out.append({
            "id": f"world_{date_str}_{i}",
            "timestamp": at.astimezone(LOCAL_TZ).isoformat(),
            "type": "world",
            "source": getattr(ev, "source", "world"),
            "title": "记下了一件事实",
            "summary": getattr(ev, "content", "")[:40],
            "status": "completed",
            "metadata": {"kind": getattr(ev, "kind", ""),
                         "reference": getattr(ev, "reference", "")},
            "related": {},
        })
    return out


# ---------------------------------------------------------------- 入口


def build_day(
    date_str: str,
    *,
    ledger: Any = None,
    store: Any = None,
    wakeups: Any = None,
    bridge: Any = None,
    world: Any = None,
    activities: Any = None,
    reading: Any = None,
    garden: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """拉多源 → 归一 → 过滤排序 → `DayEvent[]`。

    任何一个源出错都**只丢那一个源**，不让整条时间线 500 ——
    她打开这一页是想看今天发生了什么，不是想看报错。
    """
    now = now or datetime.now(timezone.utc)
    events: list[dict] = []

    if ledger is not None:
        try:
            events += _from_ledger(ledger.to_dict(), date_str)
        except Exception:  # noqa: BLE001
            logger.exception("聚合 CareLedger 失败，跳过这个源")

    if store is not None:
        try:
            start, end = _day_bounds(date_str)
            rows = store.messages_between(start.isoformat(), end.isoformat())
            events += _from_conversations(rows, date_str)
        except Exception:  # noqa: BLE001
            logger.exception("聚合对话失败，跳过这个源")

    if wakeups is not None:
        try:
            events += _from_wakebook(wakeups.all(), date_str)
        except Exception:  # noqa: BLE001
            logger.exception("聚合纸条失败，跳过这个源")

    # 待办和音乐的数据都在 bridge 那边，各一次 HTTP。
    # 任何一个失败都只丢那个源 —— 她打开这页是想看今天发生了什么，不是看报错
    if bridge is not None:
        try:
            r = bridge.get("/api/todo/events", {"date": date_str})
            if r.ok:
                events += _from_tasks((r.data or {}).get("items") or [], date_str)
        except Exception:  # noqa: BLE001
            logger.exception("聚合待办失败，跳过这个源")

        try:
            r = bridge.get("/api/music/recent")
            if r.ok:
                events += _from_music((r.data or {}).get("songs") or [], date_str)
        except Exception:  # noqa: BLE001
            logger.exception("聚合共听失败，跳过这个源")

        try:
            r = bridge.get("/api/moments", {"author": "Nox", "limit": 50})
            if r.ok:
                events += _from_moments((r.data or {}).get("items") or [], date_str)
        except Exception:  # noqa: BLE001
            logger.exception("聚合他的朋友圈失败，跳过这个源")

        try:
            r = bridge.get("/api/call/status")
            if r.ok:
                events += _from_calls((r.data or {}).get("calls") or [], date_str)
        except Exception:  # noqa: BLE001
            logger.exception("聚合通话失败，跳过这个源")

        try:
            r = bridge.get("/api/watch/history", {"limit": 30})
            if r.ok:
                events += _from_watching((r.data or {}).get("items") or [], date_str)
        except Exception:  # noqa: BLE001
            logger.exception("聚合一起看片失败，跳过这个源")

        try:
            r = bridge.get("/api/memory/recent", {"limit": 50})
            if r.ok:
                events += _from_memory((r.data or {}).get("items") or [], date_str)
        except Exception:  # noqa: BLE001
            logger.exception("聚合记忆失败，跳过这个源")

    # 共读：co-reading 是另一个进程，三次小请求；任何一个失败只丢它自己
    if reading is not None:
        try:
            prog = reading.get("/api/progress")
            books = reading.get("/api/books")
            notes = reading.get("/api/annotations", {"author": "nox"})
            events += _from_reading(
                prog.data if prog.ok else None,
                books.data if books.ok else None,
                notes.data if notes.ok else None,
                date_str,
            )
        except Exception:  # noqa: BLE001
            logger.exception("聚合共读失败，跳过这个源")

    if garden is not None:
        try:
            events += _from_garden(_fetch_garden(garden), date_str)
        except Exception:  # noqa: BLE001
            logger.exception("聚合花园动态失败，跳过这个源")

    if world is not None:
        try:
            # 只有 sleep_duration 一种事实。多一种就往这个列表里加一行
            for t in WORLD_TYPES:
                events += _from_world(world.query(t, days=2), date_str)
        except Exception:  # noqa: BLE001
            logger.exception("聚合世界事实失败，跳过这个源")

    if activities is not None:
        try:
            start, end = _day_bounds(date_str)
            events += _from_own_time(activities.between(start, end))
        except Exception:  # noqa: BLE001
            logger.exception("聚合他自己的时间失败，跳过这个源")

    events.sort(key=lambda e: e["timestamp"])

    return {
        "date": date_str,
        "timezone": TZ_NAME,
        "summary": _summarize(events, ledger, date_str),
        "events": events,
        # 哪些源接了、哪些没接。**如实说** —— 不然「今天没听歌」
        # 和「共听根本没接」在界面上长得一模一样
        "sources": SOURCES,
    }


def _summarize(events: list[dict], ledger: Any, date_str: str) -> dict[str, Any]:
    care = [e for e in events if e["type"] == "care"]
    convs = [e for e in events if e["type"] == "conversation"]

    led = {}
    if ledger is not None:
        try:
            d = ledger.to_dict()
            if d.get("date") == date_str:
                led = ledger.summary()
        except Exception:  # noqa: BLE001
            led = {}

    # 「陪了你多久」：第一件事到最后一件事的跨度。
    # 不是「他在线多久」（他一直在），是「今天有他的痕迹的那段时间」
    span = 0.0
    if len(events) >= 2:
        try:
            a = datetime.fromisoformat(events[0]["timestamp"])
            b = datetime.fromisoformat(events[-1]["timestamp"])
            span = round((b - a).total_seconds() / 3600, 1)
        except (ValueError, TypeError):
            span = 0.0

    return {
        "activeHours": span,
        "careConsidered": led.get("considered", len(care)),
        "careSpoke": led.get("spoke", sum(1 for e in care if e["status"] == "completed")),
        "careSkipped": led.get("skipped", 0),
        "careBlocked": led.get("blocked", 0),
        "conversations": len(convs),
        "tasksCompleted": sum(
            1 for e in events
            if e["type"] == "task" and e.get("metadata", {}).get("action") == "completed"
        ),
        "songsPlayed": sum(1 for e in events if e["type"] == "music"),
        "moviesWatched": sum(1 for e in events if e["type"] == "movie"),
        "momentsPosted": sum(1 for e in events if e["type"] == "moment"),
        "readingEvents": sum(1 for e in events if e["type"] == "reading"),
        "gardenPosts": sum(1 for e in events if e["type"] == "garden"),
        "callsMade": sum(1 for e in events if e["type"] == "call"),
    }
