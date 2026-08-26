"""Sessions 层集成：summary 在 get/put 循环中不双份、不丢失。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message  # noqa: E402
from api.server import Sessions  # noqa: E402
from context.compactor import SUMMARY_HEADER  # noqa: E402
from data.store import Store  # noqa: E402


@pytest.fixture
def sessions(tmp_path):
    s = Store(tmp_path / "t.db")
    yield Sessions(s, history_limit=40, recent_window_tokens=8000)
    s.close()


def test_get_put_roundtrip_with_summary(sessions):
    """put 后 get：summary 叠在最前，且不双份。"""
    sessions.put("s1", [
        Message(role="user", text="早"),
        Message(role="assistant", text="早啊"),
    ])
    # 加 summary
    sessions._store.set_summary("s1", "Topic: 打招呼")
    got = sessions.get("s1")
    system_msgs = [m for m in got if m.role == "system"]
    assert len(system_msgs) == 1
    assert system_msgs[0].text == SUMMARY_HEADER + "Topic: 打招呼"


def test_put_strips_system_so_summary_not_duplicated(sessions):
    """put 时剥离 system —— 否则 loop 带回来的 summary 会存进缓存，下次 get 双份。"""
    sessions.put("s1", [
        Message(role="user", text="早"),
        Message(role="assistant", text="早啊"),
    ])
    sessions._store.set_summary("s1", "Topic: 打招呼")

    # 模拟 loop 返回：带着 summary 的完整 history（get 后传给 loop，loop 原样带回）
    got = sessions.get("s1")
    sessions.put("s1", [
        *got,
        Message(role="user", text="第二句"),
        Message(role="assistant", text="在呢"),
    ])

    # 缓存里不应有 system（summary 读时合成）
    cached = sessions._cache["s1"]
    assert not any(m.role == "system" for m in cached)

    # 再 get：仍然只有一份 summary
    got2 = sessions.get("s1")
    system_msgs = [m for m in got2 if m.role == "system"]
    assert len(system_msgs) == 1


def test_get_cached_hits_also_attach_summary(sessions):
    """缓存命中时也要叠 summary（压缩更新后立刻生效）。"""
    sessions.put("s1", [Message(role="user", text="早"), Message(role="assistant", text="早啊")])
    # 第一次 get（缓存冷，走 load）
    first = sessions.get("s1")
    assert not any(m.role == "system" for m in first)  # 还没 summary

    # 压缩写入 summary
    sessions._store.set_summary("s1", "Topic: 打招呼")
    # 第二次 get（缓存命中）—— 必须带上新 summary
    second = sessions.get("s1")
    assert any(m.role == "system" and "打招呼" in m.text for m in second)


def test_recent_window_tokens_limits_history(sessions):
    """token 预算窗口：历史超过预算时只留最近。"""
    for i in range(30):
        sessions.put("s1", [
            Message(role="user", text=f"这是一条比较长的消息内容用来占预算{i}" * 3),
            Message(role="assistant", text=f"回复也长一点{i}" * 3),
        ])
    # 预算 2000 token 只装得下几条
    got = sessions.get("s1")
    assert len(got) < 60  # 全部是 60 条，token 窗口会砍掉大部分
    # 最后一条必在
    assert any("29" in (m.text or "") for m in got)
