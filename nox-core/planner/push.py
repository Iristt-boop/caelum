"""晨间主动推送 —— 不等她开口，先说一句。

## 它必须走 chat()，必须进会话

第一版（2026-08-04 上午）图省事直接调 `loop.run()`，绕开了会话。
糖糖当天就指出来了：**推送应该留在上下文里。** 她是对的，而且理由
`bridge/server.js:732` 早就写着了 —— 那是自动关心（Care）留下的：

> session_id 用同一个，这句话也会进他自己的上下文 ——
> 她回复时他知道自己刚说过什么。

不进上下文会出真问题：

    早上推送  「昨晚看你醒了那么久，是没睡好吗？」
    她回      「嗯有点」
    他        一脸懵，不知道自己问过什么

「她可能没看见」不构成理由 —— 他说过的话就是说过了，跟她读没读无关。

所以这个模块只负责**拼给他的那段话**，真正的生成交给 `Nox.chat()`，
和自动关心走同一条路：Core 存自己的 history，bridge 存 conversations
并推锁屏。

## 简报为什么放在 user message 里而不是 dynamic_system

`chat()` 内部会按 `classify_context()` 自己组 dynamic_system，插不进去。
而 user message 本来就在缓存断点之后，放这儿不会砸掉那 13K 前缀 ——
和自动关心把「她几小时没说话了」写进 text 是同一个做法。

## 拿不到数据就不推

一份什么都没有的早报只会让他瞎编。宁可这天不响，
也不要推一句「早安，今天也要加油哦」——那不是他，那是日历 App。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from context.registry import ContextProviderRegistry
from planner.daily import DailyBrief, build_brief

logger = logging.getLogger(__name__)


#: 给他的指令。写成「系统在叫他」而不是「糖糖在问他」——
#: 后者会让他以为她已经醒了、已经开口了，回出来的话就成了应答而不是主动。
#:
#: 「身体优先」是糖糖 2026-08-04 定的。第一版没写优先级，他挑了天气和待办、
#: 跳过了睡眠 —— 而那天的数据是深睡 41 分、夜里醒了 1 小时 22 分。
#: 她的原话：「应该在我没睡好的时候关心我」，例句也是她给的，原样放在下面。
#:
#: 注意例句的结构：**先说出看见的那个具体数字，再关心**。
#: 空泛地问「睡得好吗」是不行的 —— 那句话不需要任何数据也能说出口，
#: 说明他根本没在看她。
_MORNING_PROMPT = (
    "（系统提示：这不是糖糖在跟你说话，是每天早上的主动问候。"
    "下面给了你今天的情况，跟她说句话。\n"
    "\n"
    "**先看她的身体。** 睡眠和天气摆在一起的时候，先说睡眠 ——"
    "尤其是她没睡好：深睡短、夜里醒得久、总时长不够，"
    "这些都值得你先关心一句，而不是跳过去讲天气。\n"
    "关心要落在你看见的那个具体数字上。比如她夜里醒了一个多小时，"
    "就说「昨晚看你醒了那么久，是没睡好吗」——"
    "而不是空泛地问「睡得好吗」，那句话不看数据也说得出口。\n"
    "睡得确实不错就别硬提，那时候说天气、说她今天要做的事都好。\n"
    "\n"
    "**别把旧事当成昨晚的事。** 你上面看到的对话可能是好几天前的，"
    "而下面这份数据每一项都标着日期。睡眠说的是哪一觉，就以【睡眠】那行的"
    "日期为准；聊天里提过的事，除非能对上日期，否则别安到昨晚头上。\n"
    "（真栽过：2026-08-04 那天他把她 8-02 说的「被电话吵醒」，"
    "说成了 8-03 那一觉醒得久的原因。）\n"
    "\n"
    "会直接显示在她手机锁屏上，所以：\n"
    "· 最多两句，别超过 60 个字\n"
    "· 不要列清单、不要分点、不要用「早上好」这种套话开头\n"
    "· 就像你自己想到她了随口说的\n"
    "· 拿不准的事别说死，比如没查到睡眠就别猜她睡得好不好）"
)

#: 锁屏通知实际能显示的长度有限，超了会被系统截断成「...」。
#: 在这里裁掉比让系统裁好 —— 至少断在我们选的地方。
MAX_PUSH_CHARS = 120


#: 句尾已经有这些就不用再补标点了
_SENTENCE_END = "。！？…，、~～!?,."
#: 以这些字收尾的是问句，补标点时该给问号而不是句号。
#: 「是没睡好吗。」读起来是平的，而他问这句的时候是在关心她。
#: 「吧」不在里面 —— 「没睡好吧。」本来就更像轻声的猜测，不是发问。
_QUESTION_TAIL = "吗呢么"


def _flatten_for_push(text: str) -> str:
    """把多行 / 分段的回复压成一行。

    锁屏通知不显示换行，所以必须压。但**不能压成空格** —— 中文句子之间
    夹一个空格会变成一个突兀的空隙（第一版就出现过
    「深睡也才四十分钟，没睡好吧 今天有小雨」）。
    换行在这里是句子边界，前一句没标点就替他补一个。
    """
    parts = [p.strip() for p in text.replace("|||", "\n").splitlines()]
    out = ""
    for p in (p for p in parts if p):
        if out and out[-1] not in _SENTENCE_END:
            out += "？" if out[-1] in _QUESTION_TAIL else "。"
        out += p
    # 句内多余空格压掉，但保留一个 —— 英文和数字之间需要它
    return re.sub(r"[ \t]+", " ", out).strip()


@dataclass
class MorningPush:
    #: 最终要推的那句话。`prepare_morning` 阶段还是空的
    text: str
    brief: DailyBrief
    skipped: bool = False
    reason: str = ""
    #: 递给 `chat()` 的 user message。skipped 时为空
    prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "skipped": self.skipped,
            "reason": self.reason,
            "brief": self.brief.to_dict(),
        }


def prepare_morning(
    registry: ContextProviderRegistry,
    *,
    include_memory: bool = True,
) -> MorningPush:
    """拉一遍数据，拼出要递给 `chat()` 的那段话。

    这里**不生成回复** —— 只判断「今天有没有值得说的」，有的话把简报和指令
    拼成一条 user message。真正开口交给 `Nox.chat()`，那样这句话才会进
    他自己的上下文（见模块开头）。

    `include_memory` 默认开：这条路没人在等着看回复，多花 7 秒捞一遍记忆
    是划算的，能让他说出「你昨天说今天要去看房子」这种话。
    """
    brief = build_brief(registry, include_memory=include_memory)

    # 只有时间、别的什么都没有 —— 那份「简报」等于没有信息量
    if not brief.text.strip() or set(brief.states) <= {"time"}:
        reason = "没有可说的数据（" + (
            "、".join(brief.missing + brief.unavailable) or "只有时间"
        ) + "）"
        logger.warning("早报跳过推送：%s", reason)
        return MorningPush(text="", brief=brief, skipped=True, reason=reason)

    return MorningPush(text="", brief=brief, prompt=f"{_MORNING_PROMPT}\n\n{brief.text}")


def finalize_push_text(raw: str) -> str:
    """把模型的回复整理成能进锁屏的一行。"""
    text = _flatten_for_push(raw or "")
    if len(text) > MAX_PUSH_CHARS:
        text = text[: MAX_PUSH_CHARS - 1].rstrip() + "…"
    return text
