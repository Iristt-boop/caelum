"""躁动 —— 他有话想说，而她正忙。

## 🔴 这个 Drive 最容易静默失效

它**没有自己的存量**：憋着的话在 IntentBook 里、她在忙什么在感知层里，
每次都要 `refresh_resonance()` 现算一遍。

漏掉那一句的后果是：躁动**永远是 0，而且不报错** ——
接口照常返回、别的 Drive 照常有值，只是少了一个。
没人会发现，因为「他现在不躁动」看起来完全合理。

所以这个文件里最要紧的是最后那组：**真起 app、真跑一轮 HTTP、
再从响应里把躁动读出来**。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from attention.intent import GENERATE_THRESHOLD  # noqa: E402
from attention.restlessness import (  # noqa: E402
    FOCUS_AFTER_S,
    MAX,
    RestlessnessState,
    busy_of,
)

NOW = datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc)
LONG = FOCUS_AFTER_S * 2  # 已经进入状态


class Test它是乘积不是加和:
    """🔴 任何一边是 0，躁动就该是 0。"""

    def test_他没话想说_她再忙也不躁动(self):
        st = RestlessnessState()
        assert st.value(want=0.0, app="三角洲", seconds=LONG, now=NOW) == 0

    def test_她闲着_他有话直接说就行(self):
        """不知道她在干嘛 = 不忙。他直接说，没什么可憋的。"""
        st = RestlessnessState()
        assert st.value(want=1.0, app=None, seconds=0, now=NOW) == 0

    def test_两边都有才躁动(self):
        st = RestlessnessState()
        assert st.value(want=0.8, app="三角洲", seconds=LONG, now=NOW) > 0

    def test_加和做不到这件事(self):
        """她打了三小时游戏、他一句话都没有 —— 加和会给出一个不小的数，
        而那个数没有任何意义。"""
        st = RestlessnessState()
        assert st.value(want=0.0, app="三角洲", seconds=99999, now=NOW) == 0


class Test出口是等不是说:
    def test_上限低于开口阈值(self):
        """🔴 躁动本来就是"想说但这会儿不该说"，
        让它变成一次主动开口等于取消了它自己。"""
        assert MAX < GENERATE_THRESHOLD

    def test_怎么憋都不超过上限(self):
        st = RestlessnessState()
        st.waiting_since = NOW - timedelta(days=3)
        v = st.value(want=1.0, app="三角洲", seconds=99999, now=NOW)
        assert v <= MAX


class Test档位是她定的:
    """⚠️ 这张表是糖糖 2026-08-30 定的，不是猜的 ——
    「打断她的代价有多大」只有她知道。改之前先问她。"""

    def test_游戏最高(self):
        assert busy_of("三角洲行动", LONG) == 1.0

    def test_剪辑写稿次之(self):
        assert busy_of("Adobe Premiere Pro", LONG) == 0.8

    def test_看片再次(self):
        assert busy_of("哔哩哔哩", LONG) == 0.6

    def test_刷手机最低(self):
        """本来就在碎片切换，打断成本低。"""
        assert busy_of("微信", LONG) == 0.25

    def test_档位是有序的(self):
        order = [busy_of(a, LONG) for a in ("三角洲", "premiere", "bilibili", "微信")]
        assert order == sorted(order, reverse=True)

    def test_不认识的应用给低的(self):
        """宁可漏判她在忙，也不要凭一个不认识的窗口名让他躁动。"""
        assert 0 < busy_of("SomeRandomApp.exe", LONG) <= 0.35


class Test不知道就是不忙:
    """🔴 反过来的代价是他凭空躁动一整天，**而且没人会发现** ——
    「她在忙」这个假设看起来完全合理。

    app-tracker 停了 28 天没人发现，是同一类错误的另一面。
    """

    def test_传感器没数据(self):
        assert busy_of(None, LONG) == 0.0

    def test_空字符串(self):
        assert busy_of("", LONG) == 0.0

    def test_挂机(self):
        """感知层把 idle 转成 None 再进来，这里再兜一层。"""
        assert busy_of(None, 0) == 0.0


class Test刚切过去不算专注:
    def test_待满才算数(self):
        assert busy_of("三角洲", FOCUS_AFTER_S) == pytest.approx(1.0)

    def test_刚切过去几乎不算(self):
        assert busy_of("三角洲", 5) < 0.1

    def test_线性爬上去(self):
        half = busy_of("三角洲", FOCUS_AFTER_S // 2)
        assert 0.4 < half < 0.6


class Test憋得越久越躁:
    def test_时间加成(self):
        st = RestlessnessState()
        st.waiting_since = NOW
        just = st.value(want=0.4, app="三角洲", seconds=LONG, now=NOW)

        st2 = RestlessnessState()
        st2.waiting_since = NOW - timedelta(hours=3)
        long = st2.value(want=0.4, app="三角洲", seconds=LONG, now=NOW)
        assert long > just

    def test_她忙完歇一会儿_计时要清零(self):
        """不清的话，她忙完歇十分钟再忙，时间加成会从上次接着算 ——
        他会显得莫名其妙地已经憋了很久。"""
        st = RestlessnessState()
        st.value(want=0.5, app="三角洲", seconds=LONG, now=NOW)
        assert st.waiting_since is not None

        #: 她去休息了
        st.value(want=0.5, app=None, seconds=0, now=NOW + timedelta(minutes=1))
        assert st.waiting_since is None

    def test_说得出为什么(self):
        st = RestlessnessState()
        st.waiting_since = NOW - timedelta(minutes=40)
        st.value(want=0.6, app="三角洲", seconds=LONG, now=NOW)
        why = st.because()
        assert any("三角洲" in w for w in why)
        assert any("40" in w for w in why)

    def test_没躁动时不说空话(self):
        st = RestlessnessState()
        st.value(want=0, app=None, now=NOW)
        assert st.because() == []


class Test只存开始憋的时刻:
    """🔴 别的都是当下算的。存下来就是第二个真源，两个真源迟早对不上。"""

    def test_存了能读回来(self):
        st = RestlessnessState()
        st.waiting_since = NOW
        assert RestlessnessState.from_dict(st.to_dict()).waiting_since == NOW

    def test_空的读得动(self):
        assert RestlessnessState.from_dict(None).waiting_since is None

    def test_坏时间不炸(self):
        assert RestlessnessState.from_dict({"waiting_since": "不是时间"}).waiting_since is None


class Test真跑一轮:
    """🔴 这一组才是真正要紧的。

    `refresh_resonance()` 漏调的话躁动**永远是 0 而且不报错**。
    读源码的测试抓不到"少调了一个方法"。
    """

    @pytest.fixture
    def live(self, monkeypatch, tmp_path):
        from fastapi.testclient import TestClient
        from api.server import create_app
        from data.store import Store
        from tests.test_api import FakeNox
        from tests.test_api_world import _Ctx, _Loop

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

    @staticmethod
    def _pend(a, title="想问问她昨晚睡得好不好"):
        """塞一条"他憋着的话"进去。

        ⚠️ `IntentEngine` 没有 `add` —— 它只由 `generate()` 从 Attention 产生。
        测试直接往内部字典里放，是为了**不依赖生成路径**：
        这个文件验的是躁动，不是 intent 怎么诞生的。
        """
        from attention.intent import Intent
        i = Intent(subject="糖糖的睡眠", title=title,
                   reason="她昨晚只睡了 6 小时", attention_strength=0.8)
        a.intents._intents[i.id] = i
        return i

    @staticmethod
    def _attention(app):
        from tests.test_dejection import _find_attention
        a = _find_attention(app)
        assert a is not None
        return a

    def test_感知层那条线真的接上了(self, live):
        """🔴 `attention.link` 没接的话，躁动读不到"她在用什么"，
        永远按"不忙"算 —— 而那看起来完全正常。"""
        a = self._attention(live.app)
        assert getattr(a, "link", None) is not None, "手没交给 attention"
        assert hasattr(a.link, "_current"), "这条链路不认识「她此刻在用什么」"

    def test_她在打游戏且他有话想说_接口里就有躁动(self, live):
        a = self._attention(live.app)
        #: 他憋着一句话
        self._pend(a)
        #: 她在打游戏，已经进入状态
        a.link._current = ("三角洲行动", 600)

        d = live.get("/api/nox/resonance").json()
        assert d["ok"] and d["enabled"]
        assert "restlessness" in d["drives"], f"躁动没出来 —— {list(d['drives'])}"

        r = d["drives"]["restlessness"]
        assert 0 < r["load"] <= MAX
        assert r["because"], "说不出为什么的 Drive 不算数"
        assert any("三角洲" in b for b in r["because"])

    def test_她闲着的时候接口里没有躁动(self, live):
        """「他现在不躁动」表现成**没有这一项**，不是 `restlessness: 0.00`。"""
        a = self._attention(live.app)
        self._pend(a)
        a.link._current = None  # 传感器没数据 = 不忙

        drives = live.get("/api/nox/resonance").json()["drives"]
        assert "restlessness" not in drives

    def test_他没话想说的时候也没有躁动(self, live):
        a = self._attention(live.app)
        a.link._current = ("三角洲行动", 600)
        drives = live.get("/api/nox/resonance").json()["drives"]
        assert "restlessness" not in drives
