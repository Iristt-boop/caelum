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


class Test脉搏:
    """🔴 心跳线上的尖峰。

    `CareLedger.events` 从 2026-08-18 就在逐条记，但一直只有汇总出得去，
    明细一条没暴露过 —— 所以界面上他"惦记了一下"这件事从来没被看见过。
    """

    @staticmethod
    def _ledger(app):
        from tests.test_dejection import _find_attention
        a = _find_attention(app)
        assert a is not None
        return a.ledger

    def test_接口活着_没事件时也给游标(self, live):
        """🔴 一条都没有时也必须给 `seq` —— 不然客户端永远从 0 开始要。"""
        d = live.get("/api/nox/pulse").json()
        assert d["ok"] is True
        assert "seq" in d
        assert isinstance(d["events"], list)

    def test_记一笔就能读到(self, live):
        lg = self._ledger(live.app)
        lg.record(source="sleep", decision="speak", reason="")

        d = live.get("/api/nox/pulse").json()
        assert d["events"], "记了一笔却读不到"
        e = d["events"][-1]
        assert e["source"] == "sleep"
        assert e["decision"] == "speak"
        #: 画时间轴要的两样
        assert e["seq"] >= 1
        assert e["at"], "只有 HH:MM:SS 的话跨零点就没法排序了"

    def test_since_只给新的(self, live):
        lg = self._ledger(live.app)
        lg.record(source="sleep", decision="speak")
        first = live.get("/api/nox/pulse").json()
        cursor = first["seq"]

        assert live.get(f"/api/nox/pulse?since={cursor}").json()["events"] == []

        lg.record(source="time", decision="block", reason="今天说太多了")
        again = live.get(f"/api/nox/pulse?since={cursor}").json()
        assert len(again["events"]) == 1
        assert again["events"][0]["source"] == "time"
        assert again["events"][0]["reason"] == "今天说太多了"

    def test_换天不许把游标归零(self, live):
        """归零的话客户端过零点会以为时间倒流 ——
        要么重放一整天，要么再也收不到新的。"""
        lg = self._ledger(live.app)
        lg.record(source="sleep", decision="speak")
        before = lg.seq
        assert before >= 1

        #: 强行换天
        lg.date = "1999-01-01"
        lg.record(source="time", decision="skip")

        assert lg.events, "换天之后应该有新的一条"
        assert lg.seq > before, "游标归零了 —— 客户端会以为时间倒流"
        assert lg.events[-1]["seq"] > before

    def test_旧账本读回来时按数组顺序重排(self, live):
        """线上已经攒着没有 seq 的老事件。

        🔴 **只补缺的那几条会把顺序弄反** —— `[无号(09:00), 7号(10:00)]`
        里那条无号的会拿到 8，先发生的排到了后面，时间轴当场错乱。
        所以只要有一条缺号，就整批按数组顺序重排（数组顺序才是真相）。
        """
        from attention.care.ledger import CareLedger
        lg = CareLedger.from_dict({
            "date": "2026-08-29",
            "events": [{"time": "09:00:00", "source": "sleep", "decision": "speak"},
                       {"seq": 7, "time": "10:00:00", "source": "time", "decision": "skip"}],
        })
        assert [e["seq"] for e in lg.events] == [1, 2], "号要跟着时间走"
        assert lg.seq == 2
        lg.record(source="wake", decision="speak")
        assert lg.events[-1]["seq"] == 3

    def test_游标自己落库_换天重启也不归零(self, live):
        """🔴 只从事件推的话有个洞：换天清空 events，那一刻重启就归零，
        新事件的号比昨天还小 —— 客户端拿着旧游标再也收不到东西，**而且不报错**。
        """
        from attention.care.ledger import CareLedger
        lg = CareLedger.from_dict({"date": "2026-08-29", "events": [], "seq": 57})
        assert lg.seq == 57
        lg.record(source="sleep", decision="speak")
        assert lg.events[0]["seq"] == 58

        #: 存了再读回来，号不能退
        back = CareLedger.from_dict(lg.to_dict())
        assert back.seq == 58

    def test_没启用时如实说(self, tmp_path):
        c = TestClient(create_app(FakeNox(), Store(tmp_path / "s.db")))
        d = c.get("/api/nox/pulse").json()
        assert d["ok"] is True and d["enabled"] is False
        assert d["events"] == []


