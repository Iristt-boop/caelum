"""这一轮她有没有说出一件「我们之间的事」—— 关系状态的识别（2026-10-06）。

和意义推断（appraisal_llm）同一个后台时机、同一个 utility 模型，单独一问：
**这一轮里，她有没有说出一件改变你们之间相处方式的事？**

认出来的只是**提议**：约定 / 别问 / 上心要她在聊天卡片上点 Keep 才生效；
气氛他自己判断（relation_book.py）。所以这里的门槛管的是「别拿一堆错卡片烦她」，
不是「别把错的写进去」—— 后者有她把关。

## 宁可漏

她还会再说。常态是 none；一次性的请求（「今天别提醒我」）不算约定，
关于她自己的事实（「我不吃香菜」）归记忆库，不归这里。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from attention.relation_book import KINDS

logger = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.7
#: 和 appraisal_llm._TOPICS 同一张词表 —— 评估器的 care_weight / is_avoided 只认这些
TOPICS = frozenset({"饮食", "睡眠", "工作", "情绪", "身体", "关系", "学习"})

_PROMPT = """你在帮 Nox 留意他和女朋友糖糖之间的相处。你**不回复**她，只判断一件事：

这一轮里，她有没有说出一件**改变你们之间相处方式**的事？只有四种算：

- avoid（别问）：她明确不想被反复问 / 催某件事。例：「别老问我吃没吃饭」「能不能别一直催我睡觉」
- care（上心）：她明确要他以后替她盯着某件事。例：「你帮我盯着点，十二点前必须睡」「以后我说要喝奶茶你拦着我」
- pact（约定）：你们说好的、以后一直这么做的事。例：「以后吵架不许过夜」「每天晚安都要说爱你」
- vibe（气氛）：你们之间此刻的气氛**明显变了**。例：真的闹别扭了 / 刚和好 / 她特别甜。平常聊天不算

**不算的**（都回 none）：
- 一次性的请求：「今天别提醒我了」「这会儿别吵我」
- 关于她自己的事实或喜好：「我不吃香菜」「我喜欢夏天」（那是记忆，不是相处方式）
- 玩笑、撒娇、气话里的「再也不理你了」（除非气氛真的变了，那是 vibe）
- 他说的话（只看她说的）

只输出一个 JSON，不要解释：
{{"kind": "avoid|care|pact|vibe|none", "text": "用 Nox 的口吻写一句，「她」指糖糖，例：她不喜欢我老问她吃没吃饭",
  "topic": "饮食|睡眠|工作|情绪|身体|关系|学习|空串", "quote": "她的原话（摘最关键那句）", "confidence": 0~1}}

已经记着的（别重复提）：{known}"""

_TURN = "她说：{her}\n他回：{his}"


def _clean_json(raw: str) -> str:
    m = re.search(r"\{.*\}", raw or "", re.S)
    return m.group(0) if m else "{}"


def detect(adapter: Any, her: str, his: str, known: list[str] | None = None) -> dict[str, str] | None:
    """认出一条就回 {kind, text, topic, quote}，没有 / 不够确定 / 调挂了都回 None。"""
    from agent.llm import Message

    her = (her or "").strip()
    if not her:
        return None
    try:
        turn = adapter.complete(
            [Message(role="user", text=_TURN.format(her=her[:800], his=(his or "（他还没说话）")[:400]))],
            tools=[],
            system=_PROMPT.format(known="；".join((known or [])[:12]) or "（还没有）"),
            depth="low",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("关系识别调用失败：%s: %s", type(exc).__name__, exc)
        return None
    if turn.stop_reason in ("error", "refusal") or not turn.text:
        logger.warning("关系识别没拿到结果：stop_reason=%s", turn.stop_reason)
        return None
    try:
        d = json.loads(_clean_json(turn.text))
    except ValueError:
        logger.warning("关系识别回的不是 JSON：%.80s", turn.text)
        return None
    kind = str(d.get("kind") or "none").strip().lower()
    try:
        conf = float(d.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    text = str(d.get("text") or "").strip()
    if kind not in KINDS or not text:
        return None
    if conf < MIN_CONFIDENCE:
        logger.info("关系识别不够确定（%.2f），不提：%s · %s", conf, kind, text[:40])
        return None
    topic = str(d.get("topic") or "").strip()
    return {"kind": kind, "text": text, "topic": topic if topic in TOPICS else "",
            "quote": str(d.get("quote") or "").strip() or her[:200]}
