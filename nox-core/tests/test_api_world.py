"""World Model 的两个 HTTP 接口 —— **真走 HTTP**。

## 为什么必须走 TestClient

2026-08-19：`/api/nox/facts` 和 `/api/nox/record/period` 写完，
**本地 804 个测试全绿**，一上线两个都坏：

    GET  → 500  NameError: name 'world' is not defined
    POST → 422  loc: ["query","req"] Field required

两个都只在 HTTP 这一层才现形：

1. `world` 是 `_build_attention` 的**局部变量**，路由函数看不见它。
   直接调 `WorldModel` 的单元测试当然全过 —— 它们根本没经过路由
2. `PeriodRequest` 定义在路由函数内部，配上 `from __future__ import
   annotations`，FastAPI 拿到的注解是字符串，在模块命名空间里解析不到，
   于是退回「当成 query 参数」，请求体永远收不到

这和 2026-08-08 那次是同一类事故（见 `test_attention_wiring.py` 开头）：
**只在线上才活的分支，本地测不出来。** 所以这个文件只做一件事 ——
把接口当外人一样打一遍。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from api.server import create_app  # noqa: E402
from data.store import Store  # noqa: E402
from tests.test_api import FakeNox  # noqa: E402


class _Provider:
    def get_state(self, turn=None, force_refresh=False):
        return {"has_data": False}


class _Ctx:
    def get(self, name):
        return _Provider() if name == "health" else None


class _Loop:
    """`FakeNox` 的 loop 只有 `tools`，没有 `register` ——
    而 `_build_attention` 会往它上面注册 record_period / remind_myself。"""

    tools = {"recall_memory": None}

    def __init__(self):
        self.registered = []

    def register(self, spec, handler):
        self.registered.append(spec.name)


def _client(monkeypatch, tmp_path) -> TestClient:
    """逼 Attention 那段「只在线上活」的分支在本地也跑起来。

    ⚠️ `FakeNox` 没有 `context`，`_build_attention` 会在
    `core.context.get("health")` 那行失败 → attention 是 None →
    World Model 也就不存在，接口全回 503。
    补一个最小的 context 才能真正测到那两个接口。
    """
    monkeypatch.setenv("NOX_ATTENTION", "1")
    nox = FakeNox()
    nox.cfg.db_path = str(tmp_path / "nox.db")
    nox.context = _Ctx()
    nox.bridge = None
    nox.current_session_id = None
    nox.loop = _Loop()
    nox.router = type("R", (), {"light_adapter": None})()
    return TestClient(create_app(nox, Store(tmp_path / "sessions.db")))


def test_读事实不许500(monkeypatch, tmp_path):
    """`world` 拿错作用域的话，这里就是 500。"""
    c = _client(monkeypatch, tmp_path)
    r = c.get("/api/nox/facts?type=menstrual&days=180")
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []      # 新库，空的


def test_记生理期走的是请求体(monkeypatch, tmp_path):
    """Pydantic 模型定义在函数内部的话，这里是 422（当成 query 参数）。"""
    c = _client(monkeypatch, tmp_path)
    r = c.post("/api/nox/record/period", json={"event": "start", "flow": "量少"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_记完读得回来(monkeypatch, tmp_path):
    """写和读是同一个 World Model —— 端到端串一遍。"""
    c = _client(monkeypatch, tmp_path)
    c.post("/api/nox/record/period", json={"event": "start", "date": "2026-08-14"})
    items = c.get("/api/nox/facts?type=menstrual&days=365").json()["items"]
    assert len(items) == 1
    assert items[0]["raw"]["event"] == "start"
    assert items[0]["raw"]["date"] == "2026-08-14"


def test_认不出的event被拒(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    r = c.post("/api/nox/record/period", json={"event": "maybe"})
    assert r.status_code == 422


def test_缺event被拒(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    assert c.post("/api/nox/record/period", json={}).status_code == 422
