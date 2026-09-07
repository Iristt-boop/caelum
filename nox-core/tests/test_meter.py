"""utility 用量记账（2026-09-06）。

糖糖问「utility 的命中要优化吗」，我答不上来 —— 因为 `usage_log` 那行写在
SSE done 帧里，**只记聊天主链路**。而 utility 一天跑 166+ 次
（压缩 59 / 话题池 96 / 意义推断 11），比聊天还多，却一个数都没有。

🔴 这个文件守的核心只有一条：**记账绝不许影响主线。**
压缩、意义推断、话题过滤本身都是增强；记账比它们更边缘。
一个观测动作把主线拖慢或者拖挂，是本末倒置。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import meter  # noqa: E402
from agent.llm import Turn, Usage  # noqa: E402


class FakeBridge:
    def __init__(self, boom: bool = False):
        self.posts: list[tuple[str, dict]] = []
        self.boom = boom

    def post(self, path, body=None):
        if self.boom:
            raise RuntimeError("bridge 挂了")
        self.posts.append((path, body or {}))
        return None


class FakeAdapter:
    class cfg:  # noqa: N801
        model = "glm-5.3-flash"

    def __init__(self, usage: Usage | None = None, boom: bool = False):
        self.calls = 0
        self.boom = boom
        self.usage = usage or Usage(input_tokens=100, output_tokens=20,
                                    cache_read_tokens=80)

    def complete(self, messages, tools, **kw):
        self.calls += 1
        if self.boom:
            raise RuntimeError("模型挂了")
        return Turn(stop_reason="end_turn", text="好", usage=self.usage)


@pytest.fixture(autouse=True)
def _clean():
    meter.bind(None)
    yield
    meter.bind(None)


def _settle():
    """记账是后台线程发的，等它一下。"""
    for _ in range(50):
        time.sleep(0.01)


# ---------------------------------------------------------------- 不影响主线


def test_no_bridge_means_no_accounting_but_same_behavior():
    """没接 bridge = 不记账，**行为完全不变**。它是观测，不是功能。"""
    a = FakeAdapter()
    t = meter.tag(a, "compaction").complete([], [])
    assert t.text == "好" and a.calls == 1


def test_bridge_failure_never_reaches_the_caller():
    """🔴 bridge 挂了，压缩照常返回。

    记账失败让压缩失败 = 一个观测动作把功能拖挂，本末倒置。
    """
    meter.bind(lambda: FakeBridge(boom=True))
    t = meter.tag(FakeAdapter(), "compaction").complete([], [])
    _settle()
    assert t.text == "好"


def test_model_failure_still_propagates():
    """⚠️ 反过来不许吞：**模型**挂了要照常抛给调用方。

    记账层只负责记账，不许顺手把真错误吃掉 ——
    那正是「不许编」的反面（工具失败被吞成沉默）。
    """
    meter.bind(lambda: FakeBridge())
    with pytest.raises(RuntimeError, match="模型挂了"):
        meter.tag(FakeAdapter(boom=True), "compaction").complete([], [])


# ---------------------------------------------------------------- 真的记上了


def test_records_task_model_and_cache():
    b = FakeBridge()
    meter.bind(lambda: b)
    meter.tag(FakeAdapter(), "topic-filter").complete([], [])
    _settle()
    assert len(b.posts) == 1
    path, body = b.posts[0]
    assert path == "/api/usage"
    assert body["task"] == "topic-filter"
    assert body["model"] == "glm-5.3-flash"
    assert body["input_tokens"] == 100
    assert body["cached_tokens"] == 80, "缓存命中要记 —— 这正是她问的那个数"


def test_empty_usage_is_not_recorded():
    """一次没花 token 的调用不该在账本里占一行。"""
    b = FakeBridge()
    meter.bind(lambda: b)
    meter.tag(FakeAdapter(usage=Usage()), "compaction").complete([], [])
    _settle()
    assert b.posts == []


def test_other_attributes_pass_through():
    """包一层不该挡住 adapter 的其它东西（cfg / stream / name…）。"""
    a = FakeAdapter()
    wrapped = meter.tag(a, "x")
    assert wrapped.cfg.model == "glm-5.3-flash"


def test_tag_none_is_none():
    """utility 没配时 adapter 是 None，贴标签不该炸。"""
    assert meter.tag(None, "compaction") is None
