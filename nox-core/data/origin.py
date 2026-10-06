"""这一轮的「用户消息」里，哪些是**她说的话**。

## 🔴 理解层只吃她的原话（糖糖 2026-09-28）

> Temporal 只吃 user-originated experience。不要吃 Nox 自己的话、
> 工具返回、视频字幕、外部内容。否则以后 Nox 看电影：「明年世界毁灭」
> → Todo 生成：「明年准备末日物资」。

`role='user'` 不等于「她说的」。落进她会话的 user 消息里还有：

    （系统提示：……）      他主动开口的开场白（speaker / waker / Care）
    【共影】她暂停在…问你   bridge 把场景描述 + 字幕拼进去的提示词
    【共影·主动】…你想说一句  **她根本没开口**，是他看片时想插一句
    diary- / reading- 会话  日记批注 / 共读回批注，整段是程序拼的

2026-09-28 查 shadow 日志抓到的：`is_injected` 只认会话前缀，而共影是
**塞在主会话里**的 —— 于是 09-22 那晚她看《摩登家庭》，Temporal 把
「曼尼加油啊」当她的话去抽时间，规则版情绪、醋意、记忆抽取、
意义推断也全都读了一遍。

## 为什么收成一处

同一个判断以前散在三处（`appraisal_llm.NOT_HER_WORDS` 按会话前缀、
`data/store.py` 的 SQL 按文本前缀、`day/aggregator.py` 又一份），
每处只认自己当时见过的那几种 —— 新加一种注入就漏一处。
`api/server._turn_ends` 在最开头调一次，下面每个读文字的消费方
拿到的都是同一个 `words`。
"""

from __future__ import annotations

#: 整段都是程序拼的会话（不是她在聊天窗口里打的字）
#: own-time-（10-06）：他自己的时间（V5）—— 那一轮的 text 是我们拼的提示词，不是她说的
PROGRAM_SESSION_PREFIXES: tuple[str, ...] = ("diary-", "reading-", "own-time-")

#: 程序拼的提示词的开头。
#:
#: ⚠️ 这是**我们自己写的**提示词的标记，不是在猜她会怎么说话 ——
#: 所以前缀匹配是可靠的。新加一种塞进主会话的提示词，**必须**在这里登记，
#: 否则它会被当成她的原话进理解层（共影就是这么漏的）。
PROGRAM_TEXT_PREFIXES: tuple[str, ...] = (
    "（系统提示", "(系统提示", "【系统提示", "[系统", "系统提示",
    "【共影",   # 【共影】/【共影·主动】（bridge 的共影链路）
)


def is_program_session(session_id: str | None) -> bool:
    """整个会话都是程序拼的（日记批注 / 共读回批注）。"""
    return str(session_id or "").startswith(PROGRAM_SESSION_PREFIXES)


def her_words(session_id: str | None, text: str | None) -> str:
    """这一轮里她真正说的话；**不是她说的就返回空串**。

    返回空串而不是 None：下游本来就用 `if not text` 判「她这轮没说字」
    （纯图片轮），程序拼的提示词和那种轮次对理解层来说是一回事 ——
    没有她的原话可读。
    """
    if is_program_session(session_id):
        return ""
    t = (text or "").strip()
    if t.startswith(PROGRAM_TEXT_PREFIXES):
        return ""
    return t
