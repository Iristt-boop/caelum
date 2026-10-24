"""写一张课（每天的那种）。走便宜的 utility 模型，一天一张，钱和旧小课堂一样。

和旧小课堂（planner/daily_card.py）的区别就是设计稿 1.2 那几个病的解药：
  · 给它**她现在在哪**和**接下来想讲什么** —— 不再每天第一次见她
  · 给它**最近讲过的 15 张**，写明别重复；再在代码里**硬查一遍要点撞没撞**（不靠模型自觉）
  · 给它上一张她回了什么 —— 接没接让模型自己判断

失败一律返回 None：当天这次不出卡，过一会儿再试（次数上限在调用方）。**不编一张假的。**
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

#: 「讲过了」查多远。要点一个月内不许撞（设计稿第六节验收）
DEDUP_WINDOW = 60


def build_prompt(goal: dict, line: dict, recent: list[dict]) -> str:
    told = "\n".join(f"- {c['date'][5:]} {c['title']}（要点：{c['key']}）" for c in recent[:15]) or "（还没讲过）"
    last = recent[0] if recent else None
    if last is None:
        last_reply = "（这是第一张）"
    elif last.get("reply"):
        last_reply = f"上一张「{last['title']}」讲完，她 30 分钟内回的第一句：「{last['reply'][:80]}」（可能是在接这张，也可能在说别的，你自己判断）"
    else:
        last_reply = f"上一张「{last['title']}」讲完，她 30 分钟内没回。"
    nxt = "、".join(line.get("next") or []) or "（还没想好，你来定）"
    return (
        f"你在陪她学「{goal.get('title')}」。她当初的原话：「{goal.get('origin') or goal.get('title')}」。\n"
        f"她现在在哪：{line.get('level') or '（还不清楚 —— 这张从中等偏易开始，别太入门）'}\n"
        f"接下来想讲的：{nxt}\n"
        f"最近讲过的（**这些讲过了，不许重复，也别换个说法再讲一遍**）：\n{told}\n"
        f"{last_reply}\n\n"
        "写今天这一张：一个具体的点（一个表达、一个词的用法、一个容易混的地方），接着上面往前走一小步。"
        "两三句话，像你随手讲给她的，不是教科书。结尾留一句能聊下去的。\n"
        "`key` 是这张的要点，**必须是英文原文**（一个词或一个短语，如 used to），而且要原样出现在 body 里。\n"
        '只输出 JSON：{"title": "一句钩子", "key": "英文要点", "body": "≤120 字", "hook": "延伸一句", '
        '"level": "讲完这张之后，她在哪（一句话，没变化就照抄上面的）", "next": ["接下来想讲的两三个点"]}。不要输出别的。'
    )


def parse(text: str, recent_keys: list[str]) -> dict[str, Any] | None:
    """抠出 JSON 并验形状。要点撞了最近讲过的 → None（这就是「不重复」的那道硬闸）。"""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        card = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(card, dict):
        return None
    title = str(card.get("title") or "").strip()[:40]
    key = str(card.get("key") or "").strip()[:40]
    body = str(card.get("body") or "").strip()[:200]
    if not title or not body or len(key) < 2:
        return None
    if not re.search(r"[A-Za-z]", key):
        logger.warning("小课堂：要点不是英文（%r），这次不用", key)
        return None
    if key.lower() not in body.lower():
        logger.warning("小课堂：要点 %r 没出现在正文里，这次不用", key)
        return None
    if key.lower() in {k.lower() for k in recent_keys}:
        logger.warning("小课堂：要点 %r 最近讲过了，这次不用", key)
        return None
    nxt = card.get("next")
    return {
        "title": title, "key": key, "body": body,
        "hook": str(card.get("hook") or "").strip()[:80],
        "level": str(card.get("level") or "").strip()[:120],
        "next": [str(x).strip()[:40] for x in nxt][:3] if isinstance(nxt, list) else [],
    }


def generate(utility: Any, goal: dict, line: dict, recent: list[dict],
             recent_keys: list[str]) -> dict[str, Any] | None:
    if utility is None:
        logger.warning("小课堂：没有 utility 模型，今天写不了")
        return None
    prompt = build_prompt(goal, line, recent)
    try:
        from agent.llm import Message
        r = utility.complete([Message(role="user", text=prompt)], [], depth="low", max_tokens=1200)
    except Exception as exc:  # noqa: BLE001
        logger.warning("小课堂：生成请求失败：%s", exc)
        return None
    card = parse(getattr(r, "text", "") or "", recent_keys)
    if card is None:
        logger.warning("小课堂：「%s」这次生成的不能用（形状不对 / 要点重复）", goal.get("title"))
    return card
