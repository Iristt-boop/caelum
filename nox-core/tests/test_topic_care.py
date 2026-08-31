"""入口 A 的测试：池子 → Care 线头 → surfaced（Topic_Pool §4.1）。

守的是定稿时立下的规矩：

1. **喂料不开口** —— TopicSource 只产生念头；开口决策全在 Orchestrator
   （吃闸、吃额度、进账本），源自己没有一条通往 speaker 的路
2. **surfaced 只在真开口后记** —— [SKIP]、dry-run、翻池子都不算
3. **空池子不进账本** —— 那不是「想了想说不出」，是没什么可想
4. 翻池子（topics_browse）是只读的，翻过不记账
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.care.ledger import BLOCK, SKIP, SPEAK  # noqa: E402
from attention.care.orchestrator import (  # noqa: E402
    CareOrchestrator,
    SourcePolicy,
)
from attention.care.signal import ThreadBook  # noqa: E402
from attention.relationship import RelationshipState  # noqa: E402
from attention.service import AttentionService  # noqa: E402
from attention.store import AttentionStore  # noqa: E402
from topic_pool import tool as topic_tool  # noqa: E402
from topic_pool.care import TopicSource  # noqa: E402
from topic_pool.pool import TopicPool  # noqa: E402
from topic_pool.store import Topic  # noqa: E402

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


class FakeStateStore:
    def __init__(self) -> None:
        self._states: dict[str, dict] = {}

    def get_source_state(self, key: str) -> dict | None:
        return self._states.get(key)

    def set_source_state(self, key: str, value: dict) -> None:
        self._states[key] = value


class FakeSpeaker:
    """返回 text = 说了；返回 None = 他自己 [SKIP] 了。"""

    def __init__(self, text: str | None = "好东西，给你看个。"):
        self.text = text
        self.prompts: list[str] = []

    def __call__(self, intent, decision, prompt=None, **kw):
        self.prompts.append(prompt or "")
        return self.text


def _pool_with_topics(tmp_path, n: int = 1) -> TopicPool:
    pool = TopicPool(tmp_path / "topics.db")
    for i in range(n):
        pool.store.add_topic(
            Topic(hook=f"切口{i}", source_title=f"来源{i}", source_url=f"u{i}",
                  category="ai", relevance=0.5 + i),
            dedup_key=f"u{i}",
        )
    return pool


def _topic_signal(topic_id: str = "t1") -> Any:
    from attention.care.signal import CareSignal
    return CareSignal(
        source="topic", subject="池子里没聊过的：测试",
        urgency=0.3,
        payload={"topic_id": topic_id, "hook": "切口", "source_title": "来源",
                 "source_url": "u", "category": "ai"},
    )


# ---------------------------------------------------------------- 源


def test_source_first_poll_only_schedules(tmp_path):
    src = TopicSource(FakeStateStore(), _pool_with_topics(tmp_path))
    out = src.poll(NOW)
    assert out == []                              # 第一眼只排下一步
    assert src._next_at is not None and src._next_at > NOW


def test_source_fires_when_due_with_open_topic(tmp_path):
    store = FakeStateStore()
    pool = _pool_with_topics(tmp_path)
    src = TopicSource(store, pool)
    src.poll(NOW)
    src._next_at = NOW                            # 白盒：把到点时间拨到现在

    out = src.poll(NOW + timedelta(minutes=1))
    assert len(out) == 1
    sig = out[0]
    assert sig.source == "topic"
    assert sig.urgency < 0.4                      # 不比惦记更急
    assert sig.payload["topic_id"]                # 料在 payload 里，交给 deliver
    # 说完这次，下一次又是几小时后
    assert src._next_at > NOW + timedelta(hours=2)


def test_source_empty_pool_is_silent_and_backs_off(tmp_path):
    """空池子：不产出念头（不进账本），一小时后再看，不是几小时。"""
    store = FakeStateStore()
    src = TopicSource(store, TopicPool(tmp_path / "empty.db"))
    src._next_at = NOW

    out = src.poll(NOW)
    assert out == []
    assert NOW < src._next_at <= NOW + timedelta(hours=2)


def test_source_skips_surfaced_topics(tmp_path):
    """推过/聊过（surfaced）的不二次提起 —— §4.1 的不循环规矩。"""
    pool = _pool_with_topics(tmp_path)
    pool.store.mark_surfaced([pool.store.open_topics(NOW)[0].id])
    src = TopicSource(FakeStateStore(), pool)
    src._next_at = NOW

    assert src.poll(NOW) == []                    # 只剩 surfaced 了 → 同空池


def test_source_without_pool_is_silent():
    assert TopicSource(FakeStateStore(), None).poll(NOW) == []


# ---------------------------------------------------------------- 决策链


def _orchestrator(deliver, *, gate: str = "", opened_recently: bool = False):
    threads = ThreadBook()
    if opened_recently:
        threads.open("company", "刚聊过的", now=NOW - timedelta(minutes=30))
    return CareOrchestrator(
        threads, deliver,
        policies={"topic": SourcePolicy(takes_quota=True, takes_gate=True,
                                        max_steps=1)},
        gate_check=(lambda now: gate),
        quota_cooldown_min=60,
    )


def test_orchestrator_lets_topic_speak_and_ledgers_it(tmp_path):
    said = []
    care = _orchestrator(lambda s, t, now: said.append(s.payload) or "mid-1")
    src = TopicSource(FakeStateStore(), _pool_with_topics(tmp_path))
    src._next_at = NOW

    care.submit_all(src.poll(NOW))
    out = care.run(NOW)

    assert [o.action for o in out] == ["spoke"]
    assert said and said[0]["topic_id"]           # 料真的递到了 deliver 手里
    assert care.ledger.summary(NOW)["spoke"] == 1  # 一本账：topic 的口也算数


def test_orchestrator_blocks_topic_at_quiet_hours(tmp_path):
    """吃 DailyGate：安静时段拦下，账本记 BLOCK，料原封不动。"""
    care = _orchestrator(lambda s, t, now: pytest.fail("不该开口"), gate="安静时段")
    src = TopicSource(FakeStateStore(), _pool_for_ledger(tmp_path))
    src._next_at = NOW

    care.submit_all(src.poll(NOW))
    out = care.run(NOW)

    assert out[0].action == "dropped" and "安静时段" in out[0].reason
    assert care.ledger.summary(NOW)["blocked"] == 1


def test_orchestrator_topic_yields_to_quota(tmp_path):
    """一小时一条新链的额度是共用的：惦记刚开过链，topic 就让路。"""
    care = _orchestrator(lambda s, t, now: pytest.fail("不该开口"),
                         opened_recently=True)
    src = TopicSource(FakeStateStore(), _pool_for_ledger(tmp_path))
    src._next_at = NOW

    care.submit_all(src.poll(NOW))
    out = care.run(NOW)

    assert out[0].action == "dropped" and "链" in out[0].reason


def _pool_for_ledger(tmp_path) -> TopicPool:
    pool = TopicPool(tmp_path / "ledger-topics.db")
    pool.store.add_topic(Topic(hook="切口", source_url="u"), dedup_key="u")
    return pool


# ---------------------------------------------------------------- surfaced


def _service(tmp_path, speaker, topics):
    astore = AttentionStore(tmp_path / "attn.db")

    class P:
        def get_state(self, turn=None, force_refresh=False):
            return {"has_data": False}

    try:
        svc = AttentionService(astore, P(), RelationshipState(),
                               speaker=speaker, topics=topics)
        yield svc
    finally:
        astore.close()


def test_speak_topic_marks_surfaced_only_after_speaking(tmp_path):
    pool = _pool_with_topics(tmp_path)
    tid = pool.store.open_topics(NOW)[0].id

    svc = next(_service(tmp_path, FakeSpeaker("聊了"), pool))
    thread = svc.threads.open("company", "s", now=NOW)
    assert svc._speak_topic(_topic_signal(tid), thread, NOW) is True
    # open 里没了 = surfaced 记上了（默认的 open_topics 含 surfaced，要显式排除）
    assert pool.store.open_topics(NOW, include_surfaced=False) == []


def test_speak_topic_skip_leaves_topic_open(tmp_path):
    """他 [SKIP] 了：topic 还是 open，以后还有机会 —— 不算被选用。"""
    pool = _pool_with_topics(tmp_path)
    tid = pool.store.open_topics(NOW)[0].id

    svc = next(_service(tmp_path, FakeSpeaker(None), pool))
    thread = svc.threads.open("company", "s", now=NOW)
    assert svc._speak_topic(_topic_signal(tid), thread, NOW) is False
    assert len(pool.store.open_topics(NOW)) == 1


def test_speak_topic_dry_run_marks_nothing(tmp_path):
    """dry-run 什么都没发生，自然也不许记账。"""
    pool = _pool_with_topics(tmp_path)
    tid = pool.store.open_topics(NOW)[0].id

    svc = next(_service(tmp_path, None, pool))    # speaker None = dry-run
    thread = svc.threads.open("company", "s", now=NOW)
    assert svc._speak_topic(_topic_signal(tid), thread, NOW) is True
    assert len(pool.store.open_topics(NOW)) == 1


def test_service_registers_topic_policy(tmp_path):
    svc = next(_service(tmp_path, FakeSpeaker(), None))
    policy = svc.care.policies["topic"]
    assert policy.takes_gate and policy.takes_quota and policy.max_steps == 1


# ---------------------------------------------------------------- 工具


def test_browse_lists_open_topics(tmp_path):
    pool = _pool_with_topics(tmp_path, n=2)
    browse = topic_tool.make_handlers(pool)["topics_browse"]

    out = browse({"limit": 1})
    assert "topic_" in out and "[ai]" in out
    assert out.count("· id=") == 1                # limit 生效


def test_browse_is_read_only(tmp_path):
    """翻过不算看过 —— open 还是 open。"""
    pool = _pool_with_topics(tmp_path)
    browse = topic_tool.make_handlers(pool)["topics_browse"]
    browse({})
    assert len(pool.store.open_topics(NOW)) == 1


def test_browse_empty_and_disabled(tmp_path):
    empty = topic_tool.make_handlers(TopicPool(tmp_path / "e.db"))["topics_browse"]
    assert "空" in empty({})
    assert "没启用" in topic_tool.make_handlers(None)["topics_browse"]({})
