"""手册给她看（Guide A3，2026-10-18：OS 的 Settings › Guide）。

和他 `guide_read` 读的是同一份（启动时载入的 topics），只读；审稿用的 `<!-- -->` 来源标注不给。
"""

from __future__ import annotations

import pytest

from guide import loader


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    from api.server import create_app
    from data.store import Store
    from tests.test_api import FakeNox

    nox = FakeNox()
    nox._guide_topics = loader.load_topics()
    return TestClient(create_app(nox, Store(tmp_path / "sessions.db"))), nox


def test_目录_每篇的id标题和什么时候翻(client):
    c, nox = client
    r = c.get("/api/nox/guide")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["id"] for i in items] == [t.id for t in nox._guide_topics]
    mem = next(i for i in items if i["id"] == "memory")
    assert mem["title"] == "记忆怎么运作" and "记不记得" in mem["ask"]
    assert "body" not in mem, "目录不带正文"


def test_正文_和他读的是同一份_来源标注不给(client):
    c, nox = client
    body = c.get("/api/nox/guide/memory").json()["body"]
    assert body == next(t.body for t in nox._guide_topics if t.id == "memory")
    assert "recall_memory" in body and "<!--" not in body


def test_没有这一篇_404(client):
    c, _ = client
    assert c.get("/api/nox/guide/no-such-topic").status_code == 404


def test_没载入手册_503不是空目录(client):
    """读不到 ≠ 没有：没载入要说，不能回一个空目录让页面显示「还没有手册」。"""
    c, nox = client
    nox._guide_topics = None
    assert c.get("/api/nox/guide").status_code == 503
