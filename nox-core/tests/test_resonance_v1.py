"""Resonance V1：对话事件进入 Attention。

见 `CAELUM-RESONANCE-ARCHITECTURE.md` 第七、八节。

## 这个文件管什么

**管道本身**：她说的话变成 `ExperienceEvent(source="chat")` 送进
`engine.handle()`，而且这条增强路径出任何事都不许影响对话主链。

  1. 接线通了：`/chat` 一轮结束后，事件带着原文和 sid 到了 engine
  2. 空文本不造事件
  3. engine 炸了对话照常

⚠️ **规则的行为不在这里测**，在 `test_resonance_v2.py`。
V2（2026-08-24）之后，「她说的话产生什么」是那边的事；
这里只保证「话送到了、送的过程不会把对话搞坏」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message, Usage  # noqa: E402
from agent.loop import LoopResult  # noqa: E402
from api.server import create_app  # noqa: E402
from attention.engine import AttentionEngine  # noqa: E402
from attention.events import ExperienceEvent  # noqa: E402
from attention.evaluator import AttentionEvaluator  # noqa: E402
from attention.registry import AttentionRegistry  # noqa: E402
from attention.relationship import RelationshipState  # noqa: E402
from data.store import Store  # noqa: E402
from router.intent import Decision, Intent  # noqa: E402
from router.router import RouteResult  # noqa: E402


# ------------------------------------------------------------ 单元：事件被忽略


def test_ordinary_talk_is_ignored():
    """普通的话不产生 Concern。

    ## 这条测试的历史

    V1 时它断言的是「**所有** chat 事件都被忽略」—— 那时候还没有规则，
    事件流过就完事。当时写着「这条红了说明有人给 chat 加了规则，
    如果是 V2 的有意改动就更新它」。

    2026-08-24 V2 落地，它红了，正是那个预期中的情况：
    「我今天其实有点难受」现在会产生 Concern（`test_resonance_v2.py`）。

    所以保证收窄成现在这条：**普通的话仍然什么都不产生**。
    她一天说几十上百句，绝大多数不该在他心里留下东西。
    """
    ev = ExperienceEvent(
        source="chat", type="message",
        payload={"text": "晚饭吃的火锅", "session_id": "s1"},
    )
    decision = AttentionEvaluator(RelationshipState()).evaluate(ev, AttentionRegistry())

    assert decision.action == "ignore"
    assert not decision.should_apply


def test_ordinary_talk_does_not_touch_registry(tmp_path):
    """普通的话流过 engine，Registry 一条都不多。"""
    from attention.store import AttentionStore

    engine = AttentionEngine.bootstrap(AttentionStore(tmp_path / "a.db"), None)
    before = len(engine.registry.list())

    engine.handle(ExperienceEvent(
        source="chat", type="message", payload={"text": "在吗", "session_id": "s1"},
    ))

    assert len(engine.registry.list()) == before


# ------------------------------------------------------------ 接线：真的送到了


class _Cfg:
    history_limit = 40
    recent_window_tokens = 8000
    context_budget_tokens = 20000

    class primary:  # noqa: N801
        model = "fake-model"

    class router:  # noqa: N801
        light_adapter = None

    def __init__(self, db_path):
        self.db_path = db_path


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
    """能聊天、也能把 Attention 装配起来的假 core。

    `test_api.py` 那个 FakeNox 没有 `context`，装不起 Attention
    （那是**故意的** —— 它测的是 API 层，不该被 Attention 拖下水）。
    这里补上装配需要的最小面。
    """

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

    def chat(self, text, history=None, images=None, voice=False, scene=None, model=None):
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
    """开着 Attention 起一个 app，并把送进 engine 的事件截下来。

    ⚠️ `db_path` 必须落在 tmp_path —— `_build_attention` 是按
    `Path(cfg.db_path).parent / "attention.db"` 找库的，
    传 `:memory:` 的话 parent 是 `.`，会在 nox-core 下面拉一坨 attention.db。
    """
    monkeypatch.setenv("NOX_ATTENTION", "1")
    monkeypatch.delenv("NOX_ATTENTION_LIVE", raising=False)

    events: list[ExperienceEvent] = []
    real = AttentionEngine.handle

    def spy(self, event, now=None):
        events.append(event)
        return real(self, event, now)

    monkeypatch.setattr(AttentionEngine, "handle", spy)

    store = Store(tmp_path / "sessions.db")
    client = TestClient(create_app(ChatNox(tmp_path / "sessions.db"), store))
    yield client, events
    store.close()


def test_chat_turn_produces_conversation_event(captured):
    """一轮对话结束 → 事件带着原文和 sid 到了 engine。"""
    client, events = captured
    r = client.post("/chat", json={"text": "我今天其实有点难受", "session_id": "s-1"})
    assert r.status_code == 200

    chat_events = [e for e in events if e.source == "chat"]
    assert len(chat_events) == 1
    ev = chat_events[0]
    assert ev.type == "message"
    assert ev.payload["text"] == "我今天其实有点难受"
    assert ev.payload["session_id"] == "s-1"
    #: sid 只用来事后追查，不参与判断（对齐 events.py 的约定）
    assert ev.origin_context == {"sid": "s-1"}


def test_empty_text_makes_no_event(captured):
    """纯图片消息不造空事件 —— 没有内容可 appraise 的事件是噪音。"""
    client, events = captured
    r = client.post("/chat", json={
        "text": "", "images": ["data:image/png;base64,iVBORw0KGgo="], "session_id": "s-2",
    })
    assert r.status_code == 200
    assert [e for e in events if e.source == "chat"] == []


def test_handle_failure_does_not_break_chat(captured, monkeypatch):
    """engine 炸了也绝不影响对话主链。

    这条是 `_turn_ends` 的既有风格（压缩失败、rebase 失败都一样）：
    **增强路径的锅不许让她收不到回复。**
    """
    client, _ = captured

    def boom(self, event, now=None):
        raise RuntimeError("故意炸的")

    monkeypatch.setattr(AttentionEngine, "handle", boom)

    r = client.post("/chat", json={"text": "在吗", "session_id": "s-3"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
