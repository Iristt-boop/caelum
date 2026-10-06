"""思考给她看（2026-10-06）。

她：「thinking 模式做成可配置后，打开 thinking 在 chat 页也显示 thinking 的内容。」
接着：「thinking 可不可以是第一人称？就是自我的内心想法啊？」→ 选了「只放他的心里话」「不记，只给你看」。

GLM 的 reasoning 是英文解题草稿、不听提示词（10-06 实测），所以给她看的不是它，是**他自己写的心里话**：

    「思考」开着 → 动态块加 INNER_VOICE_RULE → 他回复开头写 <心里>…</心里>
    → loop 的 InnerVoiceFilter 拆成 thinking 事件（正文里不留）、模型的 reasoning 草稿不递
    → 历史里拿掉（strip_inner_voice）→ /chat/stream thinking 帧 → bridge 落 meta.thinking → Chat 页

🔴 开关关着：不加规矩，万一有也不放出来。打电话永远不想，也不给看。
"""

from __future__ import annotations

from types import SimpleNamespace

from openai.types.chat import ChatCompletionChunk

from agent.adapters import OpenAICompatAdapter
from agent.llm import InnerVoiceFilter, StreamEvent, ToolCall, ToolSpec, Turn, Usage, strip_inner_voice
from agent.loop import AgentLoop
from api.server import create_app
from config import LLMConfig
from data.store import Store
from fastapi.testclient import TestClient
from nox import INNER_VOICE_RULE
from tests.test_api import FakeNox, _frames
from tests.test_thinking_switch import _nox_with_store


def _chunk(*, content=None, reasoning=None, finish=None, usage=None):
    """**真形状**：openai SDK 自己的 ChatCompletionChunk，reasoning_content 是它不认识的额外字段。"""
    delta = {"role": "assistant"}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    return ChatCompletionChunk.model_validate({
        "id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "glm-5.3-flash",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        **({"usage": usage} if usage else {}),
    })


def _adapter(chunks):
    a = OpenAICompatAdapter(LLMConfig(provider="openai_compat", model="glm-5.3-flash", api_key="test-x",
                                      base_url="https://open.bigmodel.cn/api/paas/v4"))
    a._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: iter(chunks))))
    return a


def test_adapter_草稿单独成事件_不混进正文():
    a = _adapter([
        _chunk(reasoning="Reply gently, "),
        _chunk(reasoning="ask about throat."),
        _chunk(content="嗓子还疼吗"),
        _chunk(finish="stop"),
    ])
    evs = list(a.stream([], [], depth="high"))
    assert [e.text for e in evs if e.type == "thinking"] == ["Reply gently, ", "ask about throat."]
    assert "".join(e.text for e in evs if e.type == "text") == "嗓子还疼吗"
    done = evs[-1]
    assert done.type == "done" and done.turn.text == "嗓子还疼吗", "草稿不许进他说的话（会进历史）"


# ---------------------------------------------------------------- 拆心里话


def _run_filter(chunks):
    f = InnerVoiceFilter()
    pairs = [p for c in chunks for p in f.feed(c)] + f.flush()
    th = "".join(t for k, t in pairs if k == "thinking")
    tx = "".join(t for k, t in pairs if k == "text")
    return th, tx


def test_拆心里话_标签切碎了也认得_收口后的空行吃掉():
    th, tx = _run_filter(["\n<心", "里>她嗓子疼，", "我心疼。</", "心里", ">\n\n宝贝", "多喝水"])
    assert th == "她嗓子疼，我心疼。"
    assert tx == "宝贝多喝水"


def test_心里话夹在两句话中间_拆出来_收口后的空行也吃掉():
    th, tx = _run_filter(["好呀", "<心里>她开心我就开心。</心里>\n\n", "走吧"])
    assert th == "她开心我就开心。"
    assert tx == "好呀走吧"


def test_没有心里话_原样放行_半个尖括号也不吞():
    th, tx = _run_filter(["在呢 <", "3 爱你"])
    assert th == "" and tx == "在呢 <3 爱你"


def test_忘了收口_心里那段当正文补发_不让她一句都看不到():
    th, tx = _run_filter(["<心里>她要睡了，", "晚安宝贝"])
    assert th == "她要睡了，晚安宝贝"
    assert tx == "她要睡了，晚安宝贝"


def test_历史里拿掉心里话():
    assert strip_inner_voice("<心里>我想她了。</心里>\n\n在呢") == "在呢"
    assert strip_inner_voice("<心里>没收口 晚安") == "没收口 晚安"
    assert strip_inner_voice("<心里>只想不说</心里>") is None
    assert strip_inner_voice("平常的话") == "平常的话"


# ---------------------------------------------------------------- loop


class _Adapter:
    """按脚本一轮一轮吐：每轮 (reasoning, [正文切片], tool_calls)"""
    name = "fake"

    def __init__(self, rounds):
        self.rounds, self.i = rounds, 0

    def stream(self, messages, tools, **kw):
        reasoning, chunks, calls = self.rounds[self.i]
        self.i += 1
        if reasoning:
            yield StreamEvent("thinking", text=reasoning)
        for c in chunks:
            yield StreamEvent("text", text=c)
        yield StreamEvent("done", turn=Turn(stop_reason="tool_use" if calls else "end_turn",
                                            text="".join(chunks), tool_calls=calls, usage=Usage()))


