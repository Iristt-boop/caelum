"""2026-10-10 她早上七点的截图：同一轮回复被说了两遍 + `[表情包] 举爪开心` 当文字漏出来。

1. 说过话 → 调工具 → 回来又把同一件事换个说法说一遍。生成时的事，流里递出去的收不回来，
   所以这一轮后面的每次调用，动态块里要带一条「你已经说过了」。没说过话的轮次不带。
2. 他把表情写成 `[表情包] 开心`（不调 send_meme、也不是 `[开心]` 那种）：
   流里整行吞掉，收尾时抽出 tag 转成真表情；名单外的也剥，不能留在正文和历史里教他下次接着写。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import MoodTagFilter, StreamEvent, ToolCall, ToolSpec, Turn  # noqa: E402
from agent.loop import SPOKEN_NOTE, AgentLoop  # noqa: E402
from tools.intimate import extract_text_tags  # noqa: E402


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="t", parameters={"type": "object", "properties": {}},
                    side_effect="read")


class _Fake:
    name = "fake"

    def __init__(self, script: list[Turn]) -> None:
        self.script = script
        self.dyn: list[str | None] = []

    def complete(self, messages, tools, *, system=None, dynamic_system=None, depth=None, max_tokens=None):
        self.dyn.append(dynamic_system)
        return self.script.pop(0)

    def stream(self, messages, tools, *, system=None, dynamic_system=None, depth=None, **kw):
        self.dyn.append(dynamic_system)
        t = self.script.pop(0)
        if t.text:
            yield StreamEvent("text", text=t.text)
        yield StreamEvent("done", turn=t)


def _tool_turn(text):
    return Turn(stop_reason="tool_use", text=text,
                tool_calls=[ToolCall(id="c1", name="w", arguments={})])


def _loop(script):
    a = _Fake(script)
    lp = AgentLoop(adapter=a)
    lp.register(_spec("w"), lambda args: "ok")
    return a, lp


def _drain(lp):
    return [ev for ev in lp.run_stream("在吗")]


# ------------------------------------------------------------ 1. 别重说

@pytest.mark.parametrize("streaming", [False, True])
def test_说过话再调工具_回来那一轮带上别重说(streaming):
    a, lp = _loop([_tool_turn("验收通过"), Turn(stop_reason="end_turn", text="记好了")])
    _drain(lp) if streaming else lp.run("在吗")
    assert a.dyn[0] is None or SPOKEN_NOTE not in a.dyn[0], "第一次调用还没说过话，不该带"
    assert a.dyn[1] and SPOKEN_NOTE in a.dyn[1]


@pytest.mark.parametrize("streaming", [False, True])
def test_没说话就调工具_回来不带(streaming):
    a, lp = _loop([_tool_turn(None), Turn(stop_reason="end_turn", text="记好了")])
    _drain(lp) if streaming else lp.run("在吗")
    assert all(d is None or SPOKEN_NOTE not in d for d in a.dyn)


def test_原有动态块保留_别重说拼在后面():
    a = _Fake([_tool_turn("先说一句"), Turn(stop_reason="end_turn", text="好")])
    lp = AgentLoop(adapter=a)
    lp.register(_spec("w"), lambda args: "ok")
    lp.run("在吗", dynamic_system="【现在】07:00")
    assert a.dyn[0] == "【现在】07:00"
    assert a.dyn[1].startswith("【现在】07:00") and a.dyn[1].endswith(SPOKEN_NOTE)


def test_空白不算说过话():
    a, lp = _loop([_tool_turn("  \n"), Turn(stop_reason="end_turn", text="好")])
    lp.run("在吗")
    assert a.dyn[1] is None or SPOKEN_NOTE not in a.dyn[1]


# ------------------------------------------------------------ 2. [表情包] xxx

def _stream(chunks):
    f = MoodTagFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_流里_表情包标记整行吞掉_正文留着():
    assert _stream(["先吃饭。\n\n[表情包] 举爪开心"]) == "先吃饭。\n\n"


def test_流里_一个字符一片到达也吞():
    assert _stream(list("好的\n[表情包] 开心\n下一句")) == "好的\n下一句"


@pytest.mark.parametrize("raw", ["[表情包: 开心]", "[表情包：开心]", "[表情包]开心", "[表情包] [开心]"])
def test_流里_各种写法都吞(raw):
    assert _stream(["在呢\n", raw]) == "在呢\n"


def test_流里_不是这个词的方括号不受影响():
    assert _stream(["看 [表演] 和 [表情] 吧"]) == "看 [表演] 和 [表情] 吧"


@pytest.mark.parametrize("raw,tag", [
    ("[表情包] 举爪开心", "举爪开心"),
    ("[表情包] 开心", "开心"),
    ("[表情包: 晚安]", "晚安"),
    ("[表情包：晚安]", "晚安"),
    ("[表情包]早安", "早安"),
    ("[表情包] [暖被窝]", "暖被窝"),
])
def test_收尾_抽出tag转成真表情(raw, tag):
    text, tags = extract_text_tags(f"先吃饭。\n\n{raw}")
    assert tags == [tag]
    assert text == "先吃饭。"


def test_收尾_名单外的写法也剥掉_但不发():
    text, tags = extract_text_tags("在呢\n[表情包] 自己编的名字")
    assert tags == []
    assert text == "在呢"


def test_收尾_老写法不受影响():
    assert extract_text_tags("得了你一个抱抱[抱抱]真好") == ("得了你一个抱抱真好", ["抱抱"])
    assert extract_text_tags("普通一句话") == ("普通一句话", [])


@pytest.mark.parametrize("long,short", [("爱你的形状", "爱你"), ("震惊爆炸瞪眼", "震惊")])
def test_收尾_长名字不被短名字截走(long, short):
    # 名单里有名字是另一个名字的开头：短的排前面会先咬走前半截，留下「的形状」当正文
    from agent.llm import MEME_TAGS
    assert long in MEME_TAGS and short in MEME_TAGS
    text, tags = extract_text_tags("在呢" + chr(10) + f"[表情包] {long}")
    assert tags == [long]
    assert text == "在呢"


def test_收尾_举爪开心():
    text, tags = extract_text_tags("[表情包] 举爪开心")
    assert tags == ["举爪开心"]
