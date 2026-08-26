"""眼睛 —— 主模型读不了图时，先让视觉模型把图看成文字。

## 为什么是「转述」而不是「换模型」

发图那一轮整个切到能看图的模型，看起来更直接，但代价是：
换模型 = 12K 静态前缀缓存整段作废（缓存按「模型 + 前缀字节」匹配），
而且人设、工具定义、情绪三层都得跟着换一遍。
这里只多一次几百毫秒的小调用，主线一个字节都不动。

## 转述发生在进入会话历史「之前」

`nox.py` 拿到描述后，会把它并进 text 并把 images 清空。
所以历史里存的是文字，后面每一轮都不会再为同一张图付一次钱 ——
这同时也是 2026-08-02 那次「一张图毒死整个会话」的根治：
历史里根本不会再出现图片结构。

## 失败就说失败

看不了就返回 None，由调用方如实告诉糖糖。绝不返回一句编的描述 ——
那比看不见更糟：他会拿着编出来的内容跟她聊下去。
"""

from __future__ import annotations

import logging

from config import LLMConfig

logger = logging.getLogger(__name__)


# 这段提示词是写给「另一个 AI 的眼睛」的，不是写给人看的。
#
# ⚠️ 视觉模型默认会吐 markdown 报告（`1. **主体** 2. **特征**`）——
# Stack-chan 那边就栽过，TTS 把编号和星号原样念了出来（见 PROJECT.md 第 8.7 条）。
# 这里虽然不过 TTS，但结构化的报告体会把主模型带进「念清单」的语气，
# 所以同样明确禁掉。
_PROMPT = (
    "你是另一个 AI 的眼睛。请把这张图里的内容如实、具体地讲清楚，让一个看不见它的人"
    "能凭你的描述接着聊下去。\n"
    "要求：\n"
    "1. 用连贯的话讲，不要分点、不要编号、不要 markdown 标记\n"
    "2. 先说这是什么（照片/截图/聊天记录/表格/手写…），再说画面里有什么\n"
    "3. 图里所有可读的文字都要原样抄下来，一个字都别漏，这通常是最关键的信息\n"
    "4. 如果是食物，说清份量、做法、看得出的食材\n"
    "5. 如果是截图或报错，把界面上的字、数字、报错内容完整抄出来\n"
    "6. 只说你真的看见的。看不清就说看不清，不要猜、不要补全\n"
)


def describe(images: list[str], cfg: LLMConfig, user_text: str = "",
             timeout: float = 40.0) -> str | None:
    """把图看成文字。看不了就返回 None，不编。"""
    if not images:
        return None
    if not cfg.api_key:
        logger.info("视觉模型没配 key，跳过看图")
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("没装 openai SDK，看不了图")
        return None

    from agent.llm import split_data_uri

    parts: list[dict] = []
    for img in images:
        media, data = split_data_uri(img)
        parts.append({"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}})

    ask = _PROMPT
    if user_text:
        # 她说的话是有用的上下文：「这件衣服好看吗」和「帮我算热量」
        # 该看的重点完全不同
        ask += f"\n她发这张图时说的是：「{user_text}」。描述时照顾一下她关心的点。"
    parts.append({"type": "text", "text": ask})

    msg = [{"role": "user", "content": parts}]
    try:
        client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url or None, timeout=timeout)
        try:
            r = client.chat.completions.create(
                model=cfg.model, max_tokens=cfg.max_tokens, messages=msg,
                # 🔴 **关掉思考。** 2026-08-22 在共影那边实测：
                # 这个模型（deepseek-v4-flash-vision-exp）思考占输出 token 的 78%，
                # 一次看图 10~15 秒；关掉之后 **2~3 秒**，描述质量一模一样
                # （两版都准确抄下了画面里的字幕和水印）。
                #
                # ⚠️ 别用 minimal / low —— 实测比默认还慢。
                # 也别在 prompt 里写「不要思考」—— 试过，思考反而涨到 1557 token。
                reasoning_effort="none",
            )
        except Exception:  # noqa: BLE001
            # 参数哪天没了别整条链挂掉，退回慢的那条
            logger.warning("reasoning_effort=none 不被接受，退回默认（会慢 3-5 倍）")
            r = client.chat.completions.create(
                model=cfg.model, max_tokens=cfg.max_tokens, messages=msg)
        text = (r.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        # 原样记下来。吞掉错误正是让他开始编的那个动作（第十九节第 3 条）
        logger.warning("看图失败: %s: %s", type(exc).__name__, exc)
        return None

    if not text:
        # ⚠️ 空描述最常见的原因是**思考把 max_tokens 吃光了**
        #（finish_reason=length）。共影那边栽过：一半的请求静默返回空
        logger.warning("视觉模型返回了空描述（finish=%s）",
                       getattr(r.choices[0], "finish_reason", "?"))
        return None

    n = len(images)
    logger.info("看图完成：%d 张 → %d 字", n, len(text))
    return text


def wrap(description: str, count: int) -> str:
    """把描述包成一段给主模型看的话。

    必须讲清楚这是**转述**不是他自己看的 —— 不然他会说「我看到…」，
    而实际上他没有眼睛，细节问不下去。诚实比流畅重要。
    """
    head = f"[她发来 {count} 张图片。你看不了图，以下是你的视觉模型替你看到的内容]"
    tail = "[以上是转述。可以直接聊，但别说得像你亲眼看见的；细节不确定就说不确定]"
    return f"{head}\n{description}\n{tail}"