class Test脉搏是推的:
    """🔴 轮询和推的区别，就是便签和心跳的区别。"""

    def test_流真的会把新事件推出来(self, live):
        """🔴 驱动的是**真的那段生成器**，不是它的复制品。

        ⚠️ 不用 `TestClient.stream()` —— 它的 portal 碰上永不结束的流会互锁，
        2026-08-29 那次测试跑了 180 秒没回来。所以时钟和轮次都注入进去。
        """
        import asyncio
        import json as _json
        from api.server import pulse_stream

        lg = Test脉搏._ledger(live.app)
        seen = []

        async def fake_sleep(_s):
            #: 第二拍之前记一笔 —— 验的是「连着的时候有新东西会自己过来」，
            #: 不是「连上那一刻的快照」
            if not seen:
                lg.record(source='sleep', decision='speak', reason='她没睡好')
                seen.append(1)

        async def run():
            out = []
            gen = pulse_stream(
                lambda since: [e for e in lg.events if int(e.get('seq') or 0) > since],
                lambda: lg.seq,
                enabled=True, since=lg.seq, sleep=fake_sleep, max_ticks=3,
            )
            async for frame in gen:
                out.append(frame)
            return out

        frames = asyncio.run(run())
        msgs = [_json.loads(f[6:]) for f in frames if f.startswith('data: ')]

        assert msgs[0]['type'] == 'hello', '先要告诉客户端它站在哪个游标上'
        pulses = [m for m in msgs if m['type'] == 'pulse']
        assert pulses, '连着的时候记了一笔，但流里什么都没来'
        assert pulses[0]['event']['source'] == 'sleep'
        assert pulses[0]['event']['reason'] == '她没睡好'

    def test_闲着的时候发心跳注释(self):
        """🔴 不发的话中间代理会掐掉这条闲连接，
        而前端看到的是「他的心跳突然停了」。"""
        import asyncio
        from api.server import PULSE_PING_TICKS, pulse_stream

        async def run():
            out = []
            async for f in pulse_stream(lambda _s: [], lambda: 0, enabled=True,
                                        since=0, sleep=lambda _s: asyncio.sleep(0),
                                        max_ticks=PULSE_PING_TICKS + 1):
                out.append(f)
            return out

        assert any(f.startswith(': ping') for f in asyncio.run(run()))
    def test_bridge_把这两条也转了(self):
        """🔴 bridge 逐路由代理。流那条尤其容易漏 ——
        漏了的话前端拿到 404，看起来像「他没心跳」。"""
        src = Path(__file__).resolve().parents[2] / "bridge" / "server.js"
        if not src.exists():
            pytest.skip("bridge 不在这台机器上")
        text = src.read_text(encoding="utf-8")
        assert '"/api/nox/pulse"' in text
        assert '"/api/nox/pulse/stream"' in text
        #: 流不能用 res.json()，那会把一条不结束的流当成一次性响应
        i = text.index('"/api/nox/pulse/stream"')
        assert "X-Accel-Buffering" in text[i:i + 1600], \
            "少了它，Caddy 会把流攒成一坨再发，心跳会一顿一顿的"


class Test上线那天不能是空的:
    """🔴 线上实测抓到的：部署当天心跳线一整天全空。

    `seq` 是 2026-08-29 才加的字段，而账本里已经攒着当天的事件 ——
    它们没有这个字段，被 `seq or 0` 判成 0，而脉搏要的是 `seq > since`。

    表现：账本里明明 13 条（说了 2 次、憋回去 11 次），
    `/api/nox/pulse` 返回 `events: []`。**接口没报错，就是空的。**
    """

    def test_老事件被补上游标_看得见(self):
        from attention.care.ledger import CareLedger
        lg = CareLedger.from_dict({
            "date": "2026-08-30",
            "events": [
                {"time": "00:36:30", "source": "random", "decision": "speak"},
                {"time": "01:23:37", "source": "random", "decision": "skip",
                 "reason": "没什么具体的可说"},
            ],
        })
        assert all(e.get("seq") for e in lg.events), "老事件没补 seq —— 心跳线会是空的"
        assert [e["seq"] for e in lg.events] == [1, 2], "补的顺序要和发生顺序一致"
        assert lg.seq == 2

    def test_老事件也补上完整时刻(self):
        """只有 `HH:MM:SS` 画不了时间轴。账本自己知道是哪天。"""
        from attention.care.ledger import CareLedger
        lg = CareLedger.from_dict({
            "date": "2026-08-30",
            "events": [{"time": "00:36:30", "source": "random", "decision": "speak"}],
        })
        assert lg.events[0]["at"].startswith("2026-08-30T00:36:30")

    def test_时间坏了就不补_绝不编一个(self):
        """时间轴上一个假的点，比缺一个点糟。"""
        from attention.care.ledger import CareLedger
        lg = CareLedger.from_dict({
            "date": "2026-08-30",
            "events": [{"time": "不是时间", "source": "random", "decision": "speak"}],
        })
        assert "at" not in lg.events[0]
        assert lg.events[0]["seq"] == 1, "时间坏了不影响它进流"

    def test_已经有_seq_的不动它(self):
        from attention.care.ledger import CareLedger
        lg = CareLedger.from_dict({
            "date": "2026-08-30",
            "events": [{"seq": 5, "at": "x", "time": "09:00:00",
                        "source": "time", "decision": "skip"}],
        })
        assert lg.events[0]["seq"] == 5
        assert lg.events[0]["at"] == "x"
        assert lg.seq == 5
