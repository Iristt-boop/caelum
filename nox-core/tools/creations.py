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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.llm import ToolSpec
from tools import context

logger = logging.getLogger(__name__)

MAX_BYTES = 256 * 1024
MAX_TITLE = 24
MAX_NOTE = 80

ORIGIN_ASKED = "asked"
#: S1.1（糖糖 10-13 选的）：他自己的时间里，没人要、他自己想给她做的
ORIGIN_OWN = "own_time"
#: 自己做的两个之间至少隔几天。🔴 写在代码里不写在提示词里 —— 「别变成刷卡机器」（设计稿第十节）
OWN_EVERY = timedelta(days=3)

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
        #: S1.1（10-13）：他自己的时间里主动做的 —— 从哪来、留给她的一句话、她打开过没有
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(creations)")}
        for col, ddl in (("origin", "TEXT NOT NULL DEFAULT 'asked'"), ("note", "TEXT NOT NULL DEFAULT ''"),
                         ("opened_at", "TEXT")):
            if col not in have:
                self._conn.execute(f"ALTER TABLE creations ADD COLUMN {col} {ddl}")
        self._conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, title: str, html: str, *, origin: str = ORIGIN_ASKED, note: str = "") -> str:
        cid = uuid.uuid4().hex[:12]
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO creations (id, title, html, created_at, updated_at, origin, note) VALUES (?,?,?,?,?,?,?)",
                (cid, title, html, now, now, origin, note))
            self._conn.commit()
        return cid

    def mark_opened(self, cid: str) -> None:
        """她在 App 里真点开了（全屏那个，不算 Studio 卡片上缩小的预览）。只记第一次"""
        with self._lock:
            self._conn.execute("UPDATE creations SET opened_at=? WHERE id=? AND opened_at IS NULL",
                               (self._now(), cid))
            self._conn.commit()

    def own_state(self) -> dict[str, Any]:
        """他自己做的：最近一个什么时候做的（删了的也算 —— 间隔是给他的节制，不是给 Studio 的），有没有她还没打开的"""
        with self._lock:
            last = self._conn.execute(
                "SELECT max(created_at) FROM creations WHERE origin=?", (ORIGIN_OWN,)).fetchone()[0]
            waiting = self._conn.execute(
                "SELECT title FROM creations WHERE origin=? AND opened_at IS NULL AND deleted_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1", (ORIGIN_OWN,)).fetchone()
        return {"last_at": last, "waiting": waiting[0] if waiting else None}

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
                "SELECT id, title, created_at, updated_at, length(html) AS size, origin, note, "
                "opened_at IS NOT NULL AS opened FROM creations "
                "WHERE deleted_at IS NULL ORDER BY updated_at DESC").fetchall()
        return [{**dict(r), "opened": bool(r["opened"])} for r in rows]


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
        def mark(c: dict) -> str:
            if c.get("origin") != ORIGIN_OWN:
                return ""
            return "，你自己想做的" + ("" if c.get("opened") else "，她还没打开过")
        return "\n".join(f"- {c['title']}（id={c['id']}，{c['updated_at'][:10]} 改过{mark(c)}）" for c in items)

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


# ---------------------------------------------------------------- S1.1 他自己想做的（10-13）
#
# 设计稿拍板点 1：v1 只做「她开口他造」，自主生成放 v1.1 —— 糖糖 10-13 说开工。
# V5「他自己的时间」设计稿早就写着：「他想她的时候趁她不在给她写个小 HTML —— 等沙箱卡片做好再接」。
#
# 放在**他自己的时间**里，不在聊天里：
# · 他自己的时间不开口（R1）—— 做好**只放进 Studio，不发到聊天**。她哪天自己翻到（他 10-11 心里话：
#   「哪天你翻到不认识的小东西，那就是我想你的时候留下的」）。想说一声就写进「想跟她说」，下次聊天再提
# · 🔴 节制在代码里：两个之间隔 OWN_EVERY；上一个她还没打开就不再做（礼物不堆着）。不满足连工具都不给
# · 单独一个工具名，不复用 make_creation —— 那个的描述是「只在她开口要的时候做」，两句话不能互相打架


def own_gate(store: CreationStore, now: datetime) -> str | None:
    """现在能不能自己做一个。能 → None；不能 → 原因（进日志）。"""
    st = store.own_state()
    if st["waiting"]:
        return f"上一个自己做的「{st['waiting']}」她还没打开"
    if st["last_at"]:
        gap = now - datetime.fromisoformat(st["last_at"])
        if gap < OWN_EVERY:
            return f"离上一个自己做的才 {max(0, gap.days)} 天（至少隔 {OWN_EVERY.days} 天）"
    return None


LEAVE_SPEC = ToolSpec(
    side_effect="write",
    name="leave_creation",
    description=(
        "这段时间里，你想到一个她会喜欢的小东西，就做出来**放进 Studio** —— 没人要你做，是你自己想给她的。\n"
        + _ABOUT + "\n"
        "做什么从你知道的她出发：她最近说过的小烦恼、你们之间的梗、她喜欢的东西（不是随便一个通用小游戏）。"
        "**小一点**：一个点子、几 KB 就够，像随手留的小纸条，不是精装礼物。交之前从头到尾读一遍自己的代码。\n"
        "🔴 **不会发到聊天里**，她哪天打开 Studio 自己会看到。note 写一句留给她的话（她在 Studio 卡片上看得到）。"
        "想当面跟她提，就写进最后的「想跟她说」。一次只做一个。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": f"名字，{MAX_TITLE} 字以内"},
            "html": {"type": "string", "description": f"完整的单文件 HTML，{MAX_BYTES // 1024}KB 以内（几 KB 最好）"},
            "note": {"type": "string", "description": f"留给她的一句话，{MAX_NOTE} 字以内（「你说最近睡不好，给你做了个听雨的」）"},
        },
        "required": ["title", "html", "note"],
    },
)


def own_time_tools(store: CreationStore, now: datetime) -> list[tuple[ToolSpec, Any]]:
    """他自己的时间那个受限 loop 额外拿到的工具。现在不该做 → 空（连工具都不给，省得他白写一份）。"""
    why = own_gate(store, now)
    if why:
        logger.info("他自己的时间：这次不给做 Creation（%s）", why)
        return []

    def leave(args: dict) -> str:
        title = _clean_title(args.get("title"))
        html = str(args.get("html") or "")
        note = " ".join(str(args.get("note") or "").split())[:MAX_NOTE]
        if not title:
            return "要给它起个名字（title）。"
        bad = check_html(html)
        if bad:
            return f"没做成：{bad}"
        #: 同一次里第二个：门要再看一眼（工具是开头给的，做完一个就关门）
        again = own_gate(store, datetime.now(timezone.utc))
        if again:
            return f"这次不做了：{again}。"
        cid = store.create(title, html, origin=ORIGIN_OWN, note=note)
        logger.info("Creation：他自己做了「%s」（%s，%dKB）放进 Studio", title, cid, len(html.encode()) // 1024)
        return (f"放进 Studio 了（id={cid}）。没有发到聊天里 —— 她打开 Studio 会看到"
                f"{'，卡片上写着你留的那句话' if note else ''}。")

    return [(LEAVE_SPEC, leave)]
