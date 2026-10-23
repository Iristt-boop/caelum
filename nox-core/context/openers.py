"""历史里他主动开口的开场白 → 发给模型时换成一行（减重第三步，2026-10-19 她点头）。

设计稿 `Caelum-减重三件套-设计稿-2026-10-15.md` 第三节。10-16 起会话库里：开场白样板 4.35 万字（84%）、
他说的 7.6 千字、她说的 808 字。每次开口那段系统提示（情绪分寸、别重复问、锁屏 60 字、「今天你已经说过这些」……）
只对**那一次**开口有用，却以 user 角色一直留在历史里，每轮跟着重发。

## 换成什么（10-19 抽 20 条真开场白核过）

开场白里三类东西：
  · **这次因为什么找她**（惦记晚饭 / 小课堂那张讲什么 / 话题池哪条 / 她睡着了在自言自语 / 她刚到家）
    —— 留。她接着问「你说的那个是啥」，他得知道当时说的是哪件事
  · 「今天你已经主动跟她说过这些」—— 不留：那些话就是他的回复，本来就在历史里
  · 怎么说的规矩 —— 不留：只对那一次有效

换成：`（我主动找她 00:44：她睡着了，自言自语；想起：一起听过《이 별로부터》）`

## 🔴 只在发给模型的那一刻换，库 / 缓存 / 写回一个字都不动

`Store.sync` 写回时拿**内容**做锚点，找不到锚点会全量追加 —— 换过的历史要是流回 sync，
就是 10-10「历史被成批重写」那个事故的形状。所以 `model_view()` 只在 `AgentLoop` 调模型前生成一份视图，
`messages`（返回给 Sessions、落库的那份）原样不动。**这一轮自己的开场白不换**（这一次开口还得看着它）——
loop 只换进来之前的 `history`，这一轮追加的照原样发。

`NOX_OPENER_LINE=off` 退回原样（每次现读）。
"""

from __future__ import annotations

import os
import re

from agent.llm import Message

#: 开场白的开头。只认「系统提示」这一种（他主动开口）—— 【共影】里有时有她真说的话，不动
OPENER_PREFIXES = ("（系统提示", "(系统提示", "【系统提示")

#: 日期分隔线（`context/timeline.py` 加在那一天第一条消息前面）：先剥开，换完再拼回去
_STAMP_RE = re.compile(r"^(（\d{1,2}月\d{1,2}日[^）\n]*）\n)")

_LABEL_MAX = 60


def enabled() -> bool:
    return os.getenv("NOX_OPENER_LINE", "on").strip().lower() not in ("off", "0", "false")


def _split_stamp(text: str) -> tuple[str, str]:
    m = _STAMP_RE.match(text)
    return (m.group(1), text[m.end():]) if m else ("", text)


def is_opener(text: str | None) -> bool:
    return _split_stamp(text or "")[1].lstrip().startswith(OPENER_PREFIXES)


def _cut(s: str) -> str:
    s = " ".join(s.split())
    return s if len(s) <= _LABEL_MAX else s[:_LABEL_MAX].rstrip() + "…"


def _first(pattern: str, body: str) -> str | None:
    m = re.search(pattern, body)
    return m.group(1).strip() if m else None


def opener_label(body: str) -> str:
    """开场白 → 这次因为什么找她（一句）。认不出就空串（那时只说「我主动找她」，不编）。"""
    if "知识小课堂" in body:
        kind, title = _first(r"今天这张（(.+?)）", body), _first(r"今天这张（.+?）：\s*\n(.+)", body)
        return _cut(f"小课堂·{kind or ''}：{title}" if title else "小课堂")
    if "话题池" in body:
        item = _first(r"话题池里有一条一直没人聊过的东西：\s*\n(.+)", body)
        return _cut(f"话题池：{item}" if item else "话题池")
    if "每天早上的主动问候" in body:
        return "早报"
    if "你刚才主动给她打了一个电话，她没有接" in body:
        return "刚给她打电话她没接"
    subject = _first(r"你现在惦记的是：(.+)", body)
    if subject:
        return _cut(f"惦记：{subject}")
    parts = []
    if "她睡着了" in body:
        parts.append("她睡着了，自言自语")
    elif "你刚发现她到家了" in body:
        parts.append("她刚到家")
    elif "你刚发现她出门了" in body:
        parts.append("她刚出门")
    elif "她还在外面" in body:
        parts.append("她在外面")
    old = _first(r"你忽然想起一件开心的旧事[^：]*：(.+?)。", body)
    if old:
        parts.append(f"想起：{old}")
    mood = _first(r"这一句带着的心情：([^（\n]+)", body)
    if mood and not old:
        parts.append(f"心情：{mood}")
    return _cut("；".join(parts))


def opener_line(text: str) -> str:
    """一整条开场白（可能带日期分隔线）→ 一行。"""
    stamp, body = _split_stamp(text)
    clock = _first(r"现在是\s*(?:她那边的\s*)?(?:\d+月\d+日\s*\S*\s*\S*?)?(\d{1,2}:\d{2})", body)
    label = opener_label(body)
    head = f"我主动找她 {clock}" if clock else "我主动找她"
    return f"{stamp}（{head}：{label}）" if label else f"{stamp}（{head}）"


def model_view(history: list[Message]) -> list[Message]:
    """发给模型的那份历史：开场白换成一行；他以前写坏的那种（工具调用写成了标签）剥掉标签；其余原样（同一个对象，不复制）。"""
    if not history:
        return history
    openers = enabled()
    out = []
    changed = False
    for m in history:
        if openers and m.role == "user" and m.text and is_opener(m.text):
            out.append(Message(role="user", text=opener_line(m.text), images=m.images))
            changed = True
        elif m.role == "assistant" and m.text and _has_markup(m.text):
            out.append(_unleak(m))
            changed = True
        else:
            out.append(m)
    return out if changed else history


def _has_markup(text: str) -> bool:
    from agent.contracts import is_markup_line
    return any(is_markup_line(ln) for ln in text.split("\n"))


def _unleak(m: Message) -> Message:
    """10-23：他在正文里写了一串工具调用标签（库里那条留着 —— 是她看到过的记录）。
    给模型看的那份剥掉标签，不然他下一轮照着写（10-10 伪输出放大的同一条路）；剥空了留一句交代，不留空消息"""
    from agent.contracts import strip_markup
    text, _ = strip_markup(m.text)
    return Message(role="assistant", text=text or "（这一条我把工具调用写成了一串标签，她只看到标签，什么也没做成）",
                   tool_calls=m.tool_calls)
