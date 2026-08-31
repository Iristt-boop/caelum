"""共读 / 共听 / 共影 → World Model 的接线测试（Topic_Pool §3.1.2）。

守的是四件容易做砸的事：

1. **幂等** —— 心跳几十轮，同一份进度不能写重（dedup_key 立得住）
2. **只记会话不记心跳** —— 观影误开两分钟不算一场
3. **共听只记「一起」** —— 她自己听的不进 world
4. **一块坏了不许带塌另外两块** —— sleep.py 的老规矩
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.relationship import RelationshipState  # noqa: E402
from attention.service import AttentionService  # noqa: E402
from attention.sources.shared_activities import (  # noqa: E402
    SharedActivitiesSource,
    _ts,
)
from attention.store import AttentionStore  # noqa: E402
from world_model import WorldModel  # noqa: E402
from world_model.types import FRESH, STALE  # noqa: E402

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


@dataclass
class Resp:
    """学 RestClient 的返回：.ok / .data / .error。"""

    ok: bool = True
    data: Any = None
    error: str | None = None


class FakeClient:
    def __init__(self, routes: dict[str, Resp] | None = None):
        self.routes = routes or {}
        self.calls: list[str] = []

    def get(self, path: str, params: Any = None) -> Resp:
        self.calls.append(path)
        return self.routes.get(path, Resp(ok=False, error="not mocked"))


class FakeProvider:
    def __init__(self, **kw: Any) -> None:
        self.state = {"has_data": True, **kw}

    def get_state(self, turn: Any = None, force_refresh: bool = False) -> dict:
        return self.state


@pytest.fixture
def world(tmp_path):
    w = WorldModel(tmp_path / "world.db")
    yield w
    w.store.close()


def _reading_payload() -> dict:
    return {
        "book_a": {
            "title": "考试脑科学", "status": "reading",
            "progressPercent": 43, "chunksRead": 12, "chunkCount": 28,
            "lastChunkId": "ch-12", "lastReadAt": "2026-08-31T03:20:00Z",
        },
        "book_old": {
            "title": "半年前翻过两页", "status": "paused",
            "progressPercent": 5, "lastReadAt": "2026-02-01T00:00:00Z",
        },
    }


# ---------------------------------------------------------------- 共读


def test_reading_records_recent_progress(world):
    """最近在动的书记一条；放下半年的不回灌。"""
    reading = FakeClient({"/api/progress": Resp(data=_reading_payload())})
    src = SharedActivitiesSource(reading=reading, world=world)

    assert src.poll(NOW) is None          # 只记账，不产事件
    ev = world.query("reading_progress")
    assert len(ev) == 1
    assert ev[0].raw["progress"] == 43
    assert ev[0].raw["complete"] is False
    assert ev[0].observed_at == _ts("2026-08-31T03:20:00Z")
    assert "reading/book_a/" in ev[0].reference


def test_reading_finished_book_any_age(world):
    """读完的书不管多久都值得留档 —— 「读完了」本身是事实。"""
    payload = _reading_payload()
    payload["book_done"] = {
        "title": "读完的那本", "status": "finished", "progressPercent": 100,
        "complete": True, "lastReadAt": "2026-06-01T00:00:00Z",
    }
    reading = FakeClient({"/api/progress": Resp(data=payload)})
    SharedActivitiesSource(reading=reading, world=world).poll(NOW)

    ev = world.query("reading_progress")
    done = [e for e in ev if e.raw["book_id"] == "book_done"]
    assert len(done) == 1 and done[0].raw["complete"] is True


def test_reading_poll_twice_no_dupes(world):
    """心跳连跑几轮，同一份进度只落一条 —— 幂等是接线规格的第一条。"""
    reading = FakeClient({"/api/progress": Resp(data=_reading_payload())})
    src = SharedActivitiesSource(reading=reading, world=world)

    src.poll(NOW)
    src.poll(NOW + timedelta(minutes=15))
    src.poll(NOW + timedelta(minutes=30))
    assert len(world.query("reading_progress")) == 1


def test_reading_progress_change_lands(world):
    """她又读了：lastReadAt 变了，新事实立得住新 key。"""
    reading = FakeClient({"/api/progress": Resp(data=_reading_payload())})
    src = SharedActivitiesSource(reading=reading, world=world)

    src.poll(NOW)
    reading.routes["/api/progress"] = Resp(data={
        "book_a": {**_reading_payload()["book_a"],
                   "progressPercent": 61, "chunksRead": 17,
                   "lastChunkId": "ch-17",
                   "lastReadAt": "2026-08-31T09:40:00Z"},
    })
    src.poll(NOW + timedelta(hours=6))

    ev = world.query("reading_progress")
    assert len(ev) == 2
    assert ev[0].raw["progress"] == 61     # query 新的在前
    # State 永远是最新一条：她正在读的地方
    st = world.get_state("reading_progress", now=NOW + timedelta(hours=6))
    assert st.value["progress"] == 61 and st.status == FRESH


# ---------------------------------------------------------------- 共影


def _watch_payload() -> dict:
    return {"items": [
        {"id": "w1", "title": "寂寞拍卖师", "episode": "",
         "started_at": "2026-08-31T10:15:00Z", "ended_at": "2026-08-31T11:45:00Z",
         "play_state": "ended", "position_ms": 5_400_000, "duration_s": 5400},
        {"id": "w2", "title": "误开两分钟", "episode": "",
         "started_at": "2026-08-30T15:00:00Z", "ended_at": "2026-08-30T15:02:00Z"},
        {"id": "w3", "title": "还在看的那场", "episode": "S1E3",
         "started_at": "2026-08-31T11:50:00Z", "ended_at": None},
    ]}


def test_watching_only_ended_sessions(world):
    """只记结束的会话：误开两分钟不算，还在看的不归这里管。"""
    bridge = FakeClient({"/api/watch/history": Resp(data=_watch_payload())})
    SharedActivitiesSource(bridge=bridge, world=world).poll(NOW)

    ev = world.query("watching_session")
    assert len(ev) == 1
    assert ev[0].raw["session_id"] == "w1"
    assert ev[0].raw["minutes"] == 90
    assert ev[0].raw["finished"] is True
    assert "watching/w1" in ev[0].reference


def test_watching_live_session_excluded_not_lost(world):
    """w3 还在看 —— 这轮不记，但 ended_at 一落下一轮就记（同一场）。"""
    bridge = FakeClient({"/api/watch/history": Resp(data=_watch_payload())})
    src = SharedActivitiesSource(bridge=bridge, world=world)
    src.poll(NOW)

    payload = _watch_payload()
    payload["items"][2]["ended_at"] = "2026-08-31T12:30:00Z"
    payload["items"][2]["play_state"] = "ended"
    bridge.routes["/api/watch/history"] = Resp(data=payload)
    src.poll(NOW + timedelta(hours=1))

    ev = world.query("watching_session")
    assert [e.raw["session_id"] for e in ev] == ["w3", "w1"]


def test_watch_state_goes_stale_after_two_hours(world):
    """「正在看」是个短状态：TTL 2 小时，过期自动 stale，不用谁去清。"""
    bridge = FakeClient({"/api/watch/history": Resp(data=_watch_payload())})
    SharedActivitiesSource(bridge=bridge, world=world).poll(NOW)

    assert world.get_state("watching_session", now=NOW).status == FRESH
    late = NOW + timedelta(hours=3)
    assert world.get_state("watching_session", now=late).status == STALE


# ---------------------------------------------------------------- 共听


def _listen_payload() -> dict:
    return {"memories": {
        "101": {"name": "夜空中最亮的星", "artist": "逃跑计划",
                "togetherCount": 2, "lastListened": "2026-08-30T21:00:00Z"},
        "102": {"name": "她自己点的", "togetherCount": 0,
                "lastListened": "2026-08-30T21:00:00Z"},
        "103": {"name": "很久以前一起听的", "togetherCount": 5,
                "lastListened": "2026-05-01T00:00:00Z"},
    }}


def test_listening_only_together_and_recent(world):
    """只记「一起听」；她自己听的不进 world，太久的由 eryu_experience 管。"""
    eryu = FakeClient({"/music/memory": Resp(data=_listen_payload())})
    SharedActivitiesSource(eryu=eryu, world=world).poll(NOW)

    ev = world.query("listening_together")
    assert len(ev) == 1
    assert ev[0].raw["together_count"] == 2
    assert ev[0].raw["title"] == "夜空中最亮的星"


def test_listening_count_increment_lands(world):
    """她又听完了同一个：count 变了就是新事实。"""
    eryu = FakeClient({"/music/memory": Resp(data=_listen_payload())})
    src = SharedActivitiesSource(eryu=eryu, world=world)
    src.poll(NOW)

    eryu.routes["/music/memory"] = Resp(data={"memories": {
        "101": {**_listen_payload()["memories"]["101"],
                "togetherCount": 3, "lastListened": "2026-08-31T08:00:00Z"},
    }})
    src.poll(NOW + timedelta(hours=1))

    ev = world.query("listening_together")
    assert [e.raw["together_count"] for e in ev] == [3, 2]


# ---------------------------------------------------------------- 兜底


def test_one_broken_service_does_not_sink_others(world):
    """共读挂了，共听照样记 —— 一块坏了不许带塌别人。"""
    reading = FakeClient({"/api/progress": Resp(ok=False, error="500")})
    eryu = FakeClient({"/music/memory": Resp(data=_listen_payload())})
    SharedActivitiesSource(reading=reading, eryu=eryu, world=world).poll(NOW)

    assert world.query("reading_progress") == []
    assert len(world.query("listening_together")) == 1


def test_no_clients_noop(world):
    """三块服务一个都没配：source 建得起来，poll 什么都不做。"""
    src = SharedActivitiesSource(world=world)
    assert src.poll(NOW) is None
    assert world.query("reading_progress") == []
    assert world.query("watching_session") == []
    assert world.query("listening_together") == []


# ---------------------------------------------------------------- 接线


def test_attention_service_runs_shared_source(tmp_path):
    """挂在 AttentionService 的心跳上，tick 一轮事实就落库。"""
    astore = AttentionStore(tmp_path / "attn.db")
    world = WorldModel(tmp_path / "world.db")
    reading = FakeClient({"/api/progress": Resp(data=_reading_payload())})
    try:
        svc = AttentionService(
            astore, FakeProvider(sleep_date="2026-08-31", sleep_min=430),
            RelationshipState(),
            shared_sources=[SharedActivitiesSource(reading=reading, world=world)],
        )
        svc.tick(NOW)
        assert len(world.query("reading_progress")) == 1
    finally:
        astore.close()
        world.store.close()
