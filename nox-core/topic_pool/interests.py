"""话题池跟着她的兴趣走（2026-10-23，《Caelum-记忆目标主动-设计稿》第五节，她 10-20 定：不做推荐页）。

原来池子搜什么是写死的九个方向（AI / 电影 / 书 / 热榜……），抓回来的多半跟她没关系。
现在**搜什么由她的档案（喜欢 / 在做的事）和她在跟的目标决定**：

    档案 + 目标 ──(utility，一天一次)──▶ 6 个搜索方向 {query, category, why}
    每轮抽 2 个去搜 ──▶ 候选带【因为糖糖…】前缀（同世界钩子：Filter 见了优先留、写进 why_this）

固定方向不全砍，每轮还抽 1 个 —— 免得池子里只剩她已经知道的东西（设计稿第五节第三条）。
捞进来的不给她看列表，由他挑：好奇那股心思（CuriositySource）读池子 → 进开口压力 / 发朋友圈，这两条路原来就在。

一天只问一次模型：方向存在 data 目录的 interests.json，跨天才重算；档案读不到就沿用上次的。
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agent.llm import Message
from topic_pool.filter import ALLOWED_CATEGORIES

logger = logging.getLogger(__name__)

MAX_QUERIES = 6
PER_CYCLE = 2

_PROMPT = """下面是糖糖的档案（现在的她）和她最近在跟的目标。
挑出 {n} 个**值得替她留意外面新消息**的方向 —— 她喜欢的、在做的、在跟的目标相关的。
每个方向给一个中文新闻搜索词（2~8 字，像在新闻站里会搜的词，不要整句），选一个分类，写一句为什么（「因为糖糖……」开头）。
不要她的隐私（身体状况、感情）去搜；搜的是外面的事：她爱的游戏出新东西、她在读的书的作者、她感兴趣的领域有什么新进展。

分类只能选：{cats}

【档案】
{profile}

【她在跟的目标】
{goals}

只输出 JSON 数组：[{{"query": "底特律 变人", "category": "weird", "why": "因为糖糖最爱《底特律：变人》"}}]"""


def _parse(text: str) -> list[dict]:
    s, e = (text or "").find("["), (text or "").rfind("]")
    if s < 0 or e <= s:
        return []
    try:
        arr = json.loads(text[s:e + 1])
    except ValueError:
        return []
    out = []
    for x in arr if isinstance(arr, list) else []:
        if not isinstance(x, dict):
            continue
        q = str(x.get("query") or "").strip()
        why = str(x.get("why") or "").strip()
        cat = str(x.get("category") or "")
        if not 2 <= len(q) <= 16 or not why:
            continue
        out.append({"query": q, "category": cat if cat in ALLOWED_CATEGORIES else "weird",
                    "why": why if why.startswith("因为糖糖") else f"因为糖糖{why}"})
    return out[:MAX_QUERIES]


def derive(adapter: Any, profile: str, goals: list[str]) -> list[dict]:
    """问一次 utility。失败 / 解析不了 → []（调用方沿用上次的）。"""
    if adapter is None or not profile.strip():
        return []
    prompt = _PROMPT.format(n=MAX_QUERIES, cats=" / ".join(sorted(ALLOWED_CATEGORIES)),
                            profile=profile.strip()[:3000], goals="、".join(goals) or "（没有）")
    try:
        turn = adapter.complete([Message(role="user", text=prompt)], [], system=None,
                                depth="low", max_tokens=4000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("兴趣方向没算出来（沿用上次的）：%s", exc)
        return []
    return _parse(getattr(turn, "text", "") or "")


class Interests:
    """一天算一次的搜索方向。`source` 给 (档案文本, 目标标题列表)，读不到抛也行 —— 这里接住。"""

    def __init__(self, path: str | Path, adapter: Any,
                 source: Callable[[], tuple[str, list[str]]]) -> None:
        self.path, self.adapter, self.source = Path(path), adapter, source

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}

    def current(self, now: datetime) -> list[dict]:
        day = now.astimezone().date().isoformat()
        saved = self._load()
        if saved.get("day") == day and saved.get("items"):
            return saved["items"]
        try:
            profile, goals = self.source()
        except Exception as exc:  # noqa: BLE001
            logger.warning("读不到档案 / 目标，兴趣方向沿用上次的：%s", exc)
            return saved.get("items") or []
        items = derive(self.adapter, profile, goals)
        if not items:
            return saved.get("items") or []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"day": day, "items": items}, ensure_ascii=False), encoding="utf-8")
        logger.info("今天替她留意的方向：%s", "、".join(i["query"] for i in items))
        return items

    def pick(self, now: datetime, k: int = PER_CYCLE) -> list[dict]:
        items = self.current(now)
        return random.sample(items, min(k, len(items)))
