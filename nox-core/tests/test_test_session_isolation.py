"""测试会话隔离：test-/sandbox- 流量不许碰任何全局注意力状态。

2026-08-24 事故：test- 会话聊了一句「糖糖说的心情 0.71」进了生产
Registry——他可能据此主动问她根本没说过的事。2026-09-05 补的闸门。

## 这个文件管什么

  1. `config.is_test_session` 的判定（默认前缀 + 环境变量覆盖）
  2. 测试会话的一轮对话 **不产生** ConversationEvent（正常会话照旧）
  3. `remind_myself` 在测试会话里拒留纸条——纸条是全局的，到点会真的醒

⚠️ **压缩不在拦截范围**：那是会话自己的数据，压了不碍事。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import is_test_session, test_sid_prefixes  # noqa: E402
from tools import remind as remind_tools  # noqa: E402


# ------------------------------------------------------------ 单元：判定


def test_default_prefixes():
    assert is_test_session("test-abc-123")
    assert is_test_session("sandbox-1")
    assert not is_test_session("s-1")
    assert not is_test_session("e0f1c2a4-5b6d-4e7f-8a9b-c0d1e2f3a4b5")
    assert not is_test_session("")
    assert not is_test_session(None)


def test_prefix_env_override(monkeypatch):
    monkeypatch.setenv("NOX_TEST_SID_PREFIXES", "dry-, rehearsal-")
    assert is_test_session("dry-run-1")
    assert is_test_session("rehearsal-2")
    assert not is_test_session("test-abc")  # 覆盖是替换不是追加
    monkeypatch.setenv("NOX_TEST_SID_PREFIXES", " , ")
    assert test_sid_prefixes() == ("test-", "sandbox-")  # 空值回落默认


# ------------------------------------------------------------ 集成：turn_ends 闸门
# 复用 test_resonance_v1.py 的最小假 core（ChatNox 那套 harness 的复制版，
# 因为那边的 fixture 不是公共的；这里只需要「Attention 开着、能截事件」）。


class _Cfg:
    history_limit = 40
    recent_window_tokens = 8000
    context_budget_tokens = 20000

    class primary:  # noqa: N801
        model = "fake-model"

    class utility:
        usable = False
        model = ""
        base_url = ""

    class router:  # noqa: N801
        light_adapter = None

    def __init__(self, db_path):
        self.db_path = str(db_path)


class _Loop:
    tools = {"recall_memory": None}

    def __init__(self):
        self.registered = []

    def register(self, spec, handler):
        self.registered.append(spec.name)


class _Provider:
    def get_state(self, turn=None, force_refresh=False):
        return {"has_data": False}


class ChatNox:
    def __init__(self, db_path):
        self.cfg = _Cfg(db_path)
        self.loop = _Loop()
        self.bridge = None
        self.system_prompt = "（前缀）"
        self.current_session_id = None
        self.context = type("C", (), {
            "get": staticmethod(lambda name: _Provider() if name == "health" else None)
        })()
        self.router = type("R", (), {"light_adapter": None})()

    def model_name(self, model=None):
        return model or "fake-model"

    def chat(self, text, history=None, images=None, voice=False, scene=None, model=None, **kw):
        from agent.llm import Message, Usage
        from agent.loop import LoopResult
        from router.intent import Decision, Intent
        from router.router import RouteResult

        history = list(history or [])
        return RouteResult(
            LoopResult(
                outcome="answered", text="好的", iterations=1,
                usage=Usage(input_tokens=10, output_tokens=3, cache_read_tokens=0),
                messages=[*history, Message(role="user", text=text),
                          Message(role="assistant", text="好的")],
                attachments=[],
            ),
            Decision(Intent.FULL, "测试"),
        )


@pytest.fixture
def captured(monkeypatch, tmp_path):
    """开着 Attention 起一个 app，把送进 engine 的事件截下来。

    ⚠️ `db_path` 必须落在 tmp_path（同 test_resonance_v1 的教训：
    `:memory:` 的 parent 是 `.`，会在 nox-core 下面拉一坨 attention.db）。
    """
    from fastapi.testclient import TestClient
    from attention.engine import AttentionEngine
    from attention.events import ExperienceEvent
    from api.server import create_app
    from data.store import Store

    monkeypatch.setenv("NOX_ATTENTION", "1")
    monkeypatch.delenv("NOX_ATTENTION_LIVE", raising=False)

    events: list = []
    real = AttentionEngine.handle

    def spy(self, event, now=None):
        events.append(event)
        return real(self, event, now)

    monkeypatch.setattr(AttentionEngine, "handle", spy)

    store = Store(tmp_path / "sessions.db")
    client = TestClient(create_app(ChatNox(tmp_path / "sessions.db"), store))
    yield client, events
    store.close()


def test_test_session_makes_no_conversation_event(captured):
    """测试会话聊了天，Registry 一根事件都不该收到。"""
    client, events = captured
    for sid in ("test-incident-0824", "sandbox-1"):
        r = client.post("/chat", json={"text": "我今天其实有点难受", "session_id": sid})
        assert r.status_code == 200
    assert [e for e in events if e.source == "chat"] == []


def test_real_session_still_produces_event(captured):
    """对照：正常会话的事件照旧——闸门不许误伤。"""
    client, events = captured
    r = client.post("/chat", json={"text": "普通的话", "session_id": "s-real"})
    assert r.status_code == 200
    chat_events = [e for e in events if e.source == "chat"]
    assert len(chat_events) == 1
    assert chat_events[0].payload["session_id"] == "s-real"


# ------------------------------------------------------------ 理解层也在闸门内
#
# 🔴 理解层（2026-09-05）会写 Registry，所以它必须在同一道闸门之内（R6）。
# 它比 ConversationEvent 更容易漏：那条是同步的、就在闸门下面几行；
# 这条在**后台线程**里，看代码时很容易以为"反正是异步的，不算副作用"。


class _FakeAdapter:
    """假的便宜模型：永远判出一条「毕设」。"""

    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools, **kw):
        import json
        from agent.llm import Turn, Usage
        self.calls += 1
        return Turn(stop_reason="end_turn", usage=Usage(), text=json.dumps({
            "valence": "distress", "anchor": "毕设", "topic": "工作",
            "literal": "她说不想做了", "meaning": "觉得继续投入没有意义",
            "intensity": 0.6, "confidence": 0.9,
        }, ensure_ascii=False))


class _Inline:
    """把后台线程改成当场跑完，让断言不依赖时序。

    ⚠️ 只替换 `api.server` 模块里的 `threading` —— `_maybe_compact_async`
    是函数内 `import threading`，拿到的还是真的那个，不受影响。
    """

    class Thread:
        def __init__(self, target=None, daemon=False, **kw):
            self.target = target

        def start(self):
            self.target()


@pytest.fixture
def appraising(monkeypatch, tmp_path):
    """理解层开到 on + 配好便宜模型，剩下的只看闸门拦不拦。"""
    from fastapi.testclient import TestClient
    from attention.engine import AttentionEngine
    from api import server as server_mod
    from api.server import create_app
    from data.store import Store

    monkeypatch.setenv("NOX_ATTENTION", "1")
    monkeypatch.setenv("NOX_LLM_APPRAISAL", "on")
    monkeypatch.delenv("NOX_ATTENTION_LIVE", raising=False)
    monkeypatch.setattr(server_mod, "threading", _Inline)

    events: list = []
    real = AttentionEngine.handle

    def spy(self, event, now=None):
        events.append(event)
        return real(self, event, now)

    monkeypatch.setattr(AttentionEngine, "handle", spy)

    adapter = _FakeAdapter()
    nox = ChatNox(tmp_path / "sessions.db")
    nox.router = type("R", (), {"light_adapter": adapter})()

    store = Store(tmp_path / "sessions.db")
    client = TestClient(create_app(nox, store))
    yield client, events, adapter
    store.close()


def test_test_session_never_reaches_the_appraiser(appraising):
    """🔴 闸门要拦在**调模型之前**，不是拦在写库之前。

    拦在写库之前的话，跑一遍测试会话就白花一次 LLM 的钱，
    而且日志里会出现一条根本不该存在的理解。
    """
    client, events, adapter = appraising
    r = client.post("/chat", json={"text": "我不想干了", "session_id": "test-1"})
    assert r.status_code == 200
    assert adapter.calls == 0, "测试会话不该走到便宜模型"
    assert [e for e in events if e.type == "appraisal"] == []


def test_real_session_does_reach_the_appraiser(appraising):
    """对照：正常会话该判还是判——闸门不许误伤。"""
    client, events, adapter = appraising
    r = client.post("/chat", json={"text": "我不想干了", "session_id": "s-real"})
    assert r.status_code == 200
    assert adapter.calls == 1
    ap = [e for e in events if e.type == "appraisal"]
    assert len(ap) == 1
    assert ap[0].payload["appraisal"]["anchor"] == "毕设"


def test_shadow_mode_writes_nothing(appraising, monkeypatch):
    """影子模式：判得出来，但**一个字都不进 Registry**。

    这是转正之前唯一的观察手段——它要是偷偷写了库，
    "先看一周日志再决定"就成了空话。
    """
    client, events, adapter = appraising
    monkeypatch.setenv("NOX_LLM_APPRAISAL", "shadow")
    r = client.post("/chat", json={"text": "我不想干了", "session_id": "s-real"})
    assert r.status_code == 200
    assert adapter.calls == 1, "影子模式要真的跑，否则观察不到东西"
    assert [e for e in events if e.type == "appraisal"] == []


def test_off_means_the_model_is_never_called(appraising, monkeypatch):
    """默认关的时候不该有任何额外开销。"""
    client, events, adapter = appraising
    monkeypatch.setenv("NOX_LLM_APPRAISAL", "off")
    client.post("/chat", json={"text": "我不想干了", "session_id": "s-real"})
    assert adapter.calls == 0


# ------------------------------------------------------------ remind_myself 拒纸条


class _Spec:
    def __init__(self, name):
        self.name = name


class _HandlerLoop:
    def __init__(self):
        self.handlers = {}

    def register(self, spec, handler):
        self.handlers[spec.name] = handler


def test_remind_refuses_test_session():
    """纸条是全局的，到点会真的醒来推她——测试会话不许留。"""
    loop = _HandlerLoop()
    class _Book:  # 走到 add 就算失败——闸门该在它之前拦住
        def add(self, *a, **k):
            raise AssertionError("测试会话的纸条不该进 WakeBook")

    remind_tools.register_all(
        loop,
        book_ref=lambda: _Book(),
        save=lambda book: None,
        session_id_ref=lambda: "test-1",
    )
    handler = loop.handlers["remind_myself"]
    out = handler({"after_minutes": 30, "why": "她去吃饭了"})
    assert "测试会话不留纸条" in out


def test_remind_still_needs_session():
    """原有行为不回归：拿不到 sid 照样拒。"""
    loop = _HandlerLoop()
    class _Book:
        def add(self, *a, **k):
            raise AssertionError("没有 sid 也不该走到 add")

    remind_tools.register_all(
        loop,
        book_ref=lambda: _Book(),
        save=lambda book: None,
        session_id_ref=lambda: None,
    )
    out = loop.handlers["remind_myself"]({"after_minutes": 30, "why": "她去吃饭了"})
    assert "没留成" in out
