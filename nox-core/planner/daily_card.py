"""知识小课堂 —— 每天一张卡，融入日常（2026-09-07 糖糖提的）。

每天早上 6 点后生成一张小卡：跟着星期几换主题（哲学 / 语言 / 编程 / 自由联动），
信号取自她的待办和正在读的书。**生成走 utility flash，不走主模型** ——
一天 0.005 元的活，不值得动大脑。

## 卡的去向

1. **Care 源**（`attention/sources/daily_card.py`）：下午的交付窗口里，
   他挑一个时刻把卡当作一条消息讲给她——独立通道，**不占每日 3 条关心额度**
   （糖糖 09-06 问「会不会抵消掉其他的主动开口」——不会，走的是自己的通道）。
2. **聊天上下文**（`context/providers/daily_card.py`）：她的对话里随时提
   「今天那张卡」，他能接住。

## 失败处理

utility 挂 / JSON 解析不了 → 当天不出卡，WARN 一行，明天再来。
**不退回主模型、不编一张假卡** —— 数据不全宁可不发（planner 的老规矩）。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: source_state 里的键。一天一条，date 字段做幂等。
STATE_KEY = "daily_card"

#: 星期几 → 主题。0=周一 … 6=周日。周三/六 coding 是糖糖 09-06 定的。
WEEKDAY_THEMES: dict[int, tuple[str, str]] = {
    0: ("philosophy", "哲学"),
    3: ("philosophy", "哲学"),
    1: ("language", "语言"),
    4: ("language", "语言"),
    2: ("coding", "编程"),
    5: ("coding", "编程"),
    6: ("free", "自由联动"),
}

#: 学习信号的关键词。待办文本命中任一就算「学习相关」（全是她自己的原话）。
_STUDY_HINTS = ("哲学", "西语", "英语", "单词", "书", "课", "学", "尼采")

CST = timezone(timedelta(hours=8))


def _cst(now: datetime) -> datetime:
    return now.astimezone(CST)


def weekday_theme(weekday: int) -> tuple[str, str]:
    """星期几 → (主题 key, 中文主题名)。weekday：0=周一 … 6=周日。"""
    return WEEKDAY_THEMES.get(weekday % 7, ("free", "自由联动"))


# ------------------------------------------------------------ 信号收集


def collect_signals(bridge: Any, world: Any) -> dict[str, Any]:
    """把「她在学什么」的信号现收一轮。全路径失败都容忍——卡照出，只是少了个性化。"""
    out: dict[str, Any] = {"todos": [], "reading": ""}

    # 待办里的学习相关项（关键词过滤，全是她自己的原话）
    if bridge is not None:
        try:
            r = bridge.get("/api/todo/list")
            if r.ok:
                sections = ((r.data or {}).get("sections") or {})
                texts = [t for texts in sections.values() for t in texts]
                out["todos"] = [t for t in texts if any(h in t for h in _STUDY_HINTS)][:6]
        except Exception as exc:  # noqa: BLE001
            logger.warning("学习信号：todos 取不到（%s）", exc)

    # 正在读的书（World Model；fresh 才算数）
    try:
        if world is not None:
            st = world.get_state("reading_progress")
            if st.status == "fresh":
                title = str((st.value or {}).get("title") or "").strip()
                if title:
                    out["reading"] = title
    except Exception as exc:  # noqa: BLE001
        logger.warning("学习信号：reading_progress 取不到（%s）", exc)

    return out


# ------------------------------------------------------------ 生成


def build_prompt(theme_label: str, signals: dict[str, Any]) -> str:
    todo_lines = "\n".join(f"- {t}" for t in signals.get("todos", [])) or "（没有）"
    reading = signals.get("reading") or "（没有）"
    return (
        f"今天的主题是「{theme_label}」。\n"
        f"她最近的学习线索（她的原话）：\n{todo_lines}\n"
        f"她正在读的书：{reading}\n\n"
        "写今天的知识小课堂：围绕主题，优先接住她正在学的东西，"
        "给她一个具体的知识点——一个概念、一句原文、一个表达都可以。"
        "两三句话，像你随手分享给她的，不是教科书。"
        "结尾留一句能跟她聊下去的延伸。\n"
        '只输出 JSON：{"subject": "主题标签", "title": "一句钩子", '
        '"body": "知识本体，≤120 字", "hook": "延伸"}。不要输出别的。'
    )


def parse_card(text: str, fallback_subject: str) -> dict[str, Any] | None:
    """从模型输出里抠出卡片 JSON。抠不出来返回 None——宁可不发不编。"""
    if not text:
        return None
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.S)
    if fenced:
        cleaned = fenced.group(1)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        card = json.loads(cleaned[start:end + 1])
    except ValueError:
        return None
    if not isinstance(card, dict):
        return None
    subject = str(card.get("subject") or fallback_subject).strip()[:12]
    title = str(card.get("title") or "").strip()[:40]
    body = str(card.get("body") or "").strip()[:160]
    hook = str(card.get("hook") or "").strip()[:60]
    if not title or not body:
        return None
    return {"subject": subject, "title": title, "body": body, "hook": hook}


def generate_daily_card(store: Any, utility: Any, bridge: Any, world: Any,
                        now: datetime | None = None) -> dict[str, Any] | None:
    """生成今天的卡并落 source_state。同天已生成 → 直接返回已存的那张。

    任何一步失败返回 None——当天不出卡，明天再来。**不编、不退主模型。**
    """
    now = now or datetime.now(timezone.utc)
    local = _cst(now)
    today = local.date().isoformat()

    existing = get_today_card(store, now)
    if existing:
        return existing

    if utility is None:
        logger.warning("知识小课堂：没有 utility adapter，跳过生成")
        return None

    signals = collect_signals(bridge, world)
    theme_key, theme_label = weekday_theme(local.weekday())
    prompt = build_prompt(theme_label, signals)

    try:
        # ⚠️ adapter 吃的是 Message 对象（text 字段）不是 dict/content ——
        # 这已经是第二次试错：裸 dict 炸 role、content 参数炸字段名
        from agent.llm import Message
        r = utility.complete([Message(role="user", text=prompt)],
                             [], depth="low", max_tokens=800)
    except Exception as exc:  # noqa: BLE001
        logger.warning("知识小课堂生成请求失败：%s", exc)
        return None

    card = parse_card(getattr(r, "text", "") or "", theme_label)
    if card is None:
        logger.warning("知识小课堂：生成结果解析失败，今天不出卡")
        return None

    card["date"] = today
    card["subject_key"] = theme_key
    store.set_source_state(STATE_KEY, card)
    logger.info("知识小课堂已生成：%s｜%s", card["subject"], card["title"])
    return card


# ------------------------------------------------------------ 读取


def get_today_card(store: Any, now: datetime | None = None) -> dict[str, Any] | None:
    now = now or datetime.now(timezone.utc)
    local = _cst(now)
    today = local.date().isoformat()
    try:
        raw = store.get_source_state(STATE_KEY)
    except Exception:  # noqa: BLE001
        return None
    if not raw or raw.get("date") != today:
        return None
    return raw
