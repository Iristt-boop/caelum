"""分块账本（减重设计稿第一节，2026-10-16）。

守的几条：
  · 四块（前缀 / 工具 / 动态 / 历史）各多重，chars + 估算 tokens 双口径
  · 每次调模型一行：in / cached / 调了哪些工具 / 这次有没有对她说话（had_text）——
    第二步「终结型工具不再多跑一轮」要靠 had_text
  · 流式 / 非流式两条路都带；从使用方断言：done 帧、ChatResponse、/health 真拿得到
  · 他自己的时间的用量单独记成 own_time（以前哪本账都没有）
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent.llm import Message, StreamEvent, ToolCall, ToolSpec, Turn, Usage
from agent.loop import AgentLoop, LoopResult, measure_ledger
from context.token_budget import estimate_text_tokens
from temporal import CST


def _spec(name):
    return ToolSpec(name=name, description="发个表情", parameters={"type": "object", "properties": {}},
                    side_effect="write")


def _turns():
    """第一次：说了话 + 调 send_meme；第二次：收尾，没再说话"""
    return [
        Turn(stop_reason="tool_use", text="想你啦",
             tool_calls=[ToolCall(id="c1", name="send_meme", arguments={"name": "shy"})],
             usage=Usage(input_tokens=33000, output_tokens=40, cache_read_tokens=32000)),
        Turn(stop_reason="end_turn", text="",
             usage=Usage(input_tokens=33100, output_tokens=5, cache_read_tokens=33000)),
    ]


class _Ad:
    name = "fake"

    def __init__(self):
        self.script = _turns()

    def complete(self, messages, tools, **kw):
        return self.script.pop(0)

    def stream(self, messages, tools, **kw):
        t = self.script.pop(0)
        if t.text:
            yield StreamEvent("text", text=t.text)
        yield StreamEvent("done", turn=t)


def _loop():
    loop = AgentLoop(adapter=_Ad())
    loop.register(_spec("send_meme"), lambda a: "发了")
    return loop


HISTORY = [Message(role="user", text="早安"), Message(role="assistant", text="早呀，乖")]


def test_四块各多重_双口径():
    led = measure_ledger("人设" * 100, "【你在哪】手机", [_spec("send_meme")], HISTORY, "在吗")
    assert led["prefix"] == {"chars": 200, "tokens": estimate_text_tokens("人设" * 100)}
    assert led["tools"]["count"] == 1 and led["tools"]["chars"] > 0
    assert led["dynamic"]["chars"] == len("【你在哪】手机")
    assert led["history"]["messages"] == 2 and led["history"]["chars"] == len("早安") + 1 + len("早呀，乖")
    assert led["user"]["chars"] == 2


def _check_calls(result: LoopResult):
    calls = result.ledger["calls"]
    assert [(c["n"], c["in"], c["cached"], c["tools"], c["had_text"]) for c in calls] == [
        (1, 33000, 32000, ["send_meme"], True),
        (2, 33100, 33000, [], False),
    ], "第二步要靠 had_text：第一次调工具时话已经说完了"
    assert result.ledger["blocks"]["history"]["messages"] == 2


def test_红线_非流式_每次调用一行():
    r = _loop().run("在吗", system="人设", history=list(HISTORY))
    assert r.ok
    _check_calls(r)


def test_红线_流式_每次调用一行():
    done = [ev for ev in _loop().run_stream("在吗", system="人设", history=list(HISTORY)) if ev.type == "done"]
    _check_calls(done[0].result)


def test_心里话不算对她说话():
    loop = _loop()
    loop.adapter.script[0].text = "<心里>她今天好安静</心里>"
    r = loop.run("在吗", system="人设", history=[])
    assert r.ledger["calls"][0]["had_text"] is False


# ---- 从使用方断言：done 帧 / ChatResponse / /health ----

def _client_with_ledger():
    from api.server import create_app
    from data.store import Store
    from tests.test_api import FakeNox

    nox = FakeNox()
    led = {"blocks": {"prefix": {"chars": 10, "tokens": 6}}, "calls": [{"n": 1, "in": 100, "cached": 80,
                                                                       "out": 10, "tools": [], "had_text": True}]}
    orig = nox._result

    def with_ledger(history, text):
        r = orig(history, text)
        r.ledger = led
        return r

    nox._result = with_ledger
    return TestClient(create_app(nox, Store(":memory:"))), led


def test_红线_done帧和health带账本():
    import json
    c, led = _client_with_ledger()
    body = c.post("/chat/stream", json={"text": "在吗", "session_id": "test-ledger"}).text
    frames = [json.loads(l[6:]) for l in body.split("\n") if l.startswith("data: ")]
    done = next(f for f in frames if f["type"] == "done")
    assert done["ledger"] == led
    assert c.get("/health").json()["last_turn"] == led


def test_非流式ChatResponse带账本():
    c, led = _client_with_ledger()
    assert c.post("/chat", json={"text": "在吗", "session_id": "test-ledger2"}).json()["ledger"] == led


def test_他自己的时间的用量单独记成own_time(monkeypatch):
    from agent import meter
    from attention import own_time

    seen = []
    monkeypatch.setattr(meter, "record", lambda task, model, usage: seen.append((task, usage.input_tokens)))

    class L:
        adapter = SimpleNamespace(cfg=SimpleNamespace(model="deepseek-flash"))

        def run_stream(self, *a, **k):
            ev = StreamEvent("done")
            ev.result = LoopResult("answered", "做了什么：发呆\n想跟她说：无", 1, Usage(input_tokens=30000), [])
            yield ev

    own_time.run_once(loop=L(), system="", dynamic="", now=datetime(2026, 10, 16, 9, 0, tzinfo=CST))
    assert seen == [("own_time", 30000)]
