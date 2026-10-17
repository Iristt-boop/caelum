"""set_mood —— 情绪判断的原生工具（2026-10-15 Aru 化改造）。

## 以前的做法，以及为什么换掉

情绪判断原来是**正文文本协议**：MOOD_INSTRUCTION（personality/mood.py）教模型
在回复末行写 `[mood:xxx]`，然后三级正则（mood.extract）+ 流式过滤器
（MoodTagFilter）追着堵变体 —— 大写 M（09-06）、带空格（09-22）、中文键
（10-08）、尖括号（10-11）、`<[mood:平静]>` 嵌套（10-15 她截图）。
词表永远追不上他编新的（agent/llm.py STAGE_TAG 注释里的原话）。

现在改成和 send_meme 一样的原生工具：结构化参数、enum 收死、
名单外退回重选 —— 「换个写法漏进明文」这类 bug 在协议上不存在了。
参照对象就是 Aru（Aevella/aru-host）的做法：菜单即契约，
不给模型"在散文里写协议"的自由度。

## 垫片与回退

- 文本标签通道没有拆：nox.py 收尾的 `mood.extract` 常驻，模型万一还写
  `[mood:xxx]` 照样接得住（ MoodTagFilter 流式同步剥）。
- `NOX_MOOD_TOOL=off` 回退纯文本协议：本工具不注册、
  MOOD_INSTRUCTION 回到动态块（开关在 personality/mood.py.mood_tool_on）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from agent.llm import ToolSpec
from agent.llm import HER_EMOTIONS

logger = logging.getLogger(__name__)

MOOD_SPEC = ToolSpec(
    side_effect="none",
    name="set_mood",
    terminal=True,   # 发出去就完（减重第二步，10-17）
    description=(
        "记下你对糖糖**这一轮**情绪的判断，给你的下一轮定调 —— "
        "她会得到更连贯的接法（她难过时你下一轮还记得心疼）。"
        "**每轮回复里调一次**，什么时候调都行，推荐说完话之后。"
        "**只能用这几个词，一字不差**：开心、难过、烦躁、撒娇、兴奋、疲惫、平静。"
        "拿不准就用「平静」，别猜。这个判断她看不到，是你自己的笔记 —— "
        "所以不需要在正文里再写一遍心情，好好说话就行。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "mood": {
                "type": "string",
                "enum": list(HER_EMOTIONS),
                "description": "她这一轮的情绪，七选一",
            },
        },
        "required": ["mood"],
    },
)


def register_all(loop,
                 mood_ref: Callable[[], Any],
                 save: Callable[[], None]) -> None:
    """注册 set_mood。

    mood_ref 取值函数而不是值：工具在 nox.py 注册链里挂的时候 `self.mood`
    还没建（同 luckin 的 store_ref 惯例），调用时现取。save 是 nox 的
    `_save_state` —— update 之后同步落盘，和收尾抽取那条路一个待遇。
    """

    def set_mood(args: dict) -> str:
        word = str(args.get("mood", "")).strip()
        m = mood_ref()
        if word not in HER_EMOTIONS:
            # 名单外不记、退回重选（同 send_meme 09-28 的教训：放水会落一堆
            # 查无此词的状态；这里更轻 —— 只是情绪记错，但规矩一致）
            logger.info("set_mood 用了名单外的词「%s」，退回重选", word[:40])
            return (f"「{word[:40]}」不在情绪名单里，没有记录。"
                    f"只能用：{'、'.join(HER_EMOTIONS)}。重新调一次 set_mood。")
        m.update(word)
        save()
        return f"已记下她这轮的状态是「{word}」。不用对她说，接着把话说完就行。"

    loop.register(MOOD_SPEC, set_mood)
