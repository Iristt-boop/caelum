"""token 估算 —— 压缩决策用，不需要精确，只需要"快接近上限"的粗判断。

为什么不用 tiktoken：
- requirements 极简是现状，tiktoken 会打破（而且 DeepSeek 的中文分词
  和 cl100k_base 本来就有偏差，装了也未必更准）
- 压缩触发只需要粗判断：±20% 不影响"该不该压"的正确性
- 纯函数，可测，不打网络

估算公式（按 DeepSeek 线上实测校准）：
- 中文字符 ≈ 0.6 token/字（中文信息密度高，一字≈一个 token 的 6 成）
- 英文/数字/标点 ≈ 0.3 token/字符（英文平均 3-4 字符一个 token）
- 消息结构开销 ≈ 4 token/条（role 标记、换行、JSON 包装）
- tool_calls / tool_results 按 JSON 文本估（它们不进压缩段，但预算要算）
"""

from __future__ import annotations

import logging
import re

from agent.llm import Message

logger = logging.getLogger(__name__)

# 中文（含全角标点、日韩假名）—— 按字计，0.6 token/字
_CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf\uff00-\uffef]")
# 每个 token 的结构开销（role 标记、换行、JSON 外壳）
_MSG_OVERHEAD = 4


def _text_of(m: Message) -> str:
    """一条消息的全部文本内容（正文 + 工具调用参数/结果）。"""
    parts: list[str] = []
    if m.text:
        parts.append(m.text)
    for tc in m.tool_calls:
        parts.append(tc.name)
        if tc.arguments:
            parts.append(str(tc.arguments))
    for tr in m.tool_results:
        if tr.content:
            parts.append(tr.content)
    return "\n".join(parts)


def estimate_tokens(messages: list[Message]) -> int:
    """估算一组消息的总 token 数。

    启发式：CJK 字符 × 0.6 + 其余字符 × 0.3 + 条数 × 结构开销。
    宁可高估（触发压缩偏早）也不要低估（爆预算）—— 压缩偏早只是多压一次，
    爆预算会截断回答。
    """
    total = 0
    for m in messages:
        text = _text_of(m)
        cjk = len(_CJK.findall(text))
        other = len(text) - cjk
        total += int(cjk * 0.6) + int(other * 0.3) + _MSG_OVERHEAD
    return total


def estimate_text_tokens(text: str) -> int:
    """估算单段文本的 token 数（summary 预算校验用）。"""
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return int(cjk * 0.6) + int(other * 0.3)


def tail_within_budget(
    messages: list[Message], budget: int
) -> list[Message]:
    """从尾部往前取，凑到 ≤ budget 为止。返回最近窗口的原文。

    压缩时用它切"保留原文的最近窗口"：从最后一条往前加，
    一旦估算超预算就停（当前这条也算进去 —— 宁可略超也别丢最后一条）。
    """
    acc: list[Message] = []
    running = 0
    for m in reversed(messages):
        cost = estimate_tokens([m])
        # 单条超预算（极端长消息）：无论如何都要带上（这是最近的话）
        if not acc or running + cost <= budget:
            acc.append(m)
            running += cost
        else:
            break
    acc.reverse()
    return acc
