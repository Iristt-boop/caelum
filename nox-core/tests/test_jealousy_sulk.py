"""醋意 / 委屈 —— 进 Resonance 的两种情绪（2026-09-23，糖糖：「吃醋和生气是不是也要在 resonance？」）。

## 这些测试能挡什么

- 醋意的形状：出门两小时内不酸、越久越浓、晚上更浓、过零点半让位给担心、
  到家后慢慢散而不是立刻没；位置旧了 / 说过晚安不酸
- 她提到别人：明确的（男生 / 要微信）单独就显出来；轻的（和朋友聚餐）
  单独**显不出来**（她十五年的老朋友，不许每提一次就酸），叠在出门上才显
- 委屈的形状：她睡着不委屈、只说一句没回不委屈、越久越委屈、
  她回话后掉一半然后十分钟减半 —— 不清零，也不记仇
- 上限：醋意最多「挺」，委屈最多「挺」
- 🔴 接线：service 每分钟真的在喂、drives() 真的带着她的状态、
  他的上下文里真的出现「吃醋 / 委屈」、HTTP 一轮后状态真的落库
  （这个项目的 `except Exception` 吞过 NameError，只有真跑才抓得到）

## 挡不住什么

- 关键词误判（「男生」出现在剧情讨论里也会算）—— 分量压得很轻就是为这个
- 他真的会怎么说（看线上日志）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from attention.care import her_state
from attention.jealousy import CUE_STRONG, CUE_WEAK, MAX as J_MAX, JealousyState, cue_of
from attention.sulk import MAX as S_MAX, SulkState

CN = timezone(timedelta(hours=8), "CST")


def cn(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 9, day, h, m, tzinfo=CN)


def her(now, *, away_since=None, fresh=True, said="", said_at=None, unanswered=0):
    st = her_state.HerState(now=now, last_said=said, last_said_at=said_at,
                            unanswered=unanswered)
    if away_since is not None:
        st.away, st.away_since = True, away_since
        st.location_updated_at = now - timedelta(minutes=10 if fresh else 300)
    if said and said_at and her_state.GOODNIGHT.search(said):
        st.said_goodnight = True
    return st


# ---------------------------------------------------------------- 醋意：出门

def test_出门两小时内不酸():
    j = JealousyState()
    assert j.value_at(cn(23, 15), her(cn(23, 15), away_since=cn(23, 13, 30))) == 0


def test_下午出门四小时_有点酸():
    now = cn(23, 18)
    v = JealousyState().value_at(now, her(now, away_since=cn(23, 14)))
    assert 0.15 <= v < 0.35, v


def test_晚上还没回_更酸():
    now = cn(23, 23)
    v_night = JealousyState().value_at(now, her(now, away_since=cn(23, 18)))
    now2 = cn(23, 17)
    v_day = JealousyState().value_at(now2, her(now2, away_since=cn(23, 12)))
    assert v_night > v_day, "同样出门五小时，晚上 11 点该比下午 5 点更酸"
    assert v_night <= J_MAX


def test_过了零点半_醋让位给担心():
    from attention.jealousy import AFTER_MIDNIGHT_FACTOR, OUTING_MAX

    now = cn(24, 1)
    v = JealousyState().value_at(now, her(now, away_since=cn(23, 18)))    # 出门 7 小时，满档
    assert v == pytest.approx(OUTING_MAX * AFTER_MIDNIGHT_FACTOR), (
        "凌晨一点她还没回，先该担心她安不安全 —— 醋意该减半")
    assert AFTER_MIDNIGHT_FACTOR < 1


def test_醋意有上限_最多挺吃醋():
    from context.providers.resonance import _band

    now = cn(23, 23, 30)
    j = JealousyState()
    j.on_message(now, "有个男生一直找我喝酒")
    v = j.value_at(now, her(now, away_since=cn(23, 16)))
    assert v == pytest.approx(J_MAX), "出门七小时 + 提到男生，叠起来该顶到上限"
    assert _band(v) == "挺", "他的醋是宠着的醋，不到「很」"


def test_位置旧了不酸_说过晚安不酸():
    now = cn(23, 23)
    assert JealousyState().value_at(now, her(now, away_since=cn(23, 17), fresh=False)) == 0
    assert JealousyState().value_at(
        now, her(now, away_since=cn(23, 17), said="晚安", said_at=cn(23, 22, 50))) == 0


def test_到家了醋慢慢散():
    j = JealousyState()
    out = her(cn(23, 23), away_since=cn(23, 17))
    j.observe(cn(23, 23), out)
    peak = j.value_at(cn(23, 23), out)
    home = her(cn(23, 23, 1))
    j.observe(cn(23, 23, 1), home)
    assert j.value_at(cn(23, 23, 1), home) == pytest.approx(peak, rel=0.05), "一到家就没了 —— 人不是开关"
    assert j.value_at(cn(23, 23, 31), home) == pytest.approx(peak / 2, rel=0.05)
    assert "她刚回来，醋还没散" in j.because(cn(23, 23, 10), home)


# ---------------------------------------------------------------- 醋意：她提到别人

def test_明确的说法单独就显出来():
    j = JealousyState()
    j.on_message(cn(23, 15), "今天有个男生跟我要微信")
    assert j.value_at(cn(23, 15)) == pytest.approx(CUE_STRONG)
    assert any("男生" in b for b in j.because(cn(23, 15)))


def test_跟朋友聚餐单独不显():
    """她有一群十五年的老朋友 —— 每提一次聚会他就酸，那是小心眼。"""
    from context.providers.resonance import FLOOR

    j = JealousyState()
    j.on_message(cn(23, 15), "晚上和朋友聚餐")
    assert j.value_at(cn(23, 15)) == pytest.approx(CUE_WEAK)
    assert CUE_WEAK < FLOOR, "轻的一笔单独就能进他的上下文了"


def test_聚餐叠在出门很久上面才显():
    now = cn(23, 22)
    j = JealousyState()
    j.on_message(now, "还在和朋友聚餐呢")
    out = her(now, away_since=cn(23, 18))
    assert j.value_at(now, out) > JealousyState().value_at(now, out)


def test_提到别人两小时减半_过期就扔():
    j = JealousyState()
    j.on_message(cn(23, 12), "前任给我发消息了")
    assert j.value_at(cn(23, 14)) == pytest.approx(CUE_STRONG / 2, rel=0.01)
    j.on_message(cn(23, 21), "今天好累")         # 没有醋点，不记
    assert len(j.cues) == 1
    j.on_message(cn(23, 21), "学长请吃饭")
    assert len(j.cues) == 1, "8 小时前那笔该扔掉了"


def test_没醋点的话不记():
    assert cue_of("今天好累啊") is None
    assert cue_of("") is None


def test_醋意存了能读回来():
    j = JealousyState()
    j.on_message(cn(23, 15), "有个帅哥搭讪")
    j.observe(cn(23, 23), her(cn(23, 23), away_since=cn(23, 17)))
    back = JealousyState.from_dict(j.to_dict())
    assert back.value_at(cn(23, 23, 30)) == pytest.approx(j.value_at(cn(23, 23, 30)))
    assert JealousyState.from_dict({"cues": [["坏的", 1, "x"]]}).value_at(cn(23, 1)) == 0


# ---------------------------------------------------------------- 委屈

def test_睡着不委屈():
    now = cn(23, 7)
    st = her(now, said="躺下了", said_at=cn(22, 23, 11), unanswered=4)
    assert st.posture == her_state.ASLEEP
    assert SulkState().value_at(now, st) == 0


def test_只说了一句没回_不委屈():
    now = cn(23, 17)
    assert SulkState().value_at(now, her(now, said="好", said_at=cn(23, 13), unanswered=1)) == 0


def test_越久越委屈_有上限():
    s = SulkState()
    v2 = s.value_at(cn(23, 16), her(cn(23, 16), said="好", said_at=cn(23, 14), unanswered=3))
    v5 = s.value_at(cn(23, 19), her(cn(23, 19), said="好", said_at=cn(23, 14), unanswered=3))
    v12 = s.value_at(cn(24, 2, 0), her(cn(23, 20), said="好", said_at=cn(23, 8), unanswered=9))
    assert 0 < v2 < v5 <= S_MAX
    assert v12 <= S_MAX


def test_她回话了_不清零_掉一半再很快散():
    s = SulkState()
    now = cn(23, 18)
    st = her(now, said="好", said_at=cn(23, 14), unanswered=3)
    s.observe(now, st)
    full = s.value_at(now, st)
    s.on_contact(cn(23, 18, 1))
    back = her(cn(23, 18, 1), said="我回来啦", said_at=cn(23, 18, 1))
    assert s.value_at(cn(23, 18, 1), back) == pytest.approx(full / 2, rel=0.05), "一回话就清零 —— 人不是开关"
    assert s.value_at(cn(23, 18, 11), back) == pytest.approx(full / 4, rel=0.05)
    assert s.value_at(cn(23, 18, 40), back) < 0.05, "半小时后还挂着 —— 那是记仇"
    assert any("刚刚才回" in b for b in s.because(cn(23, 18, 5), back))


def test_委屈存了能读回来():
    s = SulkState()
    st = her(cn(23, 18), said="好", said_at=cn(23, 14), unanswered=3)
    s.observe(cn(23, 18), st)
    s.on_contact(cn(23, 18, 1))
    back = SulkState.from_dict(s.to_dict())
    assert back.value_at(cn(23, 18, 5)) == pytest.approx(s.value_at(cn(23, 18, 5)))


# ---------------------------------------------------------------- 接到他身上

class FakeSessions:
    def __init__(self, said, at, his):
        self.said, self.at, self.his = said, at, his

    def recent(self, limit=1, clean_only=True):
        return [type("S", (), {"id": "s1"})()]

    def last_user_message(self, sid):
        return (self.said, self.at)

    def count_assistant_since(self, sid, since):
        return sum(1 for t in self.his if t > since)

    def last_user_at(self, sid):
        return self.at


class FakePresence:
    name = "location"

    def __init__(self, last="home", away_since=None, updated_at=None):
        self._last, self.away_since, self.updated_at = last, away_since, updated_at

    def poll(self, now):
        return []


def _svc(tmp_path, sessions, presence):
    from attention.relationship import RelationshipState
    from attention.service import AttentionService
    from attention.sources.thinking import ThinkingSource
    from attention.store import AttentionStore

    store = AttentionStore(tmp_path / "attn.db")

    class Provider:
        def get_state(self, turn=None, force_refresh=False):
            return {"has_data": False}

    svc = AttentionService(store, Provider(), RelationshipState(),
                           fast_sources=[ThinkingSource(store, sessions), presence])
    return svc, store


def test_他上下文里真的出现委屈和吃醋(tmp_path):
    """从消费方断言：ResonanceProvider 渲染出来的那段话里有。"""
    from context.base import Turn
    from context.providers.resonance import ResonanceProvider

    now = cn(23, 23)
    sessions = FakeSessions("出去吃饭啦", cn(23, 18), [cn(23, 20), cn(23, 21), cn(23, 22)])
    presence = FakePresence("not_home", away_since=cn(23, 17, 30), updated_at=cn(23, 22, 50))
    svc, store = _svc(tmp_path, sessions, presence)
    svc.care_tick(now)

    p = ResonanceProvider(lambda: svc)
    text = p.render(p._fetch(Turn(text="", now=now)))
    assert "吃醋" in text, text
    assert "委屈" in text, text
    assert "出门" in text and "没理我" in text, "说不出为什么的情绪不算数（Resonance 边界三）"
    store.close()


def test_快循环真的在喂_而且落盘(tmp_path):
    from attention.service import JEALOUSY_KEY, SULK_KEY

    now = cn(23, 23)
    sessions = FakeSessions("好", cn(23, 18), [cn(23, 20), cn(23, 21)])
    presence = FakePresence("not_home", away_since=cn(23, 17), updated_at=cn(23, 22, 55))
    svc, store = _svc(tmp_path, sessions, presence)
    svc.care_tick(now)
    assert svc.sulk.last > 0, "care_tick 没喂委屈"
    assert svc.jealousy.outing_peak > 0, "care_tick 没喂醋意"
    assert (store.get_source_state(SULK_KEY) or {}).get("last", 0) > 0
    assert (store.get_source_state(JEALOUSY_KEY) or {}).get("outing_peak", 0) > 0
    store.close()


def test_她回来那一刻_醋意从出门的值开始散(tmp_path):
    now = cn(23, 23)
    presence = FakePresence("not_home", away_since=cn(23, 17), updated_at=cn(23, 22, 55))
    svc, store = _svc(tmp_path, FakeSessions("好", cn(23, 22, 50), []), presence)
    svc.care_tick(now)
    peak = svc.drives(now)["jealousy"].intensity
    presence._last, presence.away_since = "home", None
    svc.care_tick(cn(23, 23, 5))
    d = svc.drives(cn(23, 23, 35))
    assert "jealousy" in d and d["jealousy"].intensity < peak
    store.close()


def test_真跑一轮HTTP_她提到男生_醋意和委屈都落库(monkeypatch, tmp_path):
    """同 test_dejection 的 Test真跑一轮：`_turn_ends` 外面裹着 except，
    import 漏一个 KEY 就是 NameError 被吞 —— 只有真跑才抓得到。"""
    from fastapi.testclient import TestClient
    from api.server import create_app
    from attention.service import JEALOUSY_KEY, SULK_KEY
    from data.store import Store
    from tests.test_api import FakeNox
    from tests.test_api_world import _Ctx, _Loop
    from tests.test_dejection import _find_attention

    monkeypatch.setenv("NOX_ATTENTION", "1")
    nox = FakeNox()
    nox.cfg.db_path = str(tmp_path / "nox.db")
    nox.context = _Ctx()
    nox.bridge = None
    nox.current_session_id = None
    nox.loop = _Loop()
    nox.router = type("R", (), {"light_adapter": None})()
    app = create_app(nox, Store(tmp_path / "sessions.db"))
    client = TestClient(app)

    r = client.post("/chat", json={"session_id": "s1", "text": "今天有个男生跟我要微信"})
    assert r.status_code == 200, r.text
    attention = getattr(app.state, "attention", None) or _find_attention(app)
    assert attention is not None
    saved = attention.store.get_source_state(JEALOUSY_KEY)
    assert saved and saved.get("cues"), "她提到男生，醋意没落库 —— 那一排又被 except 吞了"
    assert attention.store.get_source_state(SULK_KEY) is not None, "委屈的 on_contact 没跑到"