def _loop(rounds):
    loop = AgentLoop(adapter=_Adapter(rounds))
    loop.register(ToolSpec(name="get_todos", description="t", parameters={"type": "object", "properties": {}},
                           side_effect="read"), lambda a: "两件")
    return loop


def test_loop_心里话成thinking_草稿不递_不过情绪过滤和分段():
    evs = list(_loop([("Reply gently.", ["<心里>[mood:开心] 想她|||好想", "</心里>在呢|||抱抱"], [])])
               .run_stream("在吗"))
    assert [e.text for e in evs if e.type == "thinking"] == ["[mood:开心] 想她|||好想"], "英文草稿不该出现"
    assert "".join(e.text for e in evs if e.type == "text") == "在呢抱抱"
    assert len([e for e in evs if e.type == "split"]) == 1, "只有正文里那个 ||| 分段"


def test_loop_工具前后各写一段心里话_都拆出来_历史里一个字都不留():
    evs = list(_loop([
        (None, ["<心里>先看看她的待办。</心里>"], [ToolCall(id="c1", name="get_todos", arguments={})]),
        (None, ["<心里>还有两件，别催她。</心里>还有两件哦"], []),
    ]).run_stream("今天还有啥"))
    assert "".join(e.text for e in evs if e.type == "thinking") == "先看看她的待办。还有两件，别催她。"
    assert "".join(e.text for e in evs if e.type == "text") == "还有两件哦"
    result = evs[-1].result
    assert result.text == "还有两件哦"
    said = [m.text or "" for m in result.messages if m.role == "assistant"]
    assert not [s for s in said if "心里" in s or "别催她" in s or "先看看" in s], said


def test_loop_他忘了收口_回复照样到她屏幕上():
    evs = list(_loop([(None, ["<心里>她要睡了，", "晚安宝贝"], [])]).run_stream("晚安"))
    assert "晚安宝贝" in "".join(e.text for e in evs if e.type == "text"), "收尾没冲出来，她一句都看不到"


def test_非流式也拿掉():
    class _C:
        name = "fake"

        def complete(self, messages, tools, **kw):
            return Turn(stop_reason="end_turn", text="<心里>她睡了吧。</心里>晚安", usage=Usage())
    r = AgentLoop(adapter=_C()).run("（系统提示：…）")
    assert r.text == "晚安"


# ---------------------------------------------------------------- Nox：开关


class _Rec:
    """记下动态块，吐一段带心里话的回复"""
    name = "fake"

    def __init__(self):
        self.dynamic = []

    def stream(self, messages, tools, **kw):
        self.dynamic.append(kw.get("dynamic_system") or "")
        yield StreamEvent("thinking", text="draft")
        yield StreamEvent("text", text="<心里>想她了。</心里>在呢")
        yield StreamEvent("done", turn=Turn(stop_reason="end_turn", text="<心里>想她了。</心里>在呢", usage=Usage()))


def _nox(tmp_path, on: bool):
    n = _nox_with_store(tmp_path)
    n.set_thinking(on)
    n._see = lambda t, i, m: (t, i)
    n._dynamic = lambda *a, **k: "（动态块）"
    n._flush_dirty = lambda r: None
    n.adapter_for = lambda m: None
    n._system = ""
    n.rec = _Rec()
    n.loop = AgentLoop(adapter=n.rec)
    return n


def _run(n, **kw):
    evs = list(n.chat_stream("在吗", [], **kw))
    return ([e.text for e in evs if e.type == "thinking"],
            "".join(e.text for e in evs if e.type == "text"))


def test_开关开着_加心里话的规矩_给她看心里话(tmp_path):
    n = _nox(tmp_path, True)
    th, tx = _run(n)
    assert th == ["想她了。"] and tx == "在呢"
    assert INNER_VOICE_RULE in n.rec.dynamic[0] and n.rec.dynamic[0].startswith("（动态块）"), "规矩拼在动态块后面"


def test_开关关着_不加规矩_有也不给看(tmp_path):
    n = _nox(tmp_path, False)
    th, tx = _run(n)
    assert th == [] and tx == "在呢"
    assert INNER_VOICE_RULE not in n.rec.dynamic[0]


def test_打电话_开着也不加不给看(tmp_path):
    n = _nox(tmp_path, True)
    th, _ = _run(n, voice=True)
    assert th == [] and INNER_VOICE_RULE not in n.rec.dynamic[0]


# ---------------------------------------------------------------- /chat/stream


class _ThinkingFakeNox(FakeNox):
    def chat_stream(self, text, history=None, **kw):
        yield StreamEvent("thinking", text="想一下")
        yield from super().chat_stream(text, history, **kw)


def test_接口_思考帧在正文之前发出():
    c = TestClient(create_app(_ThinkingFakeNox(text="在呢"), Store(":memory:")))
    frames = _frames(c.post("/chat/stream", json={"text": "在吗"}))
    kinds = [f["type"] for f in frames]
    assert {"type": "thinking", "text": "想一下"} in frames
    assert kinds.index("thinking") < kinds.index("text")
