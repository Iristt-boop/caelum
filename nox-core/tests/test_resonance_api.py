"""心跳线要的那两个出口。

## 🔴 这个文件盯的是「数据有没有真的出得来」

糖糖 2026-08-29 要做 Attention 心跳线，一查发现两件事：

```text
/api/nox/state   snapshot 里有 strength / since，出门时被扔了，只剩 subject
Resonance        从 08-24 就在跑，整个路由表里没有它
```

于是前端只能把等级写死成「中」—— 那五个「中」不是测出来的，是贴上去的。
界面看着像便签，根因在接口。

所以这里测的不是「函数返回了什么」，是**真起一个 app、真跑一轮对话、
再从 HTTP 响应里把数字读出来**。读源码的测试抓不到「字段在中间层被丢了」。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.server import create_app  # noqa: E402
from data.store import Store  # noqa: E402
from tests.test_api import FakeNox  # noqa: E402
from tests.test_api_world import _Ctx, _Loop  # noqa: E402


@pytest.fixture
def live(monkeypatch, tmp_path):
    """一个 attention 真的开着的 app。"""
    monkeypatch.setenv("NOX_ATTENTION", "1")
    nox = FakeNox()
    nox.cfg.db_path = str(tmp_path / "nox.db")
    nox.context = _Ctx()
    nox.bridge = None
    nox.current_session_id = None
    nox.loop = _Loop()
    nox.router = type("R", (), {"light_adapter": None})()
    app = create_app(nox, Store(tmp_path / "sessions.db"))
    return TestClient(app)


def _say(c: TestClient, text: str) -> None:
    r = c.post("/chat", json={"session_id": "s-heartbeat", "text": text})
    assert r.status_code == 200, r.text


class Test强度出得来:
    """🔴 `strength` 原来在 server.py 里被那句列表推导扔掉了。"""

    def test_关心的事带着强度出来(self, live):
        _say(live, "我今天好累啊")

        d = live.get("/api/nox/state").json()
        assert d["ok"] is True
        items = d["attention"]["attentions"]
        assert items, "他记挂着事，但接口一条都没吐出来"

        a = items[0]
        #: 这三个字段就是心跳线的原料
        assert isinstance(a["strength"], (int, float))
        assert 0 < a["strength"] <= 1
        assert a["since"]
        assert a["kind"]

    def test_强度是真数字_不是写死的档位(self, live):
        """前端那五个「中」就是因为这里没有数才写死的。"""
        _say(live, "我今天好累啊")
        items = live.get("/api/nox/state").json()["attention"]["attentions"]
        assert items[0]["strength"] not in (0, 1), "看着像个占位值"

    def test_cares_还是字符串数组_没被改坏(self, live):
        """🔴 手机端 `NoxStatus.jsx:38` 直接读 `attn.cares`。

        把它改成对象数组的话，糖糖手机上那一栏会当场空掉 ——
        而这边的测试全绿。所以这条专门钉住旧形状。
        """
        _say(live, "我今天好累啊")
        cares = live.get("/api/nox/state").json()["attention"]["cares"]
        assert cares and all(isinstance(x, str) for x in cares)

    def test_两个字段说的是同一批事(self, live):
        _say(live, "我今天好累啊")
        attn = live.get("/api/nox/state").json()["attention"]
        assert [a["subject"] for a in attn["attentions"]] == attn["cares"]


class Test内心状态有出口了:
    def test_接口活着(self, live):
        d = live.get("/api/nox/resonance").json()
        assert d["ok"] is True
        assert d["enabled"] is True
        assert "now" in d

    def test_她说累之后_他身上压着一件事(self, live):
        _say(live, "我今天好累啊")
        drives = live.get("/api/nox/resonance").json()["drives"]
        assert "concern" in drives, f"什么都没压着？{drives}"

        c = drives["concern"]
        assert c["load"] > 0
        assert c["because"], "说不出为什么的 Drive 不算数"
        assert c["source_count"] >= 1

    def test_load_和_intensity_都给_画图要用_load(self, live):
        """🔴 `intensity` 会饱和 —— 一件事和三件事都贴着 1，图上看不出差别。

        `load` 不饱和，区分度在它身上。两个都吐出去，
        并且在接口文档里写清楚该用哪个。
        """
        _say(live, "我今天好累啊")
        c = live.get("/api/nox/resonance").json()["drives"]["concern"]
        assert "load" in c and "intensity" in c

    def test_没有的_Drive_干脆不出现(self, live):
        """🔴 「他现在不低落」该表现成**没有这一项**，不是 `dejection: 0.00`。

        挂一个 0.00 在这儿，前端画出来就是「低落：0.00」——
        读起来像他有一点点低落。
        """
        drives = live.get("/api/nox/resonance").json()["drives"]
        for name, d in drives.items():
            assert d["load"] > 0, f"{name} 挂了个 0 在这儿"

    def test_没启用时如实说没启用(self, tmp_path):
        """不返回空 drives 假装「他此刻很平静」—— 那两件事不一样。"""
        c = TestClient(create_app(FakeNox(), Store(tmp_path / "s.db")))
        d = c.get("/api/nox/resonance").json()
        assert d["ok"] is True
        assert d["enabled"] is False
        assert d["drives"] == {}


class Test前端拿得到:
    """🔴 bridge 是**逐路由代理**，不是通配。

    Core 加了接口、bridge 不加，前端拿到的是 bridge 自己的 404 ——
    而那看起来像「Core 挂了」，不像「少写了一行」。
    这条直接去读 bridge 的源码，省得下次又漏。
    """

    def test_bridge_转了这条路由(self):
        src = (Path(__file__).resolve().parents[2] / "bridge" / "server.js")
        if not src.exists():
            pytest.skip("bridge 不在这台机器上")
        text = src.read_text(encoding="utf-8")
        assert '"/api/nox/resonance"' in text, "bridge 没转这条，前端会 404"
