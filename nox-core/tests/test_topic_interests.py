"""话题池跟着她的兴趣走（10-23，设计稿第五节）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from topic_pool import interests as I
from topic_pool import scout
from topic_pool.pool import TopicPool
from tests.test_topic_pool import FakeAdapter, _cand

NOW = datetime(2026, 10, 23, 4, 0, tzinfo=timezone.utc)
GOOD = [{"query": "底特律 变人", "category": "weird", "why": "因为糖糖最爱《底特律：变人》"},
        {"query": "尼采", "category": "books", "why": "她在读查拉图斯特拉"},
        {"query": "这是一整句太长了不像搜索词的东西啊", "category": "books", "why": "x"},
        {"query": "力量训练", "category": "随便", "why": "因为糖糖在减脂"}]


def test_解析_词太长丢掉_why补前缀_分类不认识归weird():
    got = I._parse(json.dumps(GOOD, ensure_ascii=False))
    assert [g["query"] for g in got] == ["底特律 变人", "尼采", "力量训练"]
    assert got[1]["why"] == "因为糖糖她在读查拉图斯特拉"
    assert got[2]["category"] == "weird"


def test_一天只问一次_跨天才重算_算不出沿用上次(tmp_path):
    ad = FakeAdapter(GOOD)
    it = I.Interests(tmp_path / "interests.json", ad, lambda: ("喜欢：\n- 底特律", ["减脂"]))
    assert len(it.current(NOW)) == 3 and ad.calls == 1
    it.current(NOW + timedelta(hours=3))
    assert ad.calls == 1, "同一天不再问"
    ad.payload = "不是 JSON"
    assert len(it.current(NOW + timedelta(days=1))) == 3 and ad.calls == 2, "算不出来就沿用上次的"


def test_档案读不到_沿用上次的不炸(tmp_path):
    (tmp_path / "interests.json").write_text(json.dumps({"day": "2026-10-01", "items": GOOD[:1]}, ensure_ascii=False), encoding="utf-8")

    def boom():
        raise RuntimeError("bridge 不在家")
    it = I.Interests(tmp_path / "interests.json", FakeAdapter(GOOD), boom)
    assert it.current(NOW) == GOOD[:1]


def test_提示里不让搜她的隐私():
    assert "不要她的隐私" in I._PROMPT


def test_一轮_她的两个方向加一个固定方向_候选带因为糖糖(monkeypatch, tmp_path):
    seen_dirs, seen_q = [], []
    monkeypatch.setattr(scout, "fetch_direction", lambda cat: seen_dirs.append(cat) or [])
    monkeypatch.setattr(scout, "fetch_world_hooks", lambda w, now: ([], 0))

    def fake_gnews(q, when="3d"):
        seen_q.append((q, when))
        return [_cand(f"g:{q}", f"https://x/{len(seen_q)}")]
    monkeypatch.setattr(scout, "gnews", fake_gnews)
    filt = FakeAdapter([])
    picked = [{"query": "底特律 变人", "category": "weird", "why": "因为糖糖最爱《底特律：变人》"},
              {"query": "尼采", "category": "books", "why": "因为糖糖在读尼采"}]
    pool = TopicPool(tmp_path / "t.db", adapter=filt,
                     interests=type("P", (), {"pick": lambda self, now: picked})())
    stats = pool.run_cycle(NOW)
    assert stats["interests"] == ["底特律 变人", "尼采"] and len(seen_dirs) == 1, "固定方向只留一个"
    assert seen_q == [("底特律 变人", "7d"), ("尼采", "7d")]
    cands = pool.store.recent_candidates(timedelta(hours=1))
    assert any(c.summary.startswith("【因为糖糖最爱《底特律：变人》】") and c.category == "weird" for c in cands)


def test_没有兴趣_退回老样子(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(scout, "fetch_direction", lambda cat: seen.append(cat) or [])
    monkeypatch.setattr(scout, "fetch_world_hooks", lambda w, now: ([], 0))
    TopicPool(tmp_path / "t.db", adapter=None).run_cycle(NOW)
    assert len(seen) in (2, 3)
