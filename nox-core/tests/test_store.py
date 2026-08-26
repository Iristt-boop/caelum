"""会话持久化测试。用临时库，不碰真实数据。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message, ToolCall, ToolResult  # noqa: E402
from data.store import Store  # noqa: E402


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def test_append_and_load(store):
    store.append("s1", [
        Message(role="user", text="在吗"),
        Message(role="assistant", text="在呢，糖糖"),
    ])
    got = store.load("s1")
    assert [m.role for m in got] == ["user", "assistant"]
    assert got[1].text == "在呢，糖糖"


def test_survives_reopen(tmp_path):
    """重启不丢 —— 这是整个模块存在的理由。"""
    path = tmp_path / "t.db"
    s1 = Store(path)
    s1.append("s1", [Message(role="user", text="记住这句")])
    s1.close()

    s2 = Store(path)          # 换个实例，模拟进程重启
    # 正文原样在，前面多一行日期分隔线（`context/timeline.py`）
    assert "记住这句" in s2.load("s1")[0].text
    s2.close()


def test_tool_messages_are_not_persisted(store):
    """工具调用是中间态，重放没有意义（结果早过期了），不该存。"""
    store.append("s1", [
        Message(role="user", text="床头灯什么状态"),
        Message(role="assistant", tool_calls=[ToolCall(id="c1", name="ha_get_state", arguments={})]),
        Message(role="tool_results", tool_results=[ToolResult(call_id="c1", content="on")]),
        Message(role="assistant", text="开着呢，亮度 10%"),
    ])
    got = store.load("s1")
    assert [m.role for m in got] == ["user", "assistant"]
    assert got[1].text == "开着呢，亮度 10%"


def test_empty_text_skipped(store):
    assert store.append("s1", [Message(role="assistant", text="   ")]) == 0
    assert store.load("s1") == []


def test_sync_only_writes_the_tail(store):
    """history 是累积的，重复 sync 不能重复写。"""
    h = [Message(role="user", text="第一句"), Message(role="assistant", text="回第一句")]
    assert store.sync("s1", h) == 2
    assert store.sync("s1", h) == 0          # 同样的 history 再来一次，不该重写

    h += [Message(role="user", text="第二句"), Message(role="assistant", text="回第二句")]
    assert store.sync("s1", h) == 2          # 只写新增的两条
    assert store.count("s1") == 4


def test_load_limit_keeps_the_latest(store):
    store.append("s1", [Message(role="user", text=f"第{i}句") for i in range(10)])
    got = store.load("s1", limit=3)
    # 第一条前面带日期分隔线，所以比对用 in 不用 ==
    assert [m.text for m in got][0].endswith("第7句")
    assert [m.text for m in got][1:] == ["第8句", "第9句"]


def test_sync_still_writes_after_history_exceeds_load_limit(store):
    """会话超过 load limit 后，sync 必须继续落库（2026-08-14 修）。

    老实现按条数比对：`len(pending) <= count(sid)` 就 return 0。
    一旦会话超过 limit（默认 40），history 永远是被截断的 40 条 + 新增，
    长度永远 ≤ 库里总数 → 新消息静默丢失，Nox 停在旧世界。
    这是主会话 63 条时断更、Care/晨报/Attention 全都不记得的根因。
    """
    # 先造 45 条已存消息（超过默认 limit 40）
    h = [Message(role="user", text=f"旧{i}") for i in range(45)]
    assert store.sync("s1", h) == 45
    assert store.count("s1") == 45

    # 模拟真实调用方：load 只拿回最近 40 条（截断的）
    truncated = store.load("s1", limit=40)
    # 再加两轮新消息，拼成调用方手里的"累积 history"
    h2 = truncated + [Message(role="user", text="新1"), Message(role="assistant", text="回新1")]
    assert store.sync("s1", h2) == 2          # 老实现这里返回 0 —— 就是那个 bug
    assert store.count("s1") == 47

    # 再来一轮，继续能写
    h3 = store.load("s1", limit=40) + [Message(role="user", text="新2")]
    assert store.sync("s1", h3) == 1
    assert store.count("s1") == 48

    # 同样的 history 再来一次，不该重写
    assert store.sync("s1", h3) == 0


def test_sync_anchor_matches_by_role_and_text_from_tail(store):
    """锚点必须从尾部往前找、且 role 对上 —— 相同文本不能错位。"""
    h = [
        Message(role="user", text="在吗"),
        Message(role="assistant", text="在呢"),
        Message(role="user", text="在吗"),      # 和第一条同文本
        Message(role="assistant", text="还在呢"),
    ]
    assert store.sync("s1", h) == 4

    # 截断后加载，再加新消息 —— 锚点应命中最后那条 assistant「还在呢」
    truncated = store.load("s1", limit=40)
    h2 = truncated + [Message(role="user", text="新句")]
    assert store.sync("s1", h2) == 1
    assert store.count("s1") == 5


def test_sessions_are_isolated(store):
    store.append("a", [Message(role="user", text="A 的话")])
    store.append("b", [Message(role="user", text="B 的话")])
    assert "A 的话" in store.load("a")[0].text
    assert "B 的话" in store.load("b")[0].text
    assert "B 的话" not in store.load("a")[0].text


def test_drop(store):
    store.append("s1", [Message(role="user", text="x")])
    assert store.drop("s1") is True
    assert store.load("s1") == []
    assert store.drop("s1") is False


def test_recent_orders_by_update(store):
    store.append("old", [Message(role="user", text="旧的")])
    store.append("new", [Message(role="user", text="新的")])
    ids = [s.id for s in store.recent()]
    assert ids.index("new") < ids.index("old")


def test_stats(store):
    store.append("s1", [Message(role="user", text="x"), Message(role="assistant", text="y")])
    st = store.stats()
    assert st["sessions"] == 1 and st["messages"] == 2 and st["db_bytes"] > 0


def test_auto_title_from_first_user_message(store):
    """新会话第一次写入时，自动取第一条 user 消息做标题。"""
    store.append("s1", [
        Message(role="user", text="今天天气真好"),
        Message(role="assistant", text="是呢，阳光很好"),
    ])
    sessions = store.recent()
    assert sessions[0].title == "今天天气真好"


def test_auto_title_only_sets_once(store):
    """标题只在第一次设定，后续消息不覆盖。"""
    store.append("s1", [Message(role="user", text="第一句"), Message(role="assistant", text="回第一句")])
    store.append("s1", [Message(role="user", text="第二句"), Message(role="assistant", text="回第二句")])
    sessions = store.recent()
    assert sessions[0].title == "第一句"  # 不是"第二句"


def test_auto_title_truncates_at_40_chars(store):
    """标题超过 40 字时截断。"""
    long_msg = "这是一句很长很长很长很长很长很长很长很长的话用来测试截断功能"
    store.append("s1", [Message(role="user", text=long_msg)])
    sessions = store.recent()
    assert len(sessions[0].title) <= 40
    assert sessions[0].title == long_msg[:40]


def test_set_title_does_not_overwrite_auto_title(store):
    """显式 set_title 先到先得，不会被 _ensure_title 覆盖。"""
    store.append("s1", [Message(role="user", text="用户说的第一句")])
    store.set_title("s1", "自定义标题")
    # 再追加一轮 —— _ensure_title 看到已有标题就该跳过
    store.append("s1", [Message(role="user", text="第二句")])
    sessions = store.recent()
    assert sessions[0].title == "自定义标题"


def test_recent_clean_only_filters_impure_ids(store):
    """clean_only 只列出 32 位纯 hex 的 session id。"""
    store.append("a" * 32, [Message(role="user", text="真会话")])
    store.append("test-something", [Message(role="user", text="测试")])
    store.append("short", [Message(role="user", text="太短")])
    store.append("cli", [Message(role="user", text="命令行")])

    # 不加过滤时全出来
    all_sessions = store.recent(clean_only=False)
    assert len(all_sessions) == 4

    # 加过滤后只剩 32 位纯 hex
    clean = store.recent(clean_only=True)
    assert len(clean) == 1
    assert clean[0].id == "a" * 32


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
