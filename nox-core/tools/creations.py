"""Creations —— 他给她做的小东西（2026-10-11，设计稿 `Caelum-沙箱卡片-设计稿-2026-10-06.md` S1）。

一个 Creation = 一个**单文件网页**（HTML + CSS + JS 写在一起）。她说「做个小游戏 / 小工具」，
他写出来、存起来、发到聊天里；她在 App 的 **Studio** 里随时能翻出来再打开。
糖糖 10-11 定名：不叫「沙箱卡片」（那是 Aru 的叫法），叫 Creations，菜单叫 Studio。

## 定位（糖糖 10-11 认可）

**只做玩的和送的**：游戏、小玩具、可视化、一次性的小东西。
她真实生活里的数据（待办 / 饮食 / 记账）**不**做成 Creation —— Creation 的数据存在她手机上的沙箱里，
他看不见、不会提醒她（Aru 的待办卡就是这个坑）。这条写进工具描述，让他自己分得清。

## 这里只存 HTML

Creation 运行时存的数据（她在五子棋里下到哪了）在**她的设备上**，由 App 代管（设计稿 3.3），
这个库里永远只有他写的那串 HTML —— **不存她的任何真实数据**。

## edit 而不是重做（糖糖 10-11 认可）

重做 = 新 id = 她在里面存的东西没了、Studio 里堆出好几个版本、还要再花 1.5–3 万 token。
所以 `edit_creation` 保持同一个 id：小改「把这段换成那段」，大改整份替换；留一个上一版，改坏了能退回。
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.llm import ToolSpec
from tools import context

logger = logging.getLogger(__name__)

MAX_BYTES = 256 * 1024
MAX_TITLE = 24

_HTML_HINT = re.compile(r"<(html|body|div|canvas|svg|script|style|main|section)\b", re.I)


class CreationStore:
    """creations.db —— 和 orders.db 平级的独立小库（施工时定：不并进 sessions / world）。"""

    def __init__(self, path: str | Path) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS creations (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                html       TEXT NOT NULL,
                prev_html  TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            )"""
        )
        self._conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, title: str, html: str) -> str:
        cid = uuid.uuid4().hex[:12]
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO creations (id, title, html, created_at, updated_at) VALUES (?,?,?,?,?)",
                (cid, title, html, now, now))
            self._conn.commit()
        return cid

    def get(self, cid: str) -> dict | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM creations WHERE id=? AND deleted_at IS NULL", (cid,)).fetchone()
        return dict(r) if r else None

    def update(self, cid: str, *, html: str | None = None, title: str | None = None,
               keep_prev: bool = True) -> bool:
        cur = self.get(cid)
        if cur is None:
            return False
        with self._lock:
            self._conn.execute(
                "UPDATE creations SET html=?, title=?, prev_html=?, updated_at=? WHERE id=?",
                (html if html is not None else cur["html"],
                 title if title is not None else cur["title"],
                 cur["html"] if (keep_prev and html is not None) else cur["prev_html"],
                 self._now(), cid))
            self._conn.commit()
        return True

    def revert(self, cid: str) -> bool:
        """退回上一版（只留一版：退一次之后，「上一版」就是刚才那份 —— 再退一次 = 又回来了）。"""
        cur = self.get(cid)
        if cur is None or not cur["prev_html"]:
            return False
        with self._lock:
            self._conn.execute(
                "UPDATE creations SET html=?, prev_html=?, updated_at=? WHERE id=?",
                (cur["prev_html"], cur["html"], self._now(), cid))
            self._conn.commit()
        return True

    def delete(self, cid: str) -> bool:
        """软删：HTML 清空（不留着她不要了的东西），行留着只为让聊天里指向它的消息显示「已删」。"""
        with self._lock:
            n = self._conn.execute(
                "UPDATE creations SET deleted_at=?, html='', prev_html=NULL WHERE id=? AND deleted_at IS NULL",
                (self._now(), cid)).rowcount
            self._conn.commit()
        return n > 0

    def list(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, created_at, updated_at, length(html) AS size FROM creations "
                "WHERE deleted_at IS NULL ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def check_html(html: str) -> str | None:
    """不合格返回原因（给他看），合格返回 None。"""
    if not html or not html.strip():
        return "html 是空的。"
    size = len(html.encode("utf-8"))
    if size > MAX_BYTES:
        return f"太大了（{size // 1024}KB，上限 {MAX_BYTES // 1024}KB）。删掉用不上的部分，或者做得更小一点。"
    if not _HTML_HINT.search(html):
        return "这不像一个网页（找不到任何 HTML 标签）。要交一整份单文件 HTML。"
    return None


def _clean_title(t: Any) -> str:
    return " ".join(str(t or "").split())[:MAX_TITLE]


_ABOUT = (
    "Creation 是你给她做的**单文件网页小东西**（HTML+CSS+JS 写在一份里），存在 App 的 Studio 里，她随时能再打开。\n"
    "🔴 **只做玩的和送的**：小游戏、小玩具、可视化、一次性的小东西。她真实生活的数据（待办、饮食、记账）"
    "**不要**做成 Creation —— 那里面的数据存在她手机的沙箱里，你看不见、也提醒不了她；那些用你原来的工具。\n"
    "运行环境：手机竖屏为主（写 viewport meta）；**没有网络**（fetch / 外链图片 / CDN 全被封，所有代码和素材写在这一份里，"
    "图片用 data: 或 canvas / svg 画）；`localStorage` 可以照常用，App 替它保存，关掉再开还在；`alert` 能用。"
)

MAKE_SPEC = ToolSpec(
    side_effect="write",
    name="make_creation",
    description=(
        "给她做一个新的 Creation，做完会出现在聊天里，她点开就能用。\n" + _ABOUT + "\n"
        "**只在她开口要的时候做**（「做个…」「帮我弄个小游戏」）。一次一个，不刷屏。"
        "交之前把自己的代码从头到尾读一遍：按钮能点、状态能存、手机上不溢出。\n"
        "做完用一两句话告诉她怎么玩，别把代码念给她。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": f"名字，{MAX_TITLE} 字以内（「五子棋」「待会儿吃什么转盘」）"},
            "html": {"type": "string", "description": f"完整的单文件 HTML，{MAX_BYTES // 1024}KB 以内"},
        },
        "required": ["title", "html"],
    },
)

