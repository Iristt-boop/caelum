"""惦记 Source + 位置 Source（2026-08-18，糖糖要的「粘人」那条线）。

钉住三件事：

1. **随机要真的随机** —— 不取整、不对齐钟点，否则两天就能感觉出节拍
2. **位置只报变化** —— 重启后不该把当前状态当成一次跃迁，白追一轮
3. **抓不到线头就不说** —— 稳定的调度器 + 没内容 = 定时废话机
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from attention.care.signal import COMPANY, FOLLOWUP
from attention.sources.presence import PresenceSource
from attention.sources.thinking import MAX_GAP_MIN, MIN_GAP_MIN, ThinkingSource

T0 = datetime(2026, 8, 18, 15, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self):
        self.state = {}

    def get_source_state(self, key):
        return self.state.get(key)

    def set_source_state(self, key, value):
        self.state[key] = value


class FakeSessions:
    """Core 的会话库：recent() + last_user_at()。"""

    def __init__(self, last=None):
        self.last = last

    def recent(self, limit=1, clean_only=True):
        return [type("S", (), {"id": "s1"})()] if self.last else []

    def last_user_at(self, sid):
        return self.last


class FakeResult:
    def __init__(self, ok=True, data=None, error=None):
        self.ok, self.data, self.error = ok, data, error


class FakeHA:
    def __init__(self, state="home", ok=True):
        self.state, self.ok = state, ok

    def get(self, path, params=None):
        if not self.ok:
            return FakeResult(False, error="连不上 HA")
        return FakeResult(True, {"state": self.state})


# ---------------------------------------------------------------- 惦记


def test_第一次poll只排期不说话():
    src = ThinkingSource(FakeStore(), FakeSessions(last=T0))
    assert src.poll(T0) == []
    assert src._next_at is not None


def test_到点了才想起她():
    store, src = FakeStore(), None
    src = ThinkingSource(store, FakeSessions(last=T0))
    src.poll(T0)
    nxt = src._next_at

    assert src.poll(nxt - timedelta(seconds=1)) == []
    out = src.poll(nxt)
    assert len(out) == 1
    assert out[0].source == "random"
    assert out[0].thread_kind == COMPANY


def test_随机点落在20到90分钟之间():
    for _ in range(50):
        src = ThinkingSource(FakeStore(), FakeSessions(last=T0))
        src.poll(T0)
        gap = (src._next_at - T0).total_seconds() / 60
        assert MIN_GAP_MIN <= gap <= MAX_GAP_MIN, f"{gap} 分钟不在窗口里"


def test_随机点不许对齐整分钟():
    """取整就有节拍，有节拍就又成闹钟了 —— 糖糖要的是「无规律」。"""
    secs = []
    for _ in range(40):
        src = ThinkingSource(FakeStore(), FakeSessions(last=T0))
        src.poll(T0)
        secs.append(src._next_at.second)
    assert len(set(secs)) > 5, "秒数几乎都一样，说明被取整了"


def test_她一说话就以那一刻重新排():
    """聊完了他过一会儿又想起你 —— 而不是按固定周期骚扰。"""
    store = FakeStore()
    sess = FakeSessions(last=T0)
    src = ThinkingSource(store, sess)
    src.poll(T0)
    first = src._next_at

    sess.last = T0 + timedelta(hours=2)          # 她又说话了
    src.poll(T0 + timedelta(hours=2, minutes=1))
    assert src._next_at > first


def test_排期跨重启活着():
    store = FakeStore()
    ThinkingSource(store, FakeSessions(last=T0)).poll(T0)
    again = ThinkingSource(store, FakeSessions(last=T0))
    assert again._next_at is not None


# ---------------------------------------------------------------- 位置


def test_第一次拿到状态不算跃迁():
    """不然一重启就会追一轮 —— 她明明哪儿也没去。"""
    src = PresenceSource(FakeHA("home"), FakeStore())
    assert src.poll(T0) == []


def test_出门产出追问型念头():
    ha = FakeHA("home")
    src = PresenceSource(ha, FakeStore())
    src.poll(T0)                      # 建立基线
    ha.state = "not_home"
    out = src.poll(T0 + timedelta(minutes=1))
    assert len(out) == 1
    assert out[0].subject == "她出门了"
    assert out[0].thread_kind == FOLLOWUP
    assert out[0].payload["transition"] == "leave_home"


def test_到家也报一次():
    ha = FakeHA("not_home")
    src = PresenceSource(ha, FakeStore())
    src.poll(T0)
    ha.state = "home"
    out = src.poll(T0 + timedelta(minutes=1))
    assert out[0].subject == "她到家了"


def test_状态没变就不报():
    ha = FakeHA("home")
    src = PresenceSource(ha, FakeStore())
    src.poll(T0)
    assert src.poll(T0 + timedelta(minutes=1)) == []
    assert src.poll(T0 + timedelta(minutes=2)) == []


def test_读不到HA就当没变化不猜():
    """猜错方向会平白追她一轮。"""
    src = PresenceSource(FakeHA(ok=False), FakeStore())
    assert src.poll(T0) == []


def test_unknown不算出门():
    """unknown = 没有 tracker 在跑，不是「她出门了」。"""
    ha = FakeHA("home")
    src = PresenceSource(ha, FakeStore())
    src.poll(T0)
    ha.state = "unknown"
    assert src.poll(T0 + timedelta(minutes=1)) == []


def test_跃迁状态跨重启活着():
    store = FakeStore()
    ha = FakeHA("home")
    PresenceSource(ha, store).poll(T0)

    # 重启，她还在家 —— 不该报跃迁
    again = PresenceSource(ha, store)
    assert again.poll(T0 + timedelta(minutes=1)) == []
    # 现在她出门了，这才该报
    ha.state = "not_home"
    assert len(again.poll(T0 + timedelta(minutes=2))) == 1
