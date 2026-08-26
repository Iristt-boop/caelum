"""Context Provider 框架测试（Day 1 骨架）。

守的是执行计划第一周检查点里那几条硬要求：
  - 全同步，没有 async
  - 缓存真的挡住了重复拉取（命中率可量）
  - Provider 挂了退回旧数据，而不是突然失明
  - 一个 Provider 崩了不拖垮整轮
  - 渲染结果是给 dynamic_system 的
"""

from __future__ import annotations

import inspect
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context import BaseContextProvider, ContextCache, ContextProviderRegistry  # noqa: E402
from context.base import Turn  # noqa: E402


class Counting(BaseContextProvider):
    """记录被真正拉了几次，用来验证缓存有没有生效。"""

    name = "counting"
    ttl = timedelta(minutes=5)
    section = "time"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = 0
        self.fail = False

    def _fetch(self, turn) -> dict:
        self.calls += 1
        if self.fail:
            raise RuntimeError("上游挂了")
        return {"n": self.calls}


# ------------------------------------------------------------ 缓存

def test_cache_blocks_repeat_fetch():
    p = Counting()
    assert p.get_state() == {"n": 1}
    for _ in range(5):
        p.get_state()
    assert p.calls == 1, "缓存没挡住，每轮都在真拉"


def test_force_refresh_bypasses_cache():
    p = Counting()
    p.get_state()
    p.get_state(force_refresh=True)
    assert p.calls == 2


def test_expired_ttl_refetches():
    p = Counting()
    p.ttl = timedelta(seconds=0)      # 立刻过期
    p.get_state()
    p.get_state()
    assert p.calls == 2


def test_invalidate_forces_refetch():
    p = Counting()
    p.get_state()
    p.invalidate()
    p.get_state()
    assert p.calls == 2


def test_cache_hit_rate_is_measurable():
    """命中率必须能量 —— Context Engine 唯一的严重后果是「不报错但很贵」，
    从任何监控上都看不出来，只能主动去量。"""
    c = ContextCache()
    p = Counting(cache=c)
    p.get_state()                      # miss
    for _ in range(3):
        p.get_state()                  # hit ×3
    s = c.stats()
    assert s["hits"] == 3 and s["misses"] == 1
    assert s["hit_rate"] == 0.75


# ------------------------------------------------------------ 失败要退回旧数据

def test_failure_falls_back_to_stale():
    """Provider 挂了应该让他「知道得旧一点」，不是「突然失明」。"""
    p = Counting()
    p.get_state()                                   # 先成功一次，留下旧值
    p.cache.set(p.name, {"n": 1}, timedelta(0))     # 让它过期，但值还在
    p.fail = True

    state = p.get_state()

    assert state["n"] == 1             # 用的是旧值
    assert state["stale"] is True      # 而且明确标了是旧的
    assert "stale_age_s" in state


def test_cache_object_is_shared_even_when_empty():
    """空 ContextCache 的 bool() 是 False（它有 __len__）——
    `cache or ContextCache()` 会把刚传进来的空缓存丢掉，各建各的，且不报错。"""
    c = ContextCache()
    assert not c, "空缓存本来就是 falsy，这条前提变了下面的断言就没意义了"
    assert Counting(cache=c).cache is c
    assert ContextProviderRegistry(cache=c).cache is c


def test_failure_without_history_reports_unavailable():
    """从来没成功过就如实说不可用，不许悄悄返回空字典假装正常。"""
    p = Counting()
    p.fail = True
    state = p.get_state()
    assert state["available"] is False
    assert "上游挂了" in state["error"]


def test_fetch_must_return_dict():
    class Bad(BaseContextProvider):
        name = "bad"
        def _fetch(self, turn):
            return "不是 dict"

    with pytest.raises(TypeError):
        Bad().get_state()


def test_provider_requires_name():
    class Nameless(BaseContextProvider):
        def _fetch(self, turn):
            return {}

    with pytest.raises(ValueError):
        Nameless()


# ------------------------------------------------------------ 注册表

def test_registry_shares_one_cache():
    """各家各存各的迟早不一致，也量不出命中率。"""
    reg = ContextProviderRegistry()
    p = reg.register(Counting())
    assert p.cache is reg.cache


def test_registry_rejects_duplicate():
    reg = ContextProviderRegistry()
    reg.register(Counting())
    with pytest.raises(ValueError):
        reg.register(Counting())


def test_registry_only_loads_what_router_asked_for():
    """加载谁由 Router 决定，注册表不自作主张 ——
    否则「闲聊背上全量上下文」会悄悄发生。"""
    reg = ContextProviderRegistry()
    a = reg.register(Counting())

    class Other(Counting):
        name = "other"

    b = reg.register(Other())

    reg.get_context_set(["counting"])
    assert a.calls == 1 and b.calls == 0, "没被点名的 Provider 不该被拉"


