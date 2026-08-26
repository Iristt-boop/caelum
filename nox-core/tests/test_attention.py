"""Attention M1 测试：契约、衰减、落盘。

用临时库，不碰真实数据（`never-test-in-tangtang-prod`）。
时间一律显式传 `now`，不依赖真实时钟 —— 否则衰减相关的断言会随机飘。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.events import ExperienceEvent  # noqa: E402
from attention.registry import (  # noqa: E402
    FLOOR,
    MAX_EVIDENCE,
    Attention,
    AttentionRegistry,
)
from attention.store import AttentionStore  # noqa: E402

T0 = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)


def _evt(**kw) -> ExperienceEvent:
    base = dict(
        source="health",
        type="sleep_quality_changed",
        subtype="poor_sleep",
        payload={"hours": 5.2, "quality": "poor"},
        timestamp=T0,
    )
    base.update(kw)
    return ExperienceEvent(**base)


# ---------------------------------------------------------------- 事件契约


def test_event_roundtrip():
    """契约是「贵」的那一层，序列化必须无损。"""
    e = _evt()
    back = ExperienceEvent.from_dict(e.to_dict())
    assert back == e


def test_event_rejects_empty_source_and_type():
    with pytest.raises(ValueError):
        ExperienceEvent(source="", type="x")
    with pytest.raises(ValueError):
        ExperienceEvent(source="health", type="")


def test_event_rejects_naive_timestamp():
    """不带时区的时间存进库再读出来就分不清 UTC 还是本地时间，
    而衰减和冷却全靠时间差算 —— 必须在入口挡掉。"""
    with pytest.raises(ValueError, match="时区"):
        ExperienceEvent(source="health", type="x", timestamp=datetime(2026, 8, 8, 12, 0))


def test_event_is_frozen():
    """事件是已经发生的事实，下游不许改。"""
    e = _evt()
    with pytest.raises(Exception):
        e.payload = {}  # type: ignore[misc]


# ---------------------------------------------------------------- 衰减


def test_decay_halves_after_one_half_life():
    """slow 的半衰期是 7 天。"""
    a = Attention(subject="糖糖的睡眠", strength=1.0, last_updated=T0, decay="slow")
    assert a.current_strength(T0) == pytest.approx(1.0)
    assert a.current_strength(T0 + timedelta(days=7)) == pytest.approx(0.5)
    assert a.current_strength(T0 + timedelta(days=14)) == pytest.approx(0.25)


def test_decay_is_lazy_not_scheduled():
    """服务停了一个月再回来，读出来的强度也必须是对的 ——
    这正是不用定时任务扫表的理由。"""
    a = Attention(subject="x", strength=1.0, last_updated=T0, decay="slow")
    assert a.current_strength(T0 + timedelta(days=70)) < FLOOR
    assert not a.is_alive(T0 + timedelta(days=70))


# ---------------------------------------------------------------- Registry


def test_upsert_creates_then_strengthens():
    reg = AttentionRegistry()
    reg.upsert("糖糖的睡眠", 0.60, event=_evt(), now=T0)
    assert len(reg) == 1

    # 同一个 subject 再来一次，是加强不是新建 —— 否则 Scheduler
    # 会以为有两件事要说，就会说两次
    reg.upsert("糖糖的睡眠", 0.85, event=_evt(), now=T0)
    assert len(reg) == 1
    assert reg.get("糖糖的睡眠").strength == pytest.approx(0.85)


def test_upsert_takes_max_not_sum():
    """相加的话，一个每小时报一次的 Source 几天就能把强度顶到 1.0，
    而事情本身并没有变严重。"""
    reg = AttentionRegistry()
    for _ in range(10):
        reg.upsert("糖糖的睡眠", 0.30, now=T0)
    assert reg.get("糖糖的睡眠").strength == pytest.approx(0.30)


def test_upsert_compares_against_decayed_value():
    """三天前的 0.9 不该压住今天真实的 0.6。"""
    reg = AttentionRegistry()
    reg.upsert("糖糖的睡眠", 0.90, decay="normal", now=T0)   # normal = 2 天半衰期
    later = T0 + timedelta(days=4)                            # 衰减到约 0.225
    reg.upsert("糖糖的睡眠", 0.60, decay="normal", now=later)
    assert reg.get("糖糖的睡眠").strength == pytest.approx(0.60)


def test_since_is_preserved_across_strengthen():
    """「从 8 月 4 号就开始担心了」这件事本身有意义。"""
    reg = AttentionRegistry()
    reg.upsert("糖糖的睡眠", 0.60, now=T0)
    reg.upsert("糖糖的睡眠", 0.90, now=T0 + timedelta(days=3))
    assert reg.get("糖糖的睡眠").since == T0


def test_upsert_rejects_non_concern():
    """这一轮只做 concern，别让人顺手塞进来没人处理的 focus。"""
    reg = AttentionRegistry()
    with pytest.raises(ValueError, match="concern"):
        reg.upsert("三体", 0.8, kind="focus", now=T0)


def test_upsert_rejects_out_of_range_strength():
    reg = AttentionRegistry()
    with pytest.raises(ValueError):
        reg.upsert("x", 1.5, now=T0)


def test_evidence_is_capped():
    """不封顶的话，一个每天触发的 Source 半年能堆两百条。"""
    reg = AttentionRegistry()
    for i in range(MAX_EVIDENCE + 15):
        reg.upsert("糖糖的睡眠", 0.5, event=_evt(id=f"evt-{i}"), summary=f"第{i}次", now=T0)
    ev = reg.get("糖糖的睡眠").evidence
    assert len(ev) == MAX_EVIDENCE
    # 留下的是最近的，不是最早的
    assert ev[-1].event_id == f"evt-{MAX_EVIDENCE + 14}"


def test_weaken_and_prune():
    """M5 的 Feedback 会用 weaken —— 她忽略了就该淡下去。"""
    reg = AttentionRegistry()
    reg.upsert("糖糖的睡眠", 0.80, now=T0)
    reg.weaken("糖糖的睡眠", 0.5, now=T0)
    assert reg.get("糖糖的睡眠").strength == pytest.approx(0.40)

    reg.weaken("糖糖的睡眠", 0.01, now=T0)      # 压到地板以下
    assert reg.prune(now=T0) == ["糖糖的睡眠"]
    assert len(reg) == 0


def test_list_sorts_by_current_strength():
    """排序要按衰减后的值，不是存进去的原始值。"""
    reg = AttentionRegistry()
    reg.upsert("旧的强关心", 0.90, decay="fast", now=T0)      # fast = 6 小时半衰期
    reg.upsert("新的弱关心", 0.50, decay="slow", now=T0 + timedelta(days=1))
    got = [a.subject for a in reg.list(now=T0 + timedelta(days=1))]
    assert got[0] == "新的弱关心"


# ---------------------------------------------------------------- 落盘


def test_store_roundtrip(tmp_path):
    store = AttentionStore(tmp_path / "attn.db")
    reg = AttentionRegistry()
    reg.upsert("糖糖的睡眠", 0.85, event=_evt(), summary="昨晚只睡 5.2 小时", now=T0)
    assert store.save(reg) == 1

    back = store.load()
    a = back.get("糖糖的睡眠")
    assert a is not None
    assert a.strength == pytest.approx(0.85)
    assert a.since == T0
    assert a.evidence[0].summary == "昨晚只睡 5.2 小时"
    store.close()


def test_survives_reopen(tmp_path):
    """整个 store 模块存在的理由：nox-core 会重启，
    重启之后 Nox 不能把关心了四天的事情忘光。"""
    path = tmp_path / "attn.db"
    s1 = AttentionStore(path)
    reg = AttentionRegistry()
    reg.upsert("糖糖的睡眠", 0.72, now=T0)
    s1.save(reg)
    s1.close()

    s2 = AttentionStore(path)          # 换个实例，模拟进程重启
    assert s2.load().get("糖糖的睡眠").strength == pytest.approx(0.72)
    s2.close()


def test_save_removes_dropped_subjects(tmp_path):
    """全量覆盖的意义：registry 里删掉的，库里也要消失。"""
    store = AttentionStore(tmp_path / "attn.db")
    reg = AttentionRegistry()
    reg.upsert("糖糖的睡眠", 0.80, now=T0)
    reg.upsert("糖糖的膝盖", 0.60, now=T0)
    store.save(reg)

    reg.drop("糖糖的膝盖")
    store.save(reg)

    back = store.load()
    assert "糖糖的睡眠" in back
    assert "糖糖的膝盖" not in back
    store.close()


def test_empty_db_is_not_an_error(tmp_path):
    """第一次启动是正常情况。"""
    store = AttentionStore(tmp_path / "attn.db")
    assert len(store.load()) == 0
    store.close()


def test_corrupt_row_is_skipped_not_fatal(tmp_path):
    """一条写坏了不该让整个 Registry 起不来 —— 那等于一次写坏就永久失忆。"""
    path = tmp_path / "attn.db"
    store = AttentionStore(path)
    reg = AttentionRegistry()
    reg.upsert("好的那条", 0.80, now=T0)
    store.save(reg)

    # 手动塞一条 evidence 不是合法 JSON 的行
    store._conn.execute(
        "INSERT INTO attentions(subject, kind, strength, since, last_updated, decay, evidence) "
        "VALUES('坏的那条', 'concern', 0.5, ?, ?, 'slow', '{不是 json')",
        (T0.isoformat(), T0.isoformat()),
    )
    store._conn.commit()

    back = store.load()
    assert "好的那条" in back
    assert "坏的那条" not in back
    store.close()
