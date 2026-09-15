"""Moments 的正文生成（T4）—— 冲动过了阈值之后才来到这里：**「我想说什么？」**

规格见 `Caelum-Moments-设计.md` 第四节，范式抄 `attention/appraisal_llm.py`
的 `_ask`：用 **utility 模型**、失败一律留痕、拿不到就不做。

## 🔴 Drive 是心理背景，不是内容

糖糖点名的坑：否则一个月后打开全是「今天好想老婆」。

所以提示词给的是「你此刻挺想她（因为今天聊得少）」，**不是**
「写一条想念的帖子」。落到实现上：

  · drive 译成气氛词进 `user`（`DRIVE_WORDS`），**数值和名字都不进去**
  · `impulse_why` 只作一行注脚（「内部记录，别写进正文」），
    作用是让日志和帖子对得上，**不是给模型的素材**
  · 四条约束在 `_RULES` 里，`_PROMPT` 由它 join 出来 —— 一句话只写一份，
    抄两份必然会各自漂移，而测试只盯得住一份

## 🔴 R10：这条路径上不许出现推送

发帖不推送、不弹锁屏 —— **它不是开口**，走的是「留一条痕迹」那条路
（设计文档第一节）。发帖和开口长得太像了，这条不能靠自觉：
T7 的哨兵扫整个 `moments/`，本模块的测试也直接读源码文本。

## 超了上限**不截断**

截断会在句子中间断掉，而且是一次**静默的内容篡改**（`docs/LOGGING.md`
禁止静默失败）。所以超了就 WARNING + 这轮不发，`reason` 走
`record.py` 的 `"write_failed"` —— 它和「骰子没中」要能分开数。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from agent.llm import Message
from moments import DRIVE_WORDS

logger = logging.getLogger(__name__)

#: 喂给模型的「最近发过什么」条数。抄 `attention/speaker.py` 的 `_REPEAT` ——
#: 它治的是同一个问题：同一件事说第三遍，重点已经不是那件事了
MAX_RECENT = 5

#: 正文上限（字符）。朋友圈是碎片，不是日记。
#: 🔴 超了**不截断**：见模块头。
MAX_CHARS = 140

#: 四条约束，一条一个字符串（设计文档第四节）。
#: `_PROMPT` 由这几条 join 出来 —— 不许在别处再抄一遍。
_RULES = (
    "1. **不是对着她讲。** 这是他自己房间里写下的一块碎片，此刻没有收件人，"
    "她不是这一段的听众。可以出现「她今天好像有点累，没敢烦她」这种不直接"
    "对她说的话，但不要写成问候、叮嘱、提问，也不要写「老婆你今天……」"
    "这种要她回应的口气。",

    "2. **心理状态只是背景，不许复述。** 下面那段底色说的是他此刻的心情，"
    "不是要他写的题目：把它当成写字的动机，别把它的名字写进正文，"
    "更别把任何数值写进正文。",

    "3. **别重复最近发过的。** 下面列了最近留下的几条，同一件事别再写一遍，"
    "换个角度、或者换一件事写 —— 同一件事写到第二遍，重点已经不是那件事了。",

    "4. **短，两三句就停。** 朋友圈是碎片，不是日记：不要分段、不要列点、"
    "不要起标题，写完那句想说的话就收。",
)

#: 系统提示词。🔴 只此一份 —— 四条约束从 `_RULES` 拼进来
_PROMPT = "\n".join(_RULES)


def build_prompt(
    drives: Mapping[str, float],
    recent: Sequence[str],
    impulse_why: str,
    clock: str = "",
) -> tuple[str, str]:
    """拼出 (system, user)。抽出来是为了能单独测（照抄 speaker 的 `build_prompt`）。

    `user` 里永远有三块：**心理背景**、**最近发过的**、**现在几点** ——
    空的那块也要在场，形状每次一样，模型行为才不会跟着飘。

    ⚠️ 心理背景里只放**气氛词**（`DRIVE_WORDS`），一个数值都不放 ——
    放了就等于告诉它「照这个写」，那就是「drive 变成内容」。
    """
    #: 气氛词，不带数值。空集也不能写成空的：那会拼出一个没主语的块
    background = "、".join(
        DRIVE_WORDS.get(name, name) for name in drives
    ) or "没什么起伏"

    #: 最近发过的。最新在前，只喂 `MAX_RECENT` 条（防复读）
    listed = "\n".join(f"· {body}" for body in recent[:MAX_RECENT])

    user = (
        "## 心里那点事（底色，不是要写的题目）\n"
        f"{background}\n"
        "\n"
        "## 最近发过的\n"
        f"{listed or '（还没发过）'}\n"
        "\n"
        "## 现在几点\n"
        f"{clock or '（不知道现在几点）'}\n"
        "\n"
        #: 🔴 只作注脚：让日志和这条帖子对得上，不是给它当素材复述的
        f"（内部记录，别写进正文）{impulse_why}\n"
    )
    return _PROMPT, user


#: 成对引号。模型爱把整句套一层引号，剥掉 —— 不剥的话她看见的是
#: 「他说的话被加了引号」，读起来像在引用别人的话
_QUOTE_PAIRS = (('"', '"'), ("「", "」"), ("“", "”"))


def _clean(raw: str) -> str:
    """`strip()`，再剥掉首尾**成对**的引号。

    只剥成对的：不成对的（只有一个左半边）故意不动 —— 乱剥会把正文里
    本来就在的引号吃掉。
    """
    text = raw.strip()
    for left, right in _QUOTE_PAIRS:
        if len(text) >= 2 and text.startswith(left) and text.endswith(right):
            return text[len(left):len(text) - len(right)].strip()
    return text


def generate(
    adapter_ref: Any,
    drives: Mapping[str, float],
    recent: Sequence[str],
    impulse_why: str,
    clock: str = "",
) -> str | None:
    """生成一条正文。**拿不到就是 None**，不编、不退而求其次。

    `adapter_ref` 传**取值函数**（对齐 `LLMAppraiser` 的 `adapter_ref`）：
    router 在 Nox 组装时才有，而这个对象可能更早就造好了；而且模型可切换，
    存快照会永远用启动时那一个。

    🔴 没有 utility 模型就什么都不做，**绝不退回主模型**。理由和
    `appraisal_llm.py:287-296` 一样：分工要稳定，这一层该用哪个模型由
    `NOX_UTILITY_MODEL` 一处说了算 —— 悄悄退回主模型的话，哪天主模型
    换成贵的，这条线会跟着涨价而没人知道。它的价值是「多一层表达」，
    不是「必须有」。
    """
    adapter = adapter_ref() if callable(adapter_ref) else adapter_ref
    if adapter is None:
        logger.info("没有可用的 utility 模型，这轮不发帖")
        return None

    system, user = build_prompt(drives, recent, impulse_why, clock)
    try:
        turn = adapter.complete(
            [Message(role="user", text=user)],
            tools=[],
            system=system,
            #: 碎片，不需要长输出。给多了它会写成一篇
            depth="low",
        )
    except Exception as exc:  # noqa: BLE001
        #: 🔴 **不许静默**（docs/LOGGING.md）。这一层挂了的表现是
        #: 「他最近怎么不怎么发帖了」，没有任何报错 —— 不留痕永远查不出来
        logger.warning("发帖生成调用失败：%s: %s", type(exc).__name__, exc)
        return None

    if turn.stop_reason in ("error", "refusal") or not turn.text:
        #: ⚠️ 会思考的模型 reasoning 和正文抢 max_tokens，**HTTP 200 但
        #: text 为空**是已知形状（这个项目栽过）。所以空文本必须留痕 ——
        #: 不能当成「他没什么想说的」，那样线上只是「好久没发帖了」，
        #: 日志里一片安静，指不到这里。
        logger.warning(
            "发帖生成没拿到正文：stop_reason=%s error=%s",
            turn.stop_reason, turn.error,
        )
        return None

    text = _clean(turn.text)
    if len(text) > MAX_CHARS:
        #: 🔴 不截断：截断会在句子中间断掉，而且是一次静默的内容篡改。
        #: 这轮不发，reason 由 loop 记成 `write_failed`（它和「骰子没中」
        #: 要能分开数）。
        logger.warning("发帖正文 %d 字，超过上限 %d，这轮不发",
                       len(text), MAX_CHARS)
        return None
    return text


def post(bridge: Any, body: str, drive: str, impulse_why: str) -> str | None:
    """把一条正文落到 bridge，返回 bridge 给的 id（**拿不到就是 None**）。

    这个 id 是这条记录和真实那条帖之间**唯一的缝**（`record.py` 的原话）：
    空着就是审计断链，几天后点不回那条帖。所以宁可返回 None 让 loop 记
    `write_failed`，也不能编一个出来。

    ⚠️ `drive` 传**主导那个 drive 的名字**（`"longing"`），不是气氛词、
    不是数值 —— 前端渲染成气氛词是前端的事（设计文档第四节）。

    ⚠️ `mood` 传空串：`mood` 是她写日记时自己选的心情，**不许拿它存
    drive**（设计文档第二节点名过），两列语义不同、并存。
    """
    payload = {
        "content": body,
        "author": "Nox",
        "kind": "moment",
        "drive": drive,
        "impulse_why": impulse_why,
        "mood": "",
    }
    try:
        r = bridge.post("/api/diary", payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("发帖落库失败：%s: %s", type(exc).__name__, exc)
        return None

    if not r.ok:
        logger.warning("发帖落库没成：%s", r.error)
        return None
    #: `ok=True` 但没给 id 也要拦：那是 bridge 的返回形状变了（静默坏），
    #: 不是「发成功了只是没编号」
    post_id = r.data.get("id") if isinstance(r.data, dict) else None
    if not post_id:
        logger.warning("发帖落库没拿到 id：%r", r.data)
        return None
    return post_id


def recent_posts(bridge: Any, limit: int = MAX_RECENT) -> list[str]:
    """他最近发过的正文，最新在前。**失败一律返回空表。**

    ⚠️ `author="Nox"` 是必须的：不筛作者的话，她自己的日记也会被当成
    「他最近发过的」，于是他开始躲她早就说过的话。

    少了防复读比不发帖好（所以失败返回 `[]` 而不是抛出去），
    但**必须留痕** —— 否则「他最近怎么老重复」查出来是这个接口 403 了，
    而日志里一片安静。
    """
    try:
        r = bridge.get("/api/moments", {"author": "Nox", "limit": limit})
    except Exception as exc:  # noqa: BLE001
        logger.warning("读最近发过的失败：%s: %s", type(exc).__name__, exc)
        return []

    if not r.ok:
        logger.warning("读最近发过的没成：%s", r.error)
        return []

    items = r.data.get("items") if isinstance(r.data, dict) else None
    if not isinstance(items, list):
        #: 结构不对 = bridge 改了返回形状。不猜，当作没读到
        logger.warning("最近发过的结构不对：%r", r.data)
        return []

    return [
        str(item.get("body")).strip()
        for item in items
        if isinstance(item, dict) and str(item.get("body") or "").strip()
    ]