EDIT_SPEC = ToolSpec(
    side_effect="write",
    name="edit_creation",
    description=(
        "改一个已经做好的 Creation —— **同一个 id**，她在里面存的东西不会丢，Studio 里也不会多出一份。"
        "「哪里不顺手」「换个颜色」「加个功能」都用这个，**别为了改一点去重做一个新的**。\n"
        "三种改法选一种：\n"
        "· 小改：find + replace（在原代码里把 find 那段原样换成 replace；find 要在原文里**一字不差且只出现一次**）\n"
        "· 大改：html（整份替换）\n"
        "· 改坏了：undo=true（退回上一版）\n"
        "不确定原代码长什么样就先调 list_creations 看 id，再小改；改完会重新发到聊天里。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "find": {"type": "string", "description": "小改：原代码里要换掉的那段（一字不差）"},
            "replace": {"type": "string", "description": "小改：换成什么"},
            "html": {"type": "string", "description": "大改：整份新的 HTML"},
            "title": {"type": "string", "description": "顺便改名（可选）"},
            "undo": {"type": "boolean", "description": "退回上一版"},
        },
        "required": ["id"],
    },
)

SEND_SPEC = ToolSpec(
    side_effect="write",
    name="send_creation",
    description="把一个已经做好的 Creation 再发到聊天里（「把那个五子棋再发我」）。**不重做**，只重发。",
    parameters={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
)

LIST_SPEC = ToolSpec(
    side_effect="read",
    name="list_creations",
    description="你给她做过的 Creations（id、名字、最后改的时间）。她提起「上次那个小游戏」时先查这里。",
    parameters={"type": "object", "properties": {}},
)

DELETE_SPEC = ToolSpec(
    side_effect="write",
    name="delete_creation",
    description="她说不要了就删。删了 Studio 里就没有了，聊天里那条会显示已删。",
    parameters={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
)

SPECS = (MAKE_SPEC, EDIT_SPEC, SEND_SPEC, LIST_SPEC, DELETE_SPEC)


def _attach(cid: str, title: str) -> None:
    ctx = context.current()
    if ctx is not None:
        ctx.attachments.append({"type": "creation", "creation_id": cid, "title": title})


def make_handlers(store: CreationStore) -> dict[str, Any]:
    def make(args: dict) -> str:
        title = _clean_title(args.get("title"))
        html = str(args.get("html") or "")
        if not title:
            return "要给它起个名字（title）。"
        bad = check_html(html)
        if bad:
            return f"没做成：{bad}"
        cid = store.create(title, html)
        logger.info("Creation：做了「%s」（%s，%dKB）", title, cid, len(html.encode()) // 1024)
        _attach(cid, title)
        return f"做好了，id={cid}，已经发到聊天里，她在 Studio 里也能找到。"

    def edit(args: dict) -> str:
        cid = str(args.get("id") or "").strip()
        cur = store.get(cid)
        if cur is None:
            return f"没有 id={cid or '(空)'} 这个 Creation（删了或者 id 写错了），先调 list_creations。"
        title = _clean_title(args.get("title")) or None
        if args.get("undo"):
            if not store.revert(cid):
                return "没有上一版可以退。"
            how = "退回了上一版"
        elif args.get("html"):
            html = str(args["html"])
            bad = check_html(html)
            if bad:
                return f"没改：{bad}"
            store.update(cid, html=html, title=title)
            how = "整份换了"
        elif args.get("find") is not None and "replace" in args:
            find, rep = str(args["find"]), str(args.get("replace") or "")
            n = cur["html"].count(find) if find else 0
            if n != 1:
                return (f"没改：find 那段在原代码里出现了 {n} 次（要正好 1 次）。"
                        "把 find 写得更长一点、和原文一字不差，或者改用 html 整份替换。")
            html = cur["html"].replace(find, rep, 1)
            bad = check_html(html)
            if bad:
                return f"没改：{bad}"
            store.update(cid, html=html, title=title)
            how = "改了一处"
        elif title:
            store.update(cid, title=title)
            how = "改了名字"
        else:
            return "没说要怎么改：给 find+replace、html、undo 其中一种。"
        final = store.get(cid)
        logger.info("Creation：%s「%s」（%s）", how, final["title"], cid)
        _attach(cid, final["title"])
        return f"{how}，id 不变（她在里面存的东西还在），已经重新发到聊天里。"

    def send(args: dict) -> str:
        cid = str(args.get("id") or "").strip()
        cur = store.get(cid)
        if cur is None:
            return f"没有 id={cid or '(空)'} 这个 Creation，先调 list_creations。"
        _attach(cid, cur["title"])
        return f"「{cur['title']}」已经发到聊天里了。"

    def list_(args: dict) -> str:
        items = store.list()
        if not items:
            return "还没给她做过 Creation。"
        return "\n".join(f"- {c['title']}（id={c['id']}，{c['updated_at'][:10]} 改过）" for c in items)

    def delete(args: dict) -> str:
        cid = str(args.get("id") or "").strip()
        cur = store.get(cid)
        if cur is None:
            return f"没有 id={cid or '(空)'} 这个 Creation。"
        store.delete(cid)
        logger.info("Creation：删了「%s」（%s）", cur["title"], cid)
        return f"「{cur['title']}」删掉了。"

    return {"make_creation": make, "edit_creation": edit, "send_creation": send,
            "list_creations": list_, "delete_creation": delete}


def register_all(loop: Any, store: CreationStore) -> None:
    """登记顺序固定（缓存前缀）；在 server.py 工具串**最后**登记，前面的工具位置一个不动。"""
    handlers = make_handlers(store)
    for spec in SPECS:
        loop.register(spec, handlers[spec.name])
