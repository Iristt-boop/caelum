"""记忆抽取 shadow 钩子（Phase 3 接线，2026-09-16）的测试。

## 钉什么

1. **测试会话不许碰抽取器** —— 测试流量会把 shadow 数据搅浑（R6 精神：
   这条线不写任何全局状态，但 shadow 日志也是数据）
2. **正常会话真的会调**，且对话片段两头都在（她说的 + 他回的）
3. **太短的回合跳过**（「嗯」「好」不值得一次 utility 调用）
4. 抽取器挂了不许影响对话（返回照常 200）

## 钉不了什么

- OB 侧的抽取质量（shadow 跑一周人工看的事）
- live 模式（OB 侧还没开，钩子永远 shadow）
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_test_session_isolation import ChatNox as _BaseChatNox  # noqa: E402


class _InlineThreading:
    """threading 的内联替身：Thread(target).start() 变成原地执行，
    后台代码就变成同步的，测试不用 sleep 等。"""

    def __getattr__(self, name):
        import threading as real
        return getattr(real, name)

    class Thread:
        def __init__(self, target=None, daemon=False, name=None):
            self._target = target

        def start(self):
            self._target()


class FakeOB:
    """记录调用的 OB 假身。"""

    def __init__(self):
        self.calls: list[dict] = []

    async def aextract_memory(self, dialog, today="", mode="shadow"):
        self.calls.append({"dialog": dialog, "mode": mode})
        return type("R", (), {"ok": True, "text": "[shadow] ok", "error": None})()


class BombOB:
    async def aextract_memory(self, dialog, today="", mode="shadow"):
        raise RuntimeError("OB 炸了")


class ChatNox(_BaseChatNox):
    """同 test_test_session_isolation 的最小假身，差异只有 ob。"""

    def __init__(self, db_path, ob):
        super().__init__(db_path)
        self.ob = ob


def _client(monkeypatch, tmp_path, ob):
    from data.store import Store
    from api import server as server_mod
    from api.server import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setattr(server_mod, "threading", _InlineThreading())
    nox = ChatNox(tmp_path / "sessions.db", ob)
    store = Store(tmp_path / "sessions.db")
    client = TestClient(create_app(nox, store))
    return client, store


def test_测试会话不碰抽取器(monkeypatch, tmp_path):
    """测试流量会把 shadow 数据搅浑 —— 闸门拦在调 OB 之前。"""
    ob = FakeOB()
    client, store = _client(monkeypatch, tmp_path, ob)
    r = client.post("/chat", json={
        "text": "测试会话的一条足够长的对话内容，超过三十个字的门槛",
        "session_id": "test-shadow",
    })
    assert r.status_code == 200
    assert ob.calls == [], "测试会话也触发了 shadow 抽取"
    store.close()


def test_正常会话会调_且两头都在(monkeypatch, tmp_path):
    ob = FakeOB()
    client, store = _client(monkeypatch, tmp_path, ob)
    r = client.post("/chat", json={
        "text": "今天学西语学到虚拟式过去未完成时，头都大了，你陪我聊聊",
        "session_id": "s-real",
    })
    assert r.status_code == 200
    assert len(ob.calls) == 1, f"该调一次，实际 {len(ob.calls)}"
    dialog = ob.calls[0]["dialog"]
    assert "糖糖：" in dialog and "西语" in dialog, "她说的没进对话片段"
    assert "Nox：" in dialog, "他回的没进对话片段"
    store.close()


def test_太短的回合跳过(monkeypatch, tmp_path):
    ob = FakeOB()
    client, store = _client(monkeypatch, tmp_path, ob)
    r = client.post("/chat", json={"text": "嗯", "session_id": "s-real"})
    assert r.status_code == 200
    assert ob.calls == [], "「嗯」这种回合不值得一次抽取调用"
    store.close()


def test_抽取器挂了_对话照常(monkeypatch, tmp_path):
    client, store = _client(monkeypatch, tmp_path, BombOB())
    r = client.post("/chat", json={
        "text": "这段话足够长，会触发 shadow 抽取的调用路径",
        "session_id": "s-real",
    })
    assert r.status_code == 200, "shadow 挂了不该影响对话"
    store.close()
