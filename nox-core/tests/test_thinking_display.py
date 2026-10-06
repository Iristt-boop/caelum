"""思考给她看（2026-10-06）。

她：「thinking 模式做成可配置后，打开 thinking 在 chat 页也显示 thinking 的内容。」

原来模型的思考（GLM / DeepSeek 的 `reasoning_content`）在 adapter 那层就丢了 ——
手机 Chat 页早就有折叠的 thinking 块，一直收不到东西。现在一路递出去：

    adapter（thinking 事件）→ loop（原样递，不进正文）→ Nox.chat_stream（**开关开着才放行**）
    → /chat/stream（thinking 帧）→ bridge（转发 + 落 meta.thinking）→ Chat 页

🔴 开关关着时不给看：GLM 的 low 档也会漏几个字的 reasoning（10-06 实测 11 字），
关了还冒出思考块，开关就说不清了。打电话永远不想，也不给看。
"""

from __future__ import annotations

from types import SimpleNamespace

from openai.types.chat import ChatCompletionChunk

from agent.adapters import OpenAICompatAdapter
from agent.llm import StreamEvent, Turn, Usage
from agent.loop import AgentLoop
from api.server import create_app
from config import LLMConfig
from data.store import Store
from fastapi.testclient import TestClient
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


def test_adapter_思考单独成事件_不混进正文():
    a = _adapter([
        _chunk(reasoning="她今天感冒，"),
        _chunk(reasoning="先问嗓子。"),
        _chunk(content="嗓子还疼吗"),
        _chunk(finish="stop"),
    ])
    evs = list(a.stream([], [], depth="high"))
    assert [e.text for e in evs if e.type == "thinking"] == ["她今天感冒，", "先问嗓子。"]
    assert "".join(e.text for e in evs if e.type == "text") == "嗓子还疼吗"
    done = evs[-1]
    assert done.type == "done" and done.turn.text == "嗓子还疼吗", "思考不许进他说的话（会进历史）"


# ---------------------------------------------------------------- loop


class _ThinkingAdapter:
    name = "fake"

    def stream(self, messages, tools, **kw):
        yield StreamEvent("thinking", text="[mood:开心] 想想|||怎么说")
        yield StreamEvent("text", text="在呢")
        yield StreamEvent("done", turn=Turn(stop_reason="end_turn", text="在呢", usage=Usage()))


def test_loop_原样递出思考_不过滤不分段_不进结果():
    evs = list(AgentLoop(adapter=_ThinkingAdapter()).run_stream("在吗"))
    th = [e.text for e in evs if e.type == "thinking"]
    assert th == ["[mood:开心] 想想|||怎么说"], "草稿纸不过情绪过滤和分段"
    assert not [e for e in evs if e.type == "split"]
    result = evs[-1].result
    assert result.text == "在呢"
    assert all("想想" not in (m.text or "") for m in result.messages), "思考不进历史"


# ---------------------------------------------------------------- Nox：开关


def _nox(tmp_path, on: bool):
    n = _nox_with_store(tmp_path)
    n.set_thinking(on)
    n._see = lambda t, i, m: (t, i)
    n._dynamic = lambda *a, **k: ""
    n._flush_dirty = lambda r: None
    n.adapter_for = lambda m: None
    n._system = ""
    n.loop = AgentLoop(adapter=_ThinkingAdapter())
    return n


def _thoughts(n, **kw):
    return [e.text for e in n.chat_stream("在吗", [], **kw) if e.type == "thinking"]


def test_开关开着_给她看思考(tmp_path):
    assert _thoughts(_nox(tmp_path, True)) == ["[mood:开心] 想想|||怎么说"]


def test_开关关着_一个字都不给看(tmp_path):
    assert _thoughts(_nox(tmp_path, False)) == []


def test_打电话_开着也不给看(tmp_path):
    assert _thoughts(_nox(tmp_path, True), voice=True) == []


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
