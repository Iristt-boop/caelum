"""附带产物（目前只有「要发的图片」）从工具一路传到 LoopResult。

为什么要专门测这条链路：工具跑在 loop 深处，而"把图发进聊天"这个
动作得由最外层的 bridge 执行。中间任何一环没接上，表现都是
**他说"图发过去了"但屏幕上什么都没有** —— 不报错，静悄悄地骗人。

流式那条尤其容易漏：run 和 run_stream 是两个独立入口，
给一个包了 scope 不代表另一个也包了。
"""

from __future__ import annotations

import contextvars
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import StreamEvent, ToolCall, ToolSpec, Turn  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402
from tools import context  # noqa: E402


SPEC = ToolSpec(name="send_gallery_image", description="发图",
                parameters={"type": "object", "properties": {}}, side_effect="read")


def attach_tool(_args: dict) -> str:
    ctx = context.current()
    assert ctx is not None, "工具跑的时候应该在轮次上下文里"
    ctx.attach_image("/uploads/a.jpg", album="夏天", favorited=True)
    return "图片已发送"


class FakeAdapter:
    """第一轮调工具，第二轮说话。"""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def _turns(self):
        return [
            Turn(stop_reason="tool_use", text="",
                 tool_calls=[ToolCall(id="t1", name="send_gallery_image", arguments={})]),
            Turn(stop_reason="end_turn", text="给你看这张"),
        ]

    def complete(self, messages, tools, **kw):
        turn = self._turns()[self.calls]
        self.calls += 1
        return turn

    def stream(self, messages, tools, **kw):
        turn = self._turns()[self.calls]
        self.calls += 1
        if turn.text:
            yield StreamEvent("text", text=turn.text)
        yield StreamEvent("done", turn=turn)


def _loop() -> AgentLoop:
    loop = AgentLoop(adapter=FakeAdapter())
    loop.register(SPEC, attach_tool)
    return loop


def test_非流式带回附件():
    r = _loop().run("发张照片")
    assert r.outcome == "answered"
    assert r.attachments == [
        {"type": "image", "url": "/uploads/a.jpg", "album": "夏天", "favorited": True}
    ]


def test_流式也带回附件():
    """run_stream 是另一个入口 —— 非流式接上了不代表这条也接上了。"""
    result = None
    for ev in _loop().run_stream("发张照片"):
        if ev.type == "done":
            result = ev.result  # type: ignore[attr-defined]
    assert result is not None
    assert [a["url"] for a in result.attachments] == ["/uploads/a.jpg"]


def test_没调工具时附件是空的():
    loop = AgentLoop(adapter=FakeAdapter())
    loop.adapter.calls = 1        # 直接跳到"说话"那轮
    assert loop.run("在吗").attachments == []


def test_两轮之间不串():
    """上下文按轮次隔离 —— 上一轮发的图不能跟着下一轮再发一次。"""
    loop = _loop()
    first = loop.run("发张照片")
    loop.adapter.calls = 1
    second = loop.run("在吗")
    assert len(first.attachments) == 1
    assert second.attachments == []


def test_scope_结束后上下文清空():
    with context.scope() as ctx:
        ctx.attach_image("/uploads/x.jpg")
    assert context.current() is None


def test_流式每步在各自的_context_里也不炸():
    """线上炸过的那个 bug 的回归用例。

    Starlette 用线程池逐步推同步生成器，**每次 next() 都在自己复制的
    Context 里跑**。上面那个 test_流式也带回附件 是在同一个 Context 里
    一路 for 下来的，压根测不出这个问题 —— 本地全绿，上线一调工具就
    `ValueError: Token was created in a different Context`。

    这里照着 Starlette 的做法，每次 next() 都换一个 Context 副本。
    """
    gen = _loop().run_stream("发张照片")
    result = None
    while True:
        try:
            ev = contextvars.copy_context().run(next, gen)
        except StopIteration:
            break
        if ev.type == "done":
            result = ev.result  # type: ignore[attr-defined]

    assert result is not None
    assert [a["url"] for a in result.attachments] == ["/uploads/a.jpg"]


# ---------------------------------------------------- 正文 [tag] 兜底抽取

def test_extract_text_tags_any_position():
    """他不调 send_meme、把 [开心] 直接写进正文时的兜底 ——
    任何位置都抽出来转成表情事件（2026-09-06 她报的降级问题）。"""
    from tools.intimate import extract_text_tags

    # 混在一段话中间
    # ⚠️ 这里刻意让**正文里也出现一遍同样的字**，验证只剥 `[…]` 那份、
    #    不误伤正文。tag 名跟着 `agent/llm.py` 的 MEME_TAGS 走 ——
    #    2026-09-08 那次改名（早安亲亲 → where my kiss）说是「五处同步」，
    #    漏的第六处就是这儿，测试红了两条
    text, tags = extract_text_tags("得了你一个抱抱[抱抱]真好")
    assert text == "得了你一个抱抱真好"
    assert tags == ["抱抱"]

    # 多个、保持顺序
    text, tags = extract_text_tags("[开心]早安[暖被窝]")
    assert text == "早安"
    assert tags == ["开心", "暖被窝"]

    # 没有 tag 原样返回
    text, tags = extract_text_tags("普通一句话")
    assert (text, tags) == ("普通一句话", [])

    # 空值安全
    assert extract_text_tags(None) == (None, [])
