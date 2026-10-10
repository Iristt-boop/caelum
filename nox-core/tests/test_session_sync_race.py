"""会话历史被成批重写（2026-10-10 搬家 C2 时查出来的）。

主会话 10617 行里只有 7130 行不重复：一轮对话拿着**开头的快照**跑 15~35 秒，
这期间主动消息 / [SKIP] 从另一条路先落了库；这一轮结束时用旧快照 + 新消息去 sync，
库里最后一条（那条主动消息）在旧快照里找不到 → 「宁可重复不要丢」整段重写。
10-07 起一批 180 多条，一天五批 —— 坏样板在他眼里出现 7 次，输入一轮 8.5 万 token。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message  # noqa: E402
from api.server import Sessions  # noqa: E402
from data.store import Store  # noqa: E402


def _m(role: str, text: str) -> Message:
    return Message(role=role, text=text)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def _texts(store: Store, sid: str) -> list[str]:
    return [m.text for m in store.load(sid, limit=1000)]


def test_stale_snapshot_does_not_rewrite_whole_history(store):
    base = [_m("user", f"她{i}") if i % 2 == 0 else _m("assistant", f"他{i}") for i in range(40)]
    store.sync("s", base)
    snapshot = list(base)                                   # 她这一轮开始时拿到的快照
    store.sync("s", base + [_m("user", "（系统提示：想她了）"), _m("assistant", "两小时没理我")])  # 期间主动消息落库
    written = store.sync("s", snapshot + [_m("user", "我回来了"), _m("assistant", "哼")])  # 她这一轮收尾
    assert written == 2
    assert store.count("s") == 44
    assert _texts(store, "s")[-2:] == ["我回来了", "哼"]


def test_snapshot_older_than_db_tail_window_still_anchors(store):
    """快照里最新的那条在库里，但不是库里最后几条 —— 照样只补新的。"""
    base = [_m("user", "早"), _m("assistant", "早安")]
    store.sync("s", base)
    store.sync("s", base + [_m("assistant", f"主动{i}") for i in range(5)])
    assert store.sync("s", base + [_m("user", "在"), _m("assistant", "在呢")]) == 2
    assert store.count("s") == 9


def test_repeated_words_in_this_turn_are_not_swallowed(store):
    """快照过期 + 她这轮说的和最近一模一样（又问「在吗」）：这轮的新话照写，不当成已存过。"""
    base = [_m("user", "在吗"), _m("assistant", "在呢")]
    store.sync("s", base)
    store.sync("s", base + [_m("user", "（系统提示）"), _m("assistant", "想你了")])
    assert store.sync("s", base + [_m("user", "在吗"), _m("assistant", "在呢")]) == 2
    assert _texts(store, "s")[-2:] == ["在吗", "在呢"]


def test_identical_reply_next_turn_is_still_stored(store):
    """他连着两轮回一模一样的话（[SKIP] 最常见）：原来锚点先撞上这一轮的新回复，整轮不落库。"""
    h = [_m("user", "（系统提示一）"), _m("assistant", "[SKIP]")]
    store.sync("s", h)
    assert store.sync("s", h + [_m("user", "（系统提示二）"), _m("assistant", "[SKIP]")]) == 2
    assert store.count("s") == 4


def test_exact_repeat_of_previous_turn_is_stored(store):
    """她又问「在吗」、他又回「在呢」，和上一轮一字不差 —— 是新话，不是重复 sync。"""
    h = [_m("user", "早"), _m("assistant", "早安"), _m("user", "在吗"), _m("assistant", "在呢")]
    store.sync("s", h)
    assert store.sync("s", h + [_m("user", "在吗"), _m("assistant", "在呢")]) == 2
    assert store.count("s") == 6


def test_turn_synced_in_two_steps_only_writes_the_rest(store):
    h = [_m("user", "早"), _m("assistant", "早安")]
    store.sync("s", h)
    store.sync("s", h + [_m("user", "去吃饭了")])                         # 先落了她那条
    assert store.sync("s", h + [_m("user", "去吃饭了"), _m("assistant", "去吧")]) == 1
    assert _texts(store, "s")[-2:] == ["去吃饭了", "去吧"]


def test_genuinely_unknown_history_still_appends(store):
    """快照里一条都不在库里（真不该发生）：还是宁可多写不丢。"""
    store.sync("s", [_m("user", "a"), _m("assistant", "b")])
    assert store.sync("s", [_m("user", "x"), _m("assistant", "y")]) == 2


def test_date_stamped_window_keeps_cache(tmp_path):
    """从库里读回的历史，当天第一条带「（10月10日 周六）」—— 正常一轮不能被当成过期、白扔缓存。"""
    s1 = Store(tmp_path / "t.db")
    s1.sync("s", [_m("user", "早"), _m("assistant", "早安")])
    s1.close()
    s2 = Store(tmp_path / "t.db")                       # 重启：缓存空，get 从库里读、带日期戳
    ss = Sessions(s2, history_limit=200)
    h = ss.get("s")
    assert h[0].text.startswith("（")                    # 确认确实带了戳，这条测试才有意义
    ss.put("s", h + [_m("user", "在吗"), _m("assistant", "在呢")])
    assert "s" in ss._cache
    assert s2.count("s") == 4
    s2.close()


def test_sessions_cache_drops_stale_window(store):
    """快照过期时内存缓存不能留着旧窗口 —— 不然他看不见那条主动消息。"""
    ss = Sessions(store, history_limit=200)
    sid = "s"
    ss.put(sid, [_m("user", "早"), _m("assistant", "早安")])
    snapshot = ss.get(sid)
    ss.put(sid, snapshot + [_m("user", "（系统提示）"), _m("assistant", "两小时没理我")])   # 主动消息
    ss.put(sid, snapshot + [_m("user", "我回来了"), _m("assistant", "哼")])                 # 她那轮用旧快照
    seen = [m.text for m in ss.get(sid)]
    assert "两小时没理我" in seen and seen[-1] == "哼"
    assert seen.count("早安") == 1
