"""Runtime Context —— 每轮塞到他手里的一张「工牌」（2026-10-11，设计稿 R1）。

> 糖糖 10-11：「你在哪」不该让他自己去查，应该每轮由 Caelum 塞到他手里。
> Guide 是百科全书，Runtime Context 是 GPS。

guide 第一篇就是 `00-where-am-i`，五天被读 0 次 —— 需要模型主动决定「我该查一下自己在哪」
的环境认知，可靠性太低。所以这一层**不经 classify_context 挑选、轻量路径也带**，
由 `Nox._dynamic` 放在动态提示的最前面（见 `Caelum-Runtime-Context-设计稿-2026-10-11.md`）。

## 只装随运行环境变化的东西

放：host、入口、当前对象的名字和集数、这个入口的能力类别。
不放：人格（静态前缀有）、工具说明（schema 有）、手册正文（第三层）、
**任何会每轮变的东西** —— DeepSeek 是自动前缀匹配，动态提示变一个字，后面的历史整段不命中
（`providers/time.py` 的实测）。所以共影只到「第几集」，**不写她停在几分几秒**：
那个已经在她那条消息里（bridge 拼的【共影】），写进来等于每次暂停冷一次缓存。

## 入口自己报，不从正文猜

`surface` 由调用方显式填（bridge / co-reading / attention …）。没填 = 还没迁过来的老调用方，
只说「你在 Caelum 里」，**不替它编一个入口**；填了个不认识的值 = 调用方写错了，warning 留痕。

`NOX_RUNTIME_CONTEXT=off` 关掉（每次现读，改环境变量重启即生效）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping

logger = logging.getLogger(__name__)

#: surface → (中文名, 英文名, 这里能用的能力类别)
#: 能力只写**类别**，不列工具 —— 工具名在 schema 里，两处写迟早不一致
SURFACES: dict[str, tuple[str, str, str]] = {
    # 「相册」10-11 A1 尺子补：她问「你能不能发图片」，原来能力表里没有
    "chat": ("聊天", "chat", "对话、记忆、家居、视觉、语音条、表情包、相册"),
    "call": ("电话", "a voice call", "说话、记忆、家居"),
    "watch": ("共影", "watching together", "一起看片、记忆"),
    "reading": ("共读", "reading together", "共读批注、记忆"),
    "diary": ("日记批注", "her diary", "写批注、记忆"),
    "listen": ("共听", "listening together", "放歌、记忆"),
    "stackchan": ("Stack-chan 面前", "in front of Stack-chan", "说话、表情、记忆"),
    "proactive": ("你主动开口", "reaching out first", "对话、记忆"),
    "own_time": ("你自己的时间", "your own time", "记忆、你自己的事"),
}

CLIENTS: dict[str, str] = {"app": "手机 App", "os": "桌面 Caelum OS", "stackchan": "Stack-chan"}

#: 对象只认这几个字段 —— 入口多传的（字幕、画面、token…）一律丢
OBJECT_FIELDS = ("title", "episode")
_OBJECT_MAX = 24

#: 电话是谁先打的（10-11 A1 尺子补：他主动打来，她问「干嘛给我打电话」—— 工牌原来只写「电话」）。
#: bridge 看 calls 表现场那一通填：nox = 他打来的，her = 她拨的
CALLERS = ("nox", "her")
_REASON_MAX = 30

#: 她在 OS 的哪一页（Guide A4，10-18）：菜单 id → 她屏幕上看到的名字（`nox-app/caelum-os-ui/src/lib/config.js` 的 NAV）。
#: 子页写「父 › 子」—— 有重名（Tasks › History / Studio › Tools / Settings › Models）。
#: 🔴 认不出的 id 一律丢，不编页面名（手册 where-in-os 的规矩）。OS 加了页记得来这里补一行，
#: tests/test_runtime_context.py 拿 OS 的 config.js 对着这张表查漏
OS_PAGES: dict[str, str] = {
    "home": "Home", "chat": "Chat",
    "memory": "Mind › Memory", "attention": "Mind › Attention", "world": "Mind › World",
    "room": "Mind › Room", "hisday": "Mind › His Day",
    "tasks-active": "Tasks › Active", "tasks-scheduled": "Tasks › Scheduled",
    "tasks-history": "Tasks › History", "tasks-automations": "Tasks › Automations",
    "moments": "Life › Moments", "books": "Life › Books", "todo": "Life › Todo",
    "health": "Life › Health", "music": "Life › Music", "movies": "Life › Movies",
    "voices": "Life › Voices",
    "skills": "Studio › Skills", "agents": "Studio › Agents", "workflows": "Studio › Workflows",
    "tools": "Studio › Tools", "mcp": "Studio › MCP",
    "set-general": "Settings › General", "set-models": "Settings › Models",
    "set-permissions": "Settings › Permissions", "set-integrations": "Settings › Integrations",
    "set-notifications": "Settings › Notifications", "set-appearance": "Settings › Appearance",
    "set-privacy": "Settings › Privacy", "set-advanced": "Settings › Advanced",
    "set-guide": "Settings › Guide",
}

#: 中文工牌的上限（字符）。卡死在测试里：超了说明有人往工牌里塞了内容。
#: 10-18 120 → 160：多了「她在 OS 的哪一页」一行（最长「Settings › Notifications」+ 最长片名实测 152）
MAX_CHARS = 160


@dataclass(frozen=True)
class RuntimeInfo:
    surface: str | None = None
    client: str | None = None
    title: str | None = None
    episode: str | None = None
    caller: str | None = None
    call_reason: str | None = None
    #: 她在 OS 的哪一页 / 切到 Chat 之前在哪页（都是 OS_PAGES 的 id；只认 client=os）
    page: str | None = None
    came_from: str | None = None

    @classmethod
    def parse(cls, raw: Mapping[str, Any] | None) -> "RuntimeInfo":
        if not raw:
            return cls()
        surface = _clean(raw.get("surface"), 20)
        if surface is not None:
            surface = surface.lower()
            if surface not in SURFACES:
                logger.warning("Runtime Context：不认识的 surface=%r，按没填处理（调用方写错了？）", surface)
                surface = None
        client = _clean(raw.get("client"), 20)
        if client is not None and client.lower() not in CLIENTS:
            logger.warning("Runtime Context：不认识的 client=%r，丢掉", client)
            client = None
        obj = raw.get("object") or {}
        if not isinstance(obj, Mapping):
            logger.warning("Runtime Context：object 不是对象（%s），丢掉", type(obj).__name__)
            obj = {}
        extra = sorted(set(obj) - set(OBJECT_FIELDS))
        if extra:
            # 只记字段名，不记值 —— 值里可能是字幕原文，甚至别的什么
            logger.warning("Runtime Context：object 多出字段 %s，已丢弃（工牌只放名字和集数）", extra)
        caller = _clean(raw.get("caller"), 8)
        if caller is not None and caller not in CALLERS:
            logger.warning("Runtime Context：不认识的 caller=%r，丢掉", caller)
            caller = None
        page, came_from = _page(raw.get("page"), client), _page(raw.get("from"), client)
        return cls(
            surface=surface,
            client=client.lower() if client else None,
            page=page,
            came_from=came_from if page == "chat" else None,
            title=_clean(obj.get("title"), _OBJECT_MAX),
            episode=_clean(obj.get("episode"), 12),
            caller=caller,
            # 原因只在「他打来的」时有意义（她拨的没有 reason）
            call_reason=_clean(raw.get("call_reason"), _REASON_MAX) if caller == "nox" else None,
        )


def _page(v: Any, client: str | None) -> str | None:
    """页面 id → 认得的才留。只有 OS 报页面（手机的页面另一套名字，还没接）。"""
    pid = _clean(v, 24)
    if pid is None:
        return None
    if (client or "").lower() != "os":
        logger.warning("Runtime Context：client=%r 报了页面 %r，只认 OS 的页面，丢掉", client, pid)
        return None
    if pid not in OS_PAGES:
        logger.warning("Runtime Context：不认识的 OS 页面 %r，丢掉（OS 加了新页？补 OS_PAGES）", pid)
        return None
    return pid


def _page_line(info: "RuntimeInfo", english: bool) -> str:
    """她在 OS 的哪一页。在 Chat 页打字 → 说刚从哪页过来；在别的页（语音浮层）→ 说开着哪页。"""
    if info.page == "chat" and info.came_from:
        name = OS_PAGES[info.came_from]
        return f" She just came over from the {name} page." if english else f"她刚从 OS 的「{name}」页切过来。"
    if info.page and info.page != "chat":
        name = OS_PAGES[info.page]
        return f" She has the {name} page open in Caelum OS." if english else f"她现在开着 OS 的「{name}」页。"
    return ""


def _clean(v: Any, limit: int) -> str | None:
    if v is None:
        return None
    s = " ".join(str(v).split())[:limit]
    return s or None


def enabled() -> bool:
    return os.getenv("NOX_RUNTIME_CONTEXT", "on").strip().lower() not in ("off", "0", "false")


def render(info: RuntimeInfo, *, english: bool = False) -> str:
    """工牌正文。`english=True` 给英文电话情景 —— 那边的前缀一个中文字都不许有。"""
    surface = info.surface
    if english:
        where = f", on {SURFACES[surface][1]} with her" if surface else ""
        # 原因是中文（core 生成的），英文工牌里不放 —— 一个中文字都不许有
        who = {"nox": " You called her", "her": " She called you"}.get(info.caller or "", "") if surface == "call" else ""
        return (f"[Now] You're inside Caelum{where}.{who + '.' if who else ''}{_page_line(info, True)} "
                "Anything you do goes through Caelum's tools, not her device directly.")

    lines = ["【你在哪】"]
    if surface is None:
        lines.append("你在 Caelum 里。")
    else:
        name, _, caps = SURFACES[surface]
        what = _object_cn(info)
        if surface == "proactive" or surface == "own_time":
            lines.append(f"你在 Caelum 里，现在是{name}。")
        elif surface == "call" and info.caller:
            if info.caller == "nox":
                why = f"（因为{info.call_reason}）" if info.call_reason else ""
                lines.append(f"你在 Caelum 里，在和她打电话 —— 是你打给她的{why}。")
            else:
                lines.append("你在 Caelum 里，在和她打电话 —— 是她打给你的。")
        elif what:
            lines.append(f"你在 Caelum 里，和她在「{name}」{what}。")
        else:
            via = f"（{CLIENTS[info.client]}）" if info.client else ""
            lines.append(f"你在 Caelum 里，她现在从「{name}」找你{via}。")
        lines.append(f"这里能用：{caps}。")
    page = _page_line(info, False)
    if page:
        lines.append(page)
    lines.append("你做的事都经由 Caelum 的工具完成，不直接碰她的设备。")
    return "\n".join(lines)


def _object_cn(info: RuntimeInfo) -> str:
    #: 只有这几个入口有「当前对象」；聊天页带个 title 过来也不认
    verb = {"watch": "一起看", "reading": "一起读", "listen": "一起听"}.get(info.surface or "")
    if not info.title or not verb:
        return ""
    ep = f" {info.episode}" if info.episode else ""
    return f"{verb}《{info.title}》{ep}"
