"""Growth Loop 第 0 期：经历账本（CAELUM-GROWTH-LOOP-设计.md 第四节 ②、第四·五节）。

钉五样东西：

1. **只追加** —— 碰账本的只有 append / list，结构上不许长出改和删
2. **判定只有一个来源** —— 「理了 / 没理」原样抄 rhythm 的判定，每条开口只记一次
3. **情境在开口那一刻取** —— 4 小时后判定时她早换了状态
4. **睡觉不算冷落、自言自语不进账**（G8 / G11）
5. **回调挂了不连累节奏**
"""

from __future__ import annotations

import inspect
import logging
from datetime import timedelta

from attention.care.signal import CareSignal
from attention.rhythm import RhythmModulator
from attention.store import AttentionStore
from growth import experience
from growth.experience import bucket, record_reply
from tests.test_her_state import LAY_DOWN, FakePresence, FakeSessions, cn

# ---------------------------------------------------------------- 账本本身


def test_账本记了能原样读回来_按发生时刻排(tmp_path):
    store = AttentionStore(tmp_path / "attn.db")
    store.append_experience(at=cn(28, 15).isoformat(), kind="proactive_reply",
                            context={"bucket": "14-18", "silent_min": 90}, outcome=1.0,
                            attributed_to="user", recorded_at=cn(28, 16).isoformat())
    store.append_experience(at=cn(28, 10).isoformat(), kind="proactive_reply",
                            context={"bucket": "9-12"}, outcome=0.0,
                            attributed_to="user", recorded_at=cn(28, 16).isoformat())
    rows = store.list_experiences("proactive_reply")
    assert [r["context"]["bucket"] for r in rows] == ["9-12", "14-18"]
    assert rows[1]["context"]["silent_min"] == 90 and rows[1]["outcome"] == 1.0
    assert store.list_experiences("别的") == []
    store.close()


def test_账本结构上只能往后加():
    """习惯是从账本重放出来的。能改账本，就能悄悄改掉他为什么变成现在这样。"""
    names = {n for n, _ in inspect.getmembers(AttentionStore, inspect.isfunction)
             if "experience" in n}
    assert names == {"append_experience", "list_experiences"}, names
    src = inspect.getsource(inspect.getmodule(AttentionStore)).upper()
    assert "UPDATE EXPERIENCES" not in src and "DELETE FROM EXPERIENCES" not in src


def test_时段桶按她那边的钟():
    cases = {(8, 59): "1-9", (9, 0): "9-12", (13, 59): "12-14", (14, 0): "14-18",
             (20, 59): "18-21", (21, 0): "21-1", (0, 30): "21-1", (1, 0): "1-9"}
    for (h, m), want in cases.items():
        assert bucket(cn(28, h, m)) == want, (h, m)


# ---------------------------------------------------------------- rhythm 判定 → 账本


class _Store:
    def __init__(self) -> None:
        self._d: dict = {}

    def get_source_state(self, k):
        return self._d.get(k)

    def set_source_state(self, k, v):
        self._d[k] = v


def _rhythm():
    m = RhythmModulator(_Store())
    got: list[dict] = []
    m.on_judged = lambda e: got.append(dict(e))
    return m, got


def test_她回了_判定时交出来_情境原样带着():
    m, got = _rhythm()
    ctx = {"bucket": "14-18", "posture": "normal", "silent_min": 90}
    m.on_spoke(cn(28, 15), ctx)
    m.on_contact(cn(28, 15, 30))
    assert len(got) == 1 and got[0]["replied"] is True and got[0]["ctx"] == ctx
    m.on_contact(cn(28, 16))
    m.tick(cn(28, 20))
    assert len(got) == 1, "同一条开口只许记一次 —— 记两次就是一次没回罚两遍的反面"


def test_她没回_满四小时才交_睡着不判():
    m, got = _rhythm()
    m.on_spoke(cn(28, 22), {"bucket": "21-1"})
    m.tick(cn(28, 23))
    assert got == [], "还没到 4 小时"
    m.tick(cn(29, 3))
    assert got == [], "判定时刻她在睡 —— 睡觉不算冷落（G8）"
    m.tick(cn(29, 11))
    assert len(got) == 1 and got[0]["replied"] is False


def test_回调挂了_节奏照常_而且留痕(caplog):
    m = RhythmModulator(_Store())

    def boom(_e):
        raise RuntimeError("库锁了")

    m.on_judged = boom
    m.on_spoke(cn(28, 15))
    with caplog.at_level(logging.ERROR, logger="attention.rhythm"):
        m.on_contact(cn(28, 15, 30))
        m.tick(cn(28, 20))
    assert m._history[-1]["replied"] is True, "账本挂了不许把节奏也带挂"
    assert any("回调失败" in r.getMessage() for r in caplog.records), "不许静默"


def test_抄进账本_回话用了多久也记上(tmp_path):
    store = AttentionStore(tmp_path / "attn.db")
    record_reply(store, {"at": cn(28, 15).isoformat(), "replied": True,
                         "reply_at": cn(28, 15, 42).isoformat(),
                         "ctx": {"bucket": "14-18", "source": "random"}})
    (row,) = store.list_experiences(experience.KIND_PROACTIVE_REPLY)
    assert row["outcome"] == 1.0 and row["attributed_to"] == "user"
    assert row["context"] == {"bucket": "14-18", "source": "random", "reply_min": 42}
    store.close()


# ---------------------------------------------------------------- 接在真的 service 上


def _svc(tmp_path, sessions):
    from attention.relationship import RelationshipState
    from attention.service import AttentionService
    from attention.sources.thinking import ThinkingSource

    store = AttentionStore(tmp_path / "attn.db")

    class Provider:
        def get_state(self, turn=None, force_refresh=False):
            return {"has_data": False}

    svc = AttentionService(
        store, Provider(), RelationshipState(),
        speaker=lambda intent, decision, prompt=None: "msg-1",
        fast_sources=[ThinkingSource(store, sessions), FakePresence("home")],
        rhythm=RhythmModulator(store),
    )
    return svc, store


def test_白天找她_开口那一刻的情境进账_不是判定那一刻(tmp_path):
    """她 14:00 说过话，他 15:30 开口（沉默 90 分钟），她 16:10 回 —— 账上记的是 90，不是 0。"""
    sessions = FakeSessions("先去弄设计了", cn(28, 14))
    svc, store = _svc(tmp_path, sessions)
    svc.care.submit(CareSignal(source="random", subject="想起你了"))
    assert [o.action for o in svc.care_tick(cn(28, 15, 30))] == ["spoke"]

    sessions.said, sessions.at = "回来啦", cn(28, 16, 10)
    svc.rhythm.on_contact(cn(28, 16, 10))
    (row,) = store.list_experiences(experience.KIND_PROACTIVE_REPLY)
    assert row["outcome"] == 1.0
    assert row["context"]["silent_min"] == 90, row["context"]
    assert row["context"]["posture"] == "normal" and row["context"]["bucket"] == "14-18"
    assert row["context"]["source"] == "random" and row["context"]["reply_min"] == 40
    store.close()


def test_睡着时的自言自语不进账(tmp_path):
    svc, store = _svc(tmp_path, FakeSessions("躺下了", LAY_DOWN))
    svc.care.submit(CareSignal(source="random", subject="想起你了"))
    assert [o.action for o in svc.care_tick(cn(23, 3))] == ["spoke"]
    svc.rhythm.tick(cn(23, 3) + timedelta(hours=9))
    svc.rhythm.on_contact(cn(23, 12))
    assert store.list_experiences() == [], "她睡着时他说的话本来就不等回（G11）"
    store.close()
