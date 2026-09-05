"""MemoryProvider 测试。不打网络，OB 全用假的。

重点守三件事：
  1. **只读** —— 它绝不能调用任何写入类接口
  2. **检索不留痕** —— touch=False / drift=False，否则会刷歪 OB 的权重模型
  3. **不写死在每轮名单里** —— 加载与否由 classify_context 判，见
     `test_context_routing.py` 那一组

⚠️ 2026-09-05：第 2、3 条替换了原来的「永远不加载」。
封它的两条理由（7 秒 / 缓存 62.5%）都已失效 ——
详见 `context/providers/memory.py` 顶上那张表。
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
    assert all(set(c) <= {"query", "max_results", "touch", "drift"} for c in ob.calls), \
        "只该调 breath 检索，不许带写入参数"


#: 🔴 **真实的 OB 返回形状**（2026-09-05 从 VPS 上抄下来的，不是编的）。
#:
#: 脱水结果是格式化 JSON —— 6 条就是 1866 字符，而动态块总预算只有 800。
#: 整段塞进去的话 `registry.render()` 会把它**丢掉**、只留一条 warning，
#: 表现成「解禁了但他还是想不起来」，不看日志永远发现不了。
REAL_OB = """=== 浮现记忆 ===
[语义关联] [bucket_id:1cae7095d021] 📌 记忆桶: 小克的接纳 [主题:人际, 心理] [情感:V0.7/A0.3]
{
  "core_facts": [
    "2026-08-06晚，糖糖和小克聊会话启动前状态与记忆连续性",
    "小克坦言看不见自己的醒来、位置查不到、记忆靠翻查"
  ],
  "emotion_state": "被接纳、温暖",
  "todos": [],
  "keywords": ["糖糖", "小克", "记忆连续性"],
  "summary": "糖糖对小克说现在这样已经很好了，接纳其记忆缺口。"
}
---
[bucket_id:79425dd94929] 📌 记忆桶: 修复对话记忆bug [主题:编程, 恋爱] [情感:V0.6/A0.4]
{
  "core_facts": ["修复了一个伤人的bug"],
  "emotion_state": "愧疚",
  "todos": ["不在正式环境污染上下文"],
  "keywords": ["bug"],
  "summary": "修好了每说一句都问怎么醒了的 bug。"
}"""


def test_json_dehydration_is_boiled_down_to_one_line_each():
    """🔴 只取 summary，不把整坨 JSON 塞进他的上下文。

    `recall_memory` 工具那条路无所谓（模型当工具结果读 JSON 没问题），
    Provider 这条路是**每轮都要付钱的上下文**。
    """
    class OB:
        def breath(self, **kw):
            return MemoryResult(ok=True, text=REAL_OB)

    p = MemoryProvider(OB())
    s = p.get_state(Turn(text="你还记得我们第一天吗"))
    assert s["count"] == 2, "两条记忆该出两条，不是按行拆成几十条"
    assert s["relevant"][0].startswith("小克的接纳：")
    assert "现在这样已经很好了" in s["relevant"][0]
    for bad in ("core_facts", "keywords", "emotion_state", "{"):
        assert all(bad not in ln for ln in s["relevant"]), bad

    out = p.render(s)
    assert len(out) < 300, f"渲染出来 {len(out)} 字，会把 800 预算挤爆"


def test_plain_text_blocks_keep_every_line():
    """⚠️ 解析不出 JSON 时**每一行都要留下**。

    第一版只取第一行 —— 「解析不出来就少给几条」是最难查的那种故障：
    他答不上来，而日志里什么都没有。
    """
    class OB:
        def breath(self, **kw):
            return MemoryResult(ok=True, text="=== 浮现记忆 ===\n她怕冷\n第一天是 6 月 10 日")

    s = MemoryProvider(OB()).get_state(Turn(text="随便"))
    assert s["relevant"] == ["她怕冷", "第一天是 6 月 10 日"]


@pytest.mark.parametrize("sentinel", [
    "未找到相关记忆。",
    "没有可以展示的记忆。",
    "没有重要度 >= 7 的记忆。",
    "权重池平静，没有需要处理的记忆。",
])
def test_ob_empty_sentinels_are_not_memories(sentinel):
    """🔴 OB 检索不到时返回的是**一句中文**，不是空串。

    而 `split_breath` 没有 header 就把整段归到 dynamic ——
    于是它会被当成一条记忆渲染成「【记起来的】未找到相关记忆。」。

    雪藏期间这个洞不发作（planner 一天才用一两次）；
    2026-09-05 解禁时才现形。这几句原文抄自 `Ombre-Brain/server.py`，
    钉在这里 —— OB 改文案的话这条会红，那正是要的。
    """
    class OB:
        def breath(self, **kw):
            return MemoryResult(ok=True, text=sentinel)

    p = MemoryProvider(OB())
    s = p.get_state(Turn(text="她怕冷"))
    assert s["relevant"] == []
    assert p.render(s) == "", "检索落空不该往他脑子里塞一句废话"


@pytest.mark.parametrize("sentinel", [
    "记忆系统暂时无法访问。",
    "检索过程出错，请稍后重试。",
])
def test_ob_failure_is_not_silently_empty(sentinel):
    """⚠️ 「没找到」和「挂了」是两件事（第十九节第 3 条：失败必须可见）。

    当成空结果的话，OB 宕机会表现成「他忽然什么都不记得了」，
    而且悄无声息 —— 要抛出去，让基类退回旧记忆并标 stale。
    """
    class OB:
        def breath(self, **kw):
            return MemoryResult(ok=True, text=sentinel)

    p = MemoryProvider(OB())
    s = p.get_state(Turn(text="她怕冷"))
    assert s.get("available") is False, "OB 坏了要如实报，不许假装没记忆"


def test_retrieval_leaves_no_trace():
    """🔴 **被动检索不许留下痕迹**（2026-09-05 解禁时加的硬约束）。

    `touch=True` 会推高 activation_count 并重置衰减，而 OB 的打分里
    有 `activation^0.3` —— 每轮自动检索都 touch 等于持续给一批记忆续命，
    旧记忆永远归不了档，她的记忆权重模型会被慢慢刷歪。**而且完全不报错。**

    `drift`（命中不足 3 条时 40% 概率漂旧桶）同理：他主动回忆时那是
    「忽然想起来」，每轮自动注入时就是噪声 —— 同一句话问两次会拿到
    不同的旧记忆，而且它进的是每轮都要付钱的 dynamic 块。

    分工：`recall_memory` 工具照旧两个都 True，那条路才是他主动回忆。
    """
    ob = FakeOB()
    p = MemoryProvider(ob)
    p.get_state(Turn(text="问点什么"))
    assert ob.calls, "根本没调 breath"
    for c in ob.calls:
        assert c.get("touch") is False, "Provider 检索不该激活记忆"
        assert c.get("drift") is False, "Provider 检索不该带随机浮现"


def test_not_hardcoded_into_the_lineup():
    """memory 不许写死在 `_dynamic` 的名单里。

    2026-09-05 解禁之后，这条守的东西变了但没消失：
    以前守「永远不加载」，现在守「**加载与否必须过 classify_context**」。

    写死进名单 = 绕过那两个条件 = 「今天几号」也要等 650ms，
    而且没有任何开关能关掉它。它是唯一会打外部服务的按需 Provider，
    这个决定权不该散落在两个地方。
    """
    src = (Path(__file__).resolve().parents[1] / "nox.py").read_text(encoding="utf-8")
    lineup = src.split('self.context.render(')[1].split(')')[0]
    assert '"memory"' not in lineup, (
        "memory 被写死进每轮名单了 —— 加载条件该由 classify_context 判，"
        "确认这是有意的再改这条测试"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
