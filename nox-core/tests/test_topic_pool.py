"""Topic Pool 的测试（Topic_Pool_实事话题池链路.md）。

守的是几件容易做砸的事：

1. **禁止编造来源** —— Filter 返回的 source_id 对不上候选就扔，
   title / url 用我们抓的那份回填，模型说什么都不采信
2. **dedup** —— 同一来源 / 同一场经历只进池子一次；重跑一轮不翻倍
3. **过期是状态不是删除** —— expired 不再出现在池子里，followed 不被碰
4. **空是正常产出** —— 没好内容、adapter 没配、某个源挂了，都不是故障
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from topic_pool import scout  # noqa: E402
from topic_pool.filter import _extract_array, run_filter  # noqa: E402
from topic_pool.pool import TopicPool  # noqa: E402
from topic_pool.store import (  # noqa: E402
    SHARED_TTL_HOURS,
    Candidate,
    Topic,
    TopicStore,
    ttl_for,
)
from world_model import WorldModel  # noqa: E402

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = TopicStore(tmp_path / "topics.db")
    yield s
    s.close()


@pytest.fixture
def world(tmp_path):
    w = WorldModel(tmp_path / "world.db")
    yield w
    w.store.close()


def _cand(source_id: str = "hn:1", url: str = "https://example.com/a",
          category: str = "ai") -> Candidate:
    return Candidate(source_id=source_id, title="标题", url=url,
                     source="hackernews", category=category,
                     summary="摘要", published_at=NOW - timedelta(hours=2))


def _topic(hook: str = "一句切口", **kw: Any) -> Topic:
    # 🔴 observed_at 锚到测试的冻结时钟 NOW——默认真实时钟的话，
    # expires_at 会随真实日期漂移，NOW+8d 的过期判定迟早失效
    #（2026-09-07 起真的炸了：真实时钟越过判定线，expire 返回 0。
    #  同 project_shared 那次「必须把 now 传进 query」的坑）
    kw.setdefault("observed_at", NOW)
    return Topic(hook=hook, category=kw.pop("category", "ai"), **kw)


# ---------------------------------------------------------------- store


def test_topic_dedup_by_key(store):
    assert store.add_topic(_topic(), dedup_key="url:aaa") is True
    assert store.add_topic(_topic(hook="换个说法"), dedup_key="url:aaa") is False


def test_open_topics_hide_expired_and_handled(store):
    store.add_topic(_topic(hook="新鲜的"), dedup_key="u1")
    old = _topic(hook="到点的", category="ai")
    old.expires_at = NOW - timedelta(hours=1)
    store.add_topic(old, dedup_key="u2")
    follow = _topic(hook="追了的")
    store.add_topic(follow, dedup_key="u3")
    store.mark(follow.id, "followed")

    items = store.open_topics(NOW)
    assert [t.hook for t in items] == ["新鲜的"]


def test_expire_only_touches_open_and_surfaced(store):
    t1 = _topic(hook="会过期的")
    store.add_topic(t1, dedup_key="e1")
    t2 = _topic(hook="追了不动的")
    store.add_topic(t2, dedup_key="e2")
    store.mark(t2.id, "followed")

    n = store.expire(NOW + timedelta(days=8))
    assert n >= 1
    assert store.mark(t1.id, "followed") is False      # 已 expired，人工决策进不去
    # followed 是终态：expire 不碰它，也不能再 dismiss（想改主意是 UI 层的事）
    assert store.stats().get("followed") == 1


def test_mark_rejects_bad_status_and_repeats(store):
    store.add_topic(_topic(), dedup_key="m1")
    with pytest.raises(ValueError):
        store.mark(store.open_topics(NOW)[0].id, "surfaced")
    tid = store.open_topics(NOW)[0].id
    assert store.mark(tid, "followed") is True
    assert store.mark(tid, "dismissed") is False       # 已是终态


def test_candidates_dedup_and_prune(store):
    assert store.add_candidate(_cand()) is True
    assert store.add_candidate(_cand()) is False       # source_id 撞了
    store.add_candidate(_cand(source_id="hn:2"))
    assert len(store.recent_candidates(timedelta(hours=1))) == 2
    # 缓存天生短命：48 小时前的全清
    old = _cand(source_id="hn:old")
    old.fetched_at = NOW - timedelta(hours=72)
    store.add_candidate(old)
    assert store.prune_candidates(timedelta(hours=48)) == 1
    assert len(store.recent_candidates(timedelta(hours=48))) == 2


def test_ttl_by_category():
    assert ttl_for("science") > ttl_for("ai")          # 论文比新闻耐放
    assert ttl_for("不认识的方向").total_seconds() > 0  # 没配的也有默认
    assert SHARED_TTL_HOURS == 48


def test_store_used_from_another_thread(tmp_path):
    """线上头一轮就栽在这：连接主线程建、Scout 在 to_thread 里用。

    世界模型的方子（check_same_thread=False + 锁）必须原样带着，
    少一样就是 sqlite3.ProgrammingError。
    """
    import threading
    s = TopicStore(tmp_path / "topics.db")
    results: list[str] = []

    def work():
        try:
            s.add_topic(_topic(hook="工人线程写的"), dedup_key="t-thread")
            found = len(s.open_topics(NOW))
            results.append("ok" if found == 1 else f"bad:{found}")
        except Exception as exc:  # noqa: BLE001
            results.append(f"raise:{type(exc).__name__}")

    t = threading.Thread(target=work)
    t.start()
    t.join()
    s.close()
    assert results == ["ok"]


# ---------------------------------------------------------------- scout 解析


def test_hn_parse(monkeypatch):
    monkeypatch.setattr(scout, "_fetch_json", lambda url: {"hits": [
        {"objectID": "42", "title": "一个好东西", "url": "https://x.com",
         "points": 88, "created_at": "2026-08-30T10:00:00Z"},
        {"objectID": "43", "title": "没外链的讨论", "url": None, "points": 30},
    ]})
    out = scout.hn("AI")
    assert [c.source_id for c in out] == ["hn:42", "hn:43"]
    assert out[0].url == "https://x.com"
    # 没外链的落回 HN 自己的讨论页，不能是空 url
    assert out[1].url == "https://news.ycombinator.com/item?id=43"


def test_github_parse(monkeypatch):
    monkeypatch.setattr(scout, "_fetch_json", lambda url: {"items": [
        {"full_name": "someone/neat-tool", "html_url": "https://github.com/someone/neat-tool",
         "description": "很小但妙", "created_at": "2026-08-29T00:00:00Z"},
    ]})
    out = scout.github_repos()
    assert out[0].source_id == "gh:someone/neat-tool"
    assert out[0].summary == "很小但妙"


def test_arxiv_parse(monkeypatch):
    xml = """<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2401.12345v2</id>
        <title>  A  quiet  paper  </title>
        <summary>关于孤独的数学。</summary>
        <published>2026-08-28T00:00:00Z</published>
      </entry>
    </feed>"""
    import xml.etree.ElementTree as ET
    monkeypatch.setattr(scout, "_fetch_xml", lambda url: ET.fromstring(xml))
    out = scout.arxiv()
    assert out[0].source_id == "arxiv:2401.12345"
    assert out[0].title == "A quiet paper"


def test_gnews_parse(monkeypatch):
    xml = """<rss><channel>
      <item><title>一本新书</title><link>https://news.example/1</link>
        <pubDate>Sun, 30 Aug 2026 08:00:00 GMT</pubDate></item>
      <item><title>没链接的不算</title><link></link></item>
    </channel></rss>"""
    import xml.etree.ElementTree as ET
    monkeypatch.setattr(scout, "_fetch_xml", lambda url: ET.fromstring(xml))
    out = scout.gnews("新书")
    assert len(out) == 1
    assert out[0].source_id.startswith("gnews:")
    assert out[0].published_at is not None


def test_fetch_direction_stamps_category(monkeypatch):
    monkeypatch.setattr(scout, "gnews", lambda q, when="3d": [
        Candidate(source_id="gnews:x", title="t", url="u",
                  source="googlenews", category="")])
    out = scout.fetch_direction("film")
    assert out and out[0].category == "film"


def test_fetch_direction_survives_a_broken_fetcher(monkeypatch):
    def boom():
        raise RuntimeError("挂了")
    monkeypatch.setattr(scout, "DIRECTIONS",
                        {"film": [boom, lambda: [_cand(category="")]]})
    out = scout.fetch_direction("film")
    assert len(out) == 1                                # 一个挂了不连累另一个


# ---------------------------------------------------------------- 世界钩子


def _observe_reading(world, title="《xxx》", **kw):
    world.observe(source="co-reading", type="reading_progress",
                  observed={"book_id": "b1", "title": title, "progress": 43,
                            "complete": False, **kw},
                  observed_at=NOW - timedelta(hours=1),
                  dedup_key="reading/b1/2026-08-31T11:00:00+00:00")


def test_world_hook_fresh_reading_drives_fetch(monkeypatch, world):
    _observe_reading(world)
    seen: list[str] = []

    def fake_gnews(q, when="3d"):
        seen.append(q)
        return [_cand(source_id="gnews:hook", category="")]

    monkeypatch.setattr(scout, "gnews", fake_gnews)
    out, hooks = scout.fetch_world_hooks(world, NOW)

    assert hooks == 1 and len(out) == 1
    assert "书评" in seen[0]
    assert out[0].summary.startswith("【因为糖糖正在读《《xxx》》】") or \
        out[0].summary.startswith("【因为糖糖正在读")
    assert out[0].category == "books"


def test_world_hook_stale_state_does_not_fetch(monkeypatch, world):
    # 5 天前的进度：reading TTL 4 天，早 stale 了，不该再追着抓书评
    world.observe(source="co-reading", type="reading_progress",
                  observed={"book_id": "b1", "title": "《xxx》", "progress": 43},
                  observed_at=NOW - timedelta(days=5),
                  dedup_key="reading/b1/old")
    monkeypatch.setattr(scout, "gnews",
                        lambda q, when="3d": (_ for _ in ()).throw(AssertionError("不该抓")))
    out, hooks = scout.fetch_world_hooks(world, NOW)
    assert out == [] and hooks == 0


# ---------------------------------------------------------------- filter


class FakeTurn:
    def __init__(self, text: str, stop_reason: str = "end"):
        self.text = text
        self.stop_reason = stop_reason


class FakeAdapter:
    def __init__(self, payload: Any):
        self.payload = payload
        self.calls = 0

    def complete(self, msgs, tools, **kw) -> FakeTurn:
        self.calls += 1
        text = self.payload if isinstance(self.payload, str) else json.dumps(
            self.payload, ensure_ascii=False)
        return FakeTurn(text)


def test_extract_array_strips_fences():
    assert _extract_array('```json\n[{"a":1}]\n```') == [{"a": 1}]
    assert _extract_array('前言 [{"a":1}] 后记') == [{"a": 1}]
    assert _extract_array("不是数组") is None


def test_filter_drops_fabricated_sources():
    cands = [_cand("hn:1", "https://x/1"), _cand("hn:2", "https://x/2", "film")]
    payload = [
        {"source_id": "hn:1", "hook": "好切口", "category": "ai",
         "why_this": ["因为她最近在做"], "relevance": 0.9},
        {"source_id": "hn:编的", "hook": "编来的", "category": "ai"},   # 死在这
        {"source_id": "hn:2", "hook": "没有 category 的照抄候选", "category": "乱写"},
    ]
    out = run_filter(FakeAdapter(payload), cands, NOW)
    assert len(out) == 2
    assert out[0]["relevance"] == 0.9
    # category 乱写的落回候选自己的方向
    assert out[1]["category"] == "film"


def test_filter_caps_at_three_and_handles_junk():
    cands = [_cand(f"hn:{i}") for i in range(5)]
    payload = [{"source_id": f"hn:{i}", "hook": f"切口{i}", "category": "ai"}
               for i in range(5)]
    out = run_filter(FakeAdapter(payload), cands, NOW)
    assert len(out) == 3                                 # 文档规则 7

    assert run_filter(FakeAdapter("这根本不是 JSON"), cands, NOW) == []
    assert run_filter(None, cands, NOW) == []            # utility 没配


# ---------------------------------------------------------------- 池子


def test_project_shared_from_world(world, tmp_path):
    pool = TopicPool(tmp_path / "topics.db", world=world)
    world.observe(source="bridge", type="watching_session",
                  observed={"session_id": "w1", "title": "寂寞拍卖师",
                            "episode": "", "minutes": 90, "finished": True},
                  observed_at=NOW - timedelta(hours=5),
                  dedup_key="watching/w1")

    assert pool.project_shared(NOW) == 1
    items = pool.topics_for_ui(NOW)
    assert len(items) == 1
    assert items[0]["origin"] == "shared"
    assert items[0]["category"] == "film"
    assert "还没聊过" in items[0]["hook"]
    # 同一场经历不投第二遍
    assert pool.project_shared(NOW) == 0


def test_project_shared_ignores_unfinished_and_old(world, tmp_path):
    pool = TopicPool(tmp_path / "topics.db", world=world)
    world.observe(source="co-reading", type="reading_progress",
                  observed={"book_id": "b1", "title": "《进行中》", "progress": 43},
                  observed_at=NOW - timedelta(hours=1), dedup_key="reading/b1/new")

    assert pool.project_shared(NOW) == 0                 # 没读完的不算枝条


def test_run_cycle_end_to_end(monkeypatch, world, tmp_path):
    pool = TopicPool(tmp_path / "topics.db", world=world,
                     adapter=FakeAdapter([{
                         "source_id": "fake:1", "hook": "值得聊一句",
                         "category": "books", "why_this": ["测试"],
                         "relevance": 0.7}]))
    monkeypatch.setattr(scout, "fetch_direction", lambda cat: [
        _cand("fake:1", "https://x/fake", category=cat)])
    monkeypatch.setattr(scout, "fetch_world_hooks", lambda w, now: ([], 0))

    stats = pool.run_cycle(NOW)
    assert stats["external_topics"] == 1
    items = pool.topics_for_ui(NOW)
    assert items[0]["hook"] == "值得聊一句"
    assert items[0]["source_url"] == "https://x/fake"    # url 用我们抓的那份

    # 重跑一轮：同一来源不进第二遍，池子不翻倍
    stats2 = pool.run_cycle(NOW + timedelta(hours=6))
    assert stats2["external_topics"] == 0
    assert len(pool.topics_for_ui(NOW + timedelta(hours=6))) == 1


def test_old_article_still_born_fresh(monkeypatch, world, tmp_path):
    """发布两天前的旧文进池子，寿命从**进池子**算——不能生下来就过期。

    2026-08-31 线上头一轮三条全灭的复现：observed_at 拿了 pubDate，
    Google News 三天窗口里的旧文 + 48h TTL，本轮 expire 直接收走。
    """
    pool = TopicPool(tmp_path / "topics.db", world=world,
                     adapter=FakeAdapter([{
                         "source_id": "fake:old", "hook": "旧文但值得聊",
                         "category": "books", "why_this": [], "relevance": 0.8}]))
    old = _cand("fake:old", "https://x/old", category="books")
    old.published_at = NOW - timedelta(days=2)
    monkeypatch.setattr(scout, "fetch_direction", lambda cat: [old])
    monkeypatch.setattr(scout, "fetch_world_hooks", lambda w, now: ([], 0))

    pool.run_cycle(NOW)
    items = pool.topics_for_ui(NOW)
    assert len(items) == 1 and items[0]["fresh"] is True


def test_run_cycle_without_adapter_still_scouts(monkeypatch, world, tmp_path):
    """utility 没配：Filter 出空是正常产出，候选缓存照攒。"""
    pool = TopicPool(tmp_path / "topics.db", world=world, adapter=None)
    monkeypatch.setattr(scout, "fetch_direction", lambda cat: [
        _cand("fake:9", "https://x/9")])
    monkeypatch.setattr(scout, "fetch_world_hooks", lambda w, now: ([], 0))

    stats = pool.run_cycle(NOW)
    assert stats["external_topics"] == 0
    assert stats["new_candidates"] == 1
