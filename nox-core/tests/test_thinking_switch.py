"""她自己选「思考」开还是关（2026-10-06）。

她：「你把思考做成个配置项，让我自己选择平常打开还是关闭。」
起因：10-05 试 DeepSeek 时关着思考，他 31 轮只调了 1 次工具、回复中位 47 token，她说「变蠢了」。

开关是**服务器上一份**（不像模型那样每台设备各记各的）：手机、OS、他主动开口都听它的。
  开 → 主聊天 depth=high；关 → low（和以前一样）
  🔴 打电话永远 low：她说完要立刻有回音，想几秒就是一段死寂
"""

from __future__ import annotations

import types
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent.llm import Message, Turn, Usage
from agent.loop import AgentLoop
from attention.store import AttentionStore
from nox import Nox
from tests.test_api_models import ModelFakeNox, _client


def _nox_with_store(tmp_path) -> Nox:
    n = Nox.__new__(Nox)
    n.state_store = AttentionStore(tmp_path / "attn.db")
    return n


def test_默认关_存了就开_重新读还在(tmp_path):
    n = _nox_with_store(tmp_path)
    assert n.thinking_on() is False
    assert n.set_thinking(True) is True
    assert n.thinking_on() is True
    n2 = Nox.__new__(Nox)
    n2.state_store = n.state_store
    assert n2.thinking_on() is True, "重启（新的 Nox）之后要还记得"


def test_状态库没起来_按关算_也不假装存上了():
    n = Nox.__new__(Nox)
    assert n.thinking_on() is False
    assert n.set_thinking(True) is False


def test_开着思考_聊天想得深_打电话照样不想(tmp_path):
    n = _nox_with_store(tmp_path)
    assert n._chat_depth(voice=False) == "low"
    n.set_thinking(True)
    assert n._chat_depth(voice=False) == "high"
    assert n._chat_depth(voice=True) == "low"


# ---------------------------------------------------------------- 一路传到模型那一层

class _Adapter:
    """记下每次请求带的 depth"""

    def __init__(self):
        self.depths = []

    def complete(self, messages, tools, **kw):
        self.depths.append(kw.get("depth"))
        return Turn(stop_reason="end_turn", text="嗯", tool_calls=[], usage=Usage())

    def stream(self, messages, tools, **kw):
        self.depths.append(kw.get("depth"))
        from agent.llm import StreamEvent
        yield StreamEvent("text", text="嗯")
        ev = StreamEvent("done")
        ev.turn = Turn(stop_reason="end_turn", text="嗯", tool_calls=[], usage=Usage())
        yield ev


def test_loop_收到的depth_原样交给模型():
    a = _Adapter()
    loop = AgentLoop(adapter=a)
    loop.run("在吗", depth="high")
    loop.run("在吗")
    assert a.depths == ["high", "low"], "不给就用默认 low"


def test_流式也一样():
    a = _Adapter()
    loop = AgentLoop(adapter=a)
    list(loop.run_stream("在吗", depth="high"))
    assert a.depths and a.depths[0] == "high"


def test_Nox两条路都带上开关(tmp_path):
    """chat()（他主动开口走这条）和 chat_stream()（她聊天走这条）都要听开关。"""
    n = _nox_with_store(tmp_path)
    n.set_thinking(True)
    seen = {}
    n._see = lambda t, i, m: (t, i)
    n._dynamic = lambda *a, **k: ""
    n._flush_dirty = lambda r: None
    n.adapter_for = lambda m: None
    n._system = ""

    def handle(*a, **k):
        seen["chat"] = k.get("depth")
        from agent.loop import LoopResult
        from router.intent import Decision, Intent
        from router.router import RouteResult
        return RouteResult(LoopResult(outcome="answered", text="嗯", iterations=1, usage=Usage(),
                                      messages=[Message(role="assistant", text="嗯")], attachments=[]),
                           Decision(Intent.FULL, "测试"))
    n.router = SimpleNamespace(handle=handle)

    def run_stream(*a, **k):
        seen["stream"] = k.get("depth")
        return iter([])
    n.loop = SimpleNamespace(run_stream=run_stream)

    n.chat("（系统提示：…）", [])
    list(n.chat_stream("在吗", []))
    assert seen == {"chat": "high", "stream": "high"}
    list(n.chat_stream("在吗", [], voice=True))
    assert seen["stream"] == "low", "电话不许想"


# ---------------------------------------------------------------- 接口

def _thinking_client(monkeypatch, tmp_path) -> TestClient:
    nox = ModelFakeNox()
    for name in ("thinking_on", "set_thinking", "_state_store"):
        setattr(nox, name, types.MethodType(getattr(Nox, name), nox))
    return _client(monkeypatch, tmp_path, nox)


def test_接口_拨开关_models页也看得到(monkeypatch, tmp_path):
    c = _thinking_client(monkeypatch, tmp_path)
    assert c.get("/api/nox/thinking").json() == {"ok": True, "on": False}
    assert c.post("/api/nox/thinking", json={"on": True}).json() == {"ok": True, "on": True}
    assert c.get("/api/nox/thinking").json()["on"] is True
    assert c.get("/api/nox/models").json()["thinking"] is True
    assert c.post("/api/nox/thinking", json={"on": False}).json()["on"] is False


def test_接口_存不上要说出来(monkeypatch, tmp_path):
    nox = ModelFakeNox()
    nox.thinking_on = lambda: False
    nox.set_thinking = lambda on: False          # 状态库写挂了
    c = _client(monkeypatch, tmp_path, nox)
    assert c.post("/api/nox/thinking", json={"on": True}).status_code == 503, "存不上还回 ok，她会以为改好了"


def test_router_把depth交给loop():
    """他主动开口走 chat() → router.handle → full_loop.run，中间这一跳漏了开关就白拨。"""
    from router.router import Router
    a = _Adapter()
    r = Router(full_loop=AgentLoop(adapter=a), system_prompt="人设")
    r.handle("（系统提示：不是她在跟你说话。现在是 15:00。想起她了，说一句）", [], depth="high")
    assert a.depths == ["high"]
