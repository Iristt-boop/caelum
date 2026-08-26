"""MemoryProvider 测试。不打网络，OB 全用假的。

重点守两件事：
  1. **只读** —— 它绝不能调用任何写入类接口
  2. **没接进每轮名单** —— 接进去等于每轮 7 秒 + 缓存掉到 62.5%
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.base import Turn  # noqa: E402
from context.providers.memory import MemoryProvider  # noqa: E402
from memory.ob_client import MemoryResult  # noqa: E402

# header 照 ob_client 里的常量写，别自己编 —— 编错了 split_breath 会把
# 整段当成 core，dynamic 变空，测试反而"通过"得莫名其妙
CORE = "=== 核心准则 ===\n钉选的东西\n"
DYNAMIC = "=== 浮现记忆 ===\n她说过怕冷\n第一天是 6 月 10 日"


class FakeOB:
    """假的 Ombre Brain。记录被怎么调用了。"""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[dict] = []

    def breath(self, query: str = "", **kw):
        self.calls.append({"query": query, **kw})
        if not self.ok:
            return MemoryResult(ok=False, error="上游 500")
        return MemoryResult(ok=True, text=CORE + DYNAMIC)


def test_only_keeps_dynamic_not_core():
    """核心准则已经在静态前缀里了，再塞一遍就是花钱买重复。"""
    p = MemoryProvider(FakeOB())
    s = p.get_state(Turn(text="你还记得我怕冷吗"))
    assert s["relevant"] == ["她说过怕冷", "第一天是 6 月 10 日"]
    assert "钉选的东西" not in "".join(s["relevant"])


def test_empty_text_skips_the_call():
    """没有话题就别去检索 —— 不带 query 的 breath 返回的是钉选桶，白花 7 秒。"""
    ob = FakeOB()
    s = MemoryProvider(ob).get_state(Turn(text="   "))
    assert ob.calls == [], "空话题不该打 OB"
    assert s["relevant"] == []
    assert "skipped" in s


def test_failure_is_not_swallowed():
    """「没检索到」和「检索挂了」是两件事，不许吞成空。"""
    p = MemoryProvider(FakeOB(ok=False))
    s = p.get_state(Turn(text="随便问问"))
    assert s["available"] is False
    assert "OB 检索失败" in s["error"]


def test_failure_falls_back_to_stale_and_says_so():
    p = MemoryProvider(FakeOB())
    p.get_state(Turn(text="第一次"))          # 先成功一次
    p.cache.set(p.name, p.cache.get(p.name).value, timedelta(0))  # 让它过期

    p.ob = FakeOB(ok=False)
    s = p.get_state(Turn(text="第二次"))
    assert s["stale"] is True
    assert "可能不是最新的" in p.render(s)     # 别把陈年检索当刚想起来的说


def test_caches_for_five_minutes_not_every_turn():
    """标 volatile 就是每轮 7 秒 —— 这个 Provider 最贵的失败模式。"""
    p = MemoryProvider(FakeOB())
    assert p.volatile is False
    assert p.ttl == timedelta(minutes=5)

    for t in ["她怕冷吗", "她喜欢什么", "上次说的那个"]:
        p.get_state(Turn(text=t))
    assert len(p.ob.calls) == 1, "5 分钟内不该反复打 OB"


def test_is_read_only():
    """只读。写记忆是 Ombre Brain 自己的事（架构文档第十节）。"""
    ob = FakeOB()
    p = MemoryProvider(ob)
    p.get_state(Turn(text="问点什么"))
    assert all(set(c) <= {"query", "max_results"} for c in ob.calls), \
        "只该调 breath 检索，不许带写入参数"


def test_not_in_the_per_turn_lineup():
    """**这条是这次的核心决定**：memory 不进每轮名单。

    接进去的代价（2026-08-02 实测）：
      - OB 检索一次约 7 秒，闲聊会从 2.7 秒变 10 秒
      - dynamic_system 每轮都变 → 缓存命中率 98.9% → 62.5%，每轮成本 ×12

    日常对话走 recall_memory 工具，模型自己判断要不要回忆。
    Daily Planner 那种一天一两次的场景再把它加进名单。
    """
    src = (Path(__file__).resolve().parents[1] / "nox.py").read_text(encoding="utf-8")
    lineup = src.split('self.context.render(')[1].split(')')[0]
    assert '"memory"' not in lineup, (
        "memory 被接进每轮名单了 —— 那是每轮 7 秒 + 成本 ×12，"
        "确认这是有意的再改这条测试"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
