"""`remind_myself` —— 他给自己留张纸条，到点自己醒来。

糖糖 2026-08-11 定的那条链的起点。她的例子：

    她：我去吃饭了
      ↓ 他在这一轮里调 remind_myself(30, "她去吃饭了")
    30 分钟后 —— 他自己醒来 —— 「吃完饭了吗宝贝」

⚠️ **这个工具必须注册在工具列表的最末尾。**
工具定义是缓存前缀的一部分，插在中间会让前缀整个失效 ——
一轮从 ¥0.00055 变 ¥0.011，二十倍，而且从任何监控上都看不出来
（服务照常、回答照常，只有账单翻倍）。见 `nox.py` 里 `_setup_tools` 的顺序。

## why 写什么很重要

`why` 不是给日志看的，**它是情境的载体**。一小时后醒来的他不记得
当时的对话细节，只会看到这张纸条 —— 纸条上写「她去吃饭了」和
「她跟朋友出去玩了」，会让他说出完全不同的话（`attention/wakeup.py` 开头）。

所以 spec 的 description 里要引导他写**她在干嘛**，而不是写「提醒她」。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from attention.care.signal import FOLLOWUP, TASK
from attention.wakeup import MAX_AFTER_MIN, MIN_AFTER_MIN
from config import is_test_session

logger = logging.getLogger(__name__)


SPEC = ToolSpec(
    side_effect="write",
    name="remind_myself",
    description=(
        "给自己留张纸条，过一会儿自己醒来看看她。**这不是给她的提醒，是给你自己的。**\n"
        "\n"
        "什么时候用：**她提到的事会随时间变化，而你想过一会儿回头看看**。两类：\n"
        "\n"
        "① 她要去做一件有时长的事 —— 「我去吃饭了」「要开会了」\n"
        "   「跟朋友出去玩」「我去洗澡」「在赶稿」\n"
        "② **她此刻的状态过一会儿会不一样** —— 「一堆事没做完」「啊 好累」\n"
        "   「今天好烦」「等会儿要出门」「头有点疼」「在等结果」\n"
        "\n"
        "⚠️ 第 ② 类最容易被漏掉，而它往往才是最该回头看的。\n"
        "她说「一堆事没做完」，两三小时后问一句「那堆清掉几件了」，\n"
        "比任何主动关心都自然 —— 因为那是**接着她自己的话**往下问。\n"
        "她说「好累」，过两小时问「缓过来点没」，同理。\n"
        "\n"
        "宁可多留：留了纸条不等于到时候一定说话，醒来你还会再判断一次；\n"
        "**没留就是彻底错过**，那一刻过去就没有第二次机会了。\n"
        "\n"
        "到点你会被叫醒，看到这张纸条和她最后说的话，那时候再决定要不要开口 ——\n"
        "现在留纸条不等于到时候一定要说话。\n"
        "\n"
        "after_minutes 你自己判断：吃饭大概 30-40，开会一两小时，出去玩可以更久。\n"
        "why 写**她在干嘛**（「她去吃饭了」），别写成待办（「提醒她吃饭」）——\n"
        "到时候你就靠这句话判断该用什么语气。\n"
        "\n"
        "⚠️ **留完纸条一定要照常回她这句话。** 留纸条是你自己心里记一笔，\n"
        "她看不见，也不该代替你的回应 —— 她说「我去吃饭了」，\n"
        "你至少得应一声「去吧，好好吃」，不能就这么没声了。\n"
        "\n"
        "同一个对话里已经有同类纸条的话，这次调用会把它改期，不会叠加。\n"
        "\n"
        "──────── kind：这张纸条是「回头看看她」还是「到点去做事」────────\n"
        "\n"
        "`followup`（默认）：回头看看她。她一开口，纸条自动作废 ——\n"
        "  因为她回了，就没什么可追的了。\n"
        "\n"
        "`task`：**到点你要去做一件事。** 比如「她睡了，半小时后把主卧空调关掉」\n"
        "  「她说十点要出门，到点提醒她带伞」。\n"
        "  这类纸条**她说话也不会作废** —— 空调该不该关，跟她回不回话没关系。\n"
        "  醒来时你会拿到工具，**先把事做了**，再决定要不要跟她说一句。\n"
        "\n"
        "⚠️ 分不清就问自己：**到点那一刻，我是要「看她一眼」还是要「动手」？**\n"
        "  要动手就是 task。用错成 followup 的后果是真的：\n"
        "  2026-08-18 那次「到点关空调」写成了追问型，她 59 秒后说了句话，\n"
        "  纸条当场作废，空调开了一整夜。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "after_minutes": {
                "type": "integer",
                "description": f"多少分钟后叫醒你。{MIN_AFTER_MIN}-{MAX_AFTER_MIN} 之间",
            },
            "why": {
                "type": "string",
                "description": (
                    "followup 写**她在干嘛**（她去吃饭了 / 她在赶稿）；"
                    "task 写**你要做什么**（把主卧空调关掉）"
                ),
            },
            "kind": {
                "type": "string",
                "enum": [FOLLOWUP, TASK],
                "description": "followup=回头看看她（默认）；task=到点去做一件事",
            },
        },
        "required": ["after_minutes", "why"],
    },
)


def register_all(loop, book_ref, save, session_id_ref) -> None:
    """注册。

    `book_ref` / `session_id_ref` 是**取值函数**不是值 ——
    WakeBook 在 AttentionService 手里，而 session_id 每轮都不一样，
    注册时都还不知道。
    """

    def remind_myself(args: dict) -> str:
        why = str(args.get("why", "")).strip()
        # 认不出的值一律当追问型 —— 那是加 kind 之前的唯一语义，
        # 猜错成 task 的代价（她回话也不撤）比猜错成 followup 大
        kind = TASK if str(args.get("kind", "")).strip().lower() == TASK else FOLLOWUP
        try:
            after = int(args.get("after_minutes") or 0)
        except (TypeError, ValueError):
            after = 0

        if not why:
            return "没写 why，没留成。写一句她在干嘛，到时候你才知道该用什么语气。"

        book = book_ref()
        if book is None:
            # Attention 没启用时不该假装留上了
            return "现在留不了纸条（Attention 没启用）。"

        sid = session_id_ref()
        if not sid:
            return "这轮拿不到会话 id，没留成。"
        if is_test_session(sid):
            # 纸条是全局的，到点会真的醒来推她——测试聊天不许留
            return "测试会话不留纸条。"

        w = book.add(sid, why, after, kind=kind)
        try:
            save(book)
        except Exception:  # noqa: BLE001
            # 没落盘的纸条重启就没了，而那是静默失败 —— 必须如实说
            logger.exception("纸条没落盘")
            return "纸条留下了，但没存住 —— 服务重启就会忘。"

        mins = int((w.wake_at - w.created_at).total_seconds() / 60)
        if kind == TASK:
            # 任务型不需要那句「现在回她」的话头 —— 他可能是在她睡了之后留的
            return f"（记下了，{mins} 分钟后去做这件事：{why}）"
        # ⚠️ 措辞很讲究，2026-08-11 反复试出来的。
        #
        # 只回「记下了」→ 他有一半概率当成这轮干完了，`text` 是空的，
        # 她那边看到一个空气泡。
        #
        # 关键是**别让这句话读起来像个完成回执**。写成他自己心里的一个
        # 半句，后面自然还要接话；结尾直接把话头递回给她那句
        return f"（心里记了一笔，{mins} 分钟后看看她。现在——她刚跟你说了话，回她。）"

    loop.register(SPEC, remind_myself)