def test_unknown_provider_is_skipped_not_fatal():
    reg = ContextProviderRegistry()
    reg.register(Counting())
    out = reg.get_context_set(["counting", "不存在的"])
    assert set(out) == {"counting"}


def test_one_broken_provider_does_not_kill_the_turn():
    class Exploding(BaseContextProvider):
        name = "boom"
        def _fetch(self, turn):
            return {}
        def get_state(self, turn=None, force_refresh=False):
            raise RuntimeError("我自己崩了")

    reg = ContextProviderRegistry()
    reg.register(Counting())
    reg.register(Exploding())

    out = reg.get_context_set(["counting", "boom"])
    assert out["counting"] == {"n": 1}
    assert out["boom"]["available"] is False


# ------------------------------------------------------------ 渲染

def test_render_keeps_router_order():
    class A(Counting):
        name = "a"
        def render(self, state): return "第一句"

    class B(Counting):
        name = "b"
        def render(self, state): return "第二句"

    reg = ContextProviderRegistry()
    reg.register(A())
    reg.register(B())
    assert reg.render(["b", "a"]) == "第二句\n第一句"


def test_empty_render_leaves_no_blank_line():
    """没什么好说的就整条不出现，别在提示里留空行。"""
    class Silent(Counting):
        name = "silent"
        def render(self, state): return ""

    reg = ContextProviderRegistry()
    reg.register(Silent())
    assert reg.render(["silent"]) == ""


def test_render_skips_unavailable_by_default():
    p = Counting()
    p.fail = True
    assert p.render(p.get_state()) == ""


def test_resolve_separates_missing_from_present():
    """「Provider 不在场」和「它返回了空」是两件事，World State 的 _meta 要区分。"""
    reg = ContextProviderRegistry()
    reg.register(Counting())
    known, missing = reg.resolve(["counting", "home", "weather"])
    assert known == ["counting"]
    assert missing == ["home", "weather"]


def test_render_respects_char_budget():
    """这段每轮都发、且在缓存断点之后 —— 每个字都按未命中价付费，必须卡上限。"""
    class Long(Counting):
        name = "long"
        def render(self, state): return "长" * 500

    class Also(Counting):
        name = "also"
        def render(self, state): return "短" * 500

    reg = ContextProviderRegistry()
    reg.register(Long())
    reg.register(Also())

    out = reg.render(["long", "also"], budget=600)
    assert "长" * 500 in out          # 排在前面的保住（顺序即优先级）
    assert "短" * 500 not in out      # 超预算的被挡下
    assert "also" in out              # 但要明说少了谁，不许悄悄少说


def test_render_within_budget_keeps_everything():
    class A(Counting):
        name = "a"
        def render(self, state): return "一句话"

    reg = ContextProviderRegistry()
    reg.register(A())
    assert reg.render(["a"]) == "一句话"


def test_broken_render_does_not_kill_the_turn():
    class BadRender(Counting):
        name = "badrender"
        def render(self, state): raise RuntimeError("渲染崩了")

    reg = ContextProviderRegistry()
    reg.register(Counting())
    reg.register(BadRender())
    assert reg.render(["counting", "badrender"]) == "counting: n=1"


# ------------------------------------------------------------ 架构约束

def test_volatile_provider_bypasses_cache():
    """每轮必新的 Provider（如 mood）不许吃缓存 —— 缓存住会把上一轮的
    判断用到这一轮，是正确性问题不是性能取舍。"""
    class Volatile(Counting):
        name = "volatile"
        volatile = True

    p = Volatile()
    for _ in range(4):
        p.get_state()
    assert p.calls == 4, "volatile Provider 被缓存了"
    assert len(p.cache) == 0, "volatile Provider 不该往缓存里写"


def test_turn_reaches_fetch():
    """当轮输入要能传到 _fetch —— mood 的场景判断靠它。"""
    seen = {}

    class Peek(BaseContextProvider):
        name = "peek"
        def _fetch(self, turn):
            seen["text"] = turn.text
            return {"ok": True}

    Peek().get_state(Turn(text="她说的话"))
    assert seen["text"] == "她说的话"


def test_everything_is_synchronous():
    """架构文档 0.2：Provider 写同步。这条被破坏时要当场失败，
    而不是等到 Starlette 线程池里再炸（contextvars 那次的教训）。"""
    for cls in (BaseContextProvider, ContextCache, ContextProviderRegistry):
        for name, fn in inspect.getmembers(cls, inspect.isfunction):
            assert not inspect.iscoroutinefunction(fn), f"{cls.__name__}.{name} 是 async"


def test_engine_is_not_written_yet():
    """Engine 是 Provider 足够多之后的封装，不是起点（架构文档第二节）。
    这条测试是个路标：真要写 engine.py 时删掉它，别无意中提前搭抽象层。"""
    assert not (Path(__file__).resolve().parents[1] / "context" / "engine.py").exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
