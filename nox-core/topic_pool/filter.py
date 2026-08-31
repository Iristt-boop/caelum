"""Filter —— utility 模型做第一遍筛选（文档 §3.2）。

Scout 一轮抓回 30+ 条候选，只有 0~3 条值得进池子。这个便宜模型就是
那道闸：主 Agent 永远只见筛过的，不见整片信息流。

## 禁止编造，靠构造不靠提示词

文档规则 8：「禁止编造来源；必须真实 source_id」。提示词里写了
「照抄」，但**真正的保证在校验**：模型返回的每条 topic 都要能对上
本轮候选里的 source_id，对不上的直接扔；title / url 一律**拿我们自己
抓的那份**回填，模型说什么都不采信。编出来的料在这层就死了。

## 失败返回空

模型挂了 / 返回解析不了 → 空数组，不是异常。候选缓存还在（§3.3），
下一轮重筛就行。没有好内容返回空数组也是**正常产出**（规则 6），
调用方别把空当故障。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from agent.llm import Message
from topic_pool.store import Candidate

logger = logging.getLogger(__name__)

#: 一轮最多带回来的条数（文档 §3.2 规则 7）
MAX_TOPICS = 3
#: 一次最多喂给模型的候选数。再多 prompt 就失控了，先按分数/新旧裁
MAX_CANDIDATES = 40

ALLOWED_CATEGORIES = {
    "ai", "opensource", "science", "art", "design",
    "film", "books", "music", "weird",
}

_SYSTEM = """你在给一个两人小家的「话题池」做第一道筛选。下面是 Scout 从外部抓来的候选材料。

规则：
1. Topic 是材料，不是任务。给出一个能继续追的切口，不替人把结论写完。
2. hook = 材料事实 + 一个值得继续看的点，一句话，像跟熟悉的人转述，不要标题党。
3. 不要问号选题、震惊体、营销标题；没有好内容就返回空数组 []，这是正常结果。
4. 相似内容去重，同一件事只留最好的一条。
5. 每轮最多 3 条。
6. category 只能选：ai / opensource / science / art / design / film / books / music / weird。
7. 禁止编造：source_id 必须原样照抄候选里的 id，一条对不上都不要。
8. summary 以【因为糖糖…】开头的候选是「世界钩子」——她正在读/在看的东西，
   why_this 第一条写明这一点，同等质量下优先保留。

输出 JSON 数组，每条形如：
{"source_id": "...", "hook": "...", "category": "...", "why_this": ["...", "..."], "relevance": 0.0~1.0}
只输出 JSON，不要别的。"""


def build_messages(candidates: list[Candidate]) -> list[Message]:
    """候选 → prompt。超量的丢掉，摘要截短 —— 便宜模型也要省着喂。"""
    picked = candidates[:MAX_CANDIDATES]
    payload = [
        {
            "id": c.source_id,
            "title": c.title[:150],
            "source": c.source,
            "category": c.category,
            "summary": (c.summary or "")[:220],
            "published": c.published_at.date().isoformat() if c.published_at else None,
        }
        for c in picked
    ]
    return [
        Message(role="system", text=_SYSTEM),
        Message(role="user", text=json.dumps(payload, ensure_ascii=False)),
    ]


def _extract_array(text: str) -> list[Any] | None:
    """模型嘴里的 JSON 数组。剥掉 markdown 栅栏，取最外层的 [...]。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        arr = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return arr if isinstance(arr, list) else None


def run_filter(adapter: Any, candidates: list[Candidate],
               now: datetime | None = None) -> list[dict]:
    """筛一遍。返回**已校验**的原始 topic（source_id 都对得上本轮候选）。

    adapter 为 None（utility 没配）或模型失败 → 返回 []。
    """
    if adapter is None or not candidates:
        return []
    now = now or datetime.now().astimezone()

    try:
        turn = adapter.complete(build_messages(candidates), [],
                                system=None, depth="low", max_tokens=4000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("筛选调用失败：%s", exc)
        return []
    if getattr(turn, "stop_reason", "") in ("error", "refusal") or not turn.text:
        logger.warning("筛选未产出内容：%s", getattr(turn, "stop_reason", "?"))
        return []

    arr = _extract_array(turn.text)
    if arr is None:
        logger.warning("筛选返回解析不了 JSON，这轮当没有好内容")
        return []

    by_id = {c.source_id: c for c in candidates}
    out: list[dict] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        cand = by_id.get(str(item.get("source_id") or ""))
        if cand is None:
            continue  # 编造的料，死在这
        hook = str(item.get("hook") or "").strip()
        if not hook:
            continue
        category = str(item.get("category") or "")
        if category not in ALLOWED_CATEGORIES:
            category = cand.category or "weird"
        why = [str(w).strip() for w in (item.get("why_this") or []) if str(w).strip()][:4]
        try:
            relevance = max(0.0, min(1.0, float(item.get("relevance") or 0.5)))
        except (TypeError, ValueError):
            relevance = 0.5
        out.append({
            "source_id": cand.source_id,
            "hook": hook[:300],
            "category": category,
            "why_this": why,
            "relevance": relevance,
        })
        if len(out) >= MAX_TOPICS:
            break
    return out
