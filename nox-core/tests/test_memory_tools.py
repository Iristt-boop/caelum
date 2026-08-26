"""七个记忆工具的测试。假 OB，不打网络。

重点在参数处理 —— 这些工具能改能删记忆，传错一个字段就是不可逆的损失。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.loop import AgentLoop  # noqa: E402
from memory.ob_client import MemoryResult  # noqa: E402
from memory.tools import make_handlers, register_all  # noqa: E402


class FakeOB:
    """记录每次调用的参数，方便断言透传是否正确。"""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[str, dict]] = []

    def _r(self, text: str = "ok") -> MemoryResult:
        return MemoryResult(self.ok, text=text if self.ok else "",
                            error=None if self.ok else "OB 挂了")

    def recall(self, query, **kw):
        self.calls.append(("recall", {"query": query, **kw}))
        return self._r("找到了一些记忆")

    def hold(self, content, **kw):
        self.calls.append(("hold", {"content": content, **kw}))
        return self._r("已存")

    def grow(self, content):
        self.calls.append(("grow", {"content": content}))
        return self._r("已归档，拆了 3 个桶")

    def pulse(self, include_archive=False):
        self.calls.append(("pulse", {"include_archive": include_archive}))
        return self._r("固化 37 动态 139")

    def dream(self):
        self.calls.append(("dream", {}))
        return self._r("最近的桶：...")

    def trace(self, bucket_id, **kw):
        self.calls.append(("trace", {"bucket_id": bucket_id, **kw}))
        return self._r("已修改")

    def merge(self, target_id, source_ids):
        self.calls.append(("merge", {"target_id": target_id, "source_ids": source_ids}))
        return self._r("已合并 2 个桶")


@pytest.fixture
def ob():
    return FakeOB()


@pytest.fixture
def h(ob):
    return make_handlers(ob)


# ---------------------------------------------------------------- 注册

def test_all_seven_registered(ob):
    loop = AgentLoop(adapter=None)  # type: ignore[arg-type]
    register_all(loop, ob)
    assert set(loop.tools) == {
        "recall_memory", "remember", "archive_memory",
        "memory_status", "review_memory", "edit_memory", "merge_memory",
    }


def test_registration_order_is_stable(ob):
    """工具定义是缓存前缀的一部分，顺序变了缓存全废。"""
    a, b = AgentLoop(adapter=None), AgentLoop(adapter=None)  # type: ignore[arg-type]
    register_all(a, ob)
    register_all(b, FakeOB())
    assert list(a.tools) == list(b.tools)


# ---------------------------------------------------------------- edit

def test_edit_only_passes_given_fields(ob, h):
    """OB 那边 -1 / 空串表示「不改」。全量透传会把没打算动的字段覆盖掉。"""
    h["edit_memory"]({"bucket_id": "abc", "resolved": 1})
    name, args = ob.calls[-1]
    assert name == "trace"
    assert args == {"bucket_id": "abc", "resolved": 1}
    assert "pinned" not in args and "content" not in args


def test_edit_without_changes_does_nothing(ob, h):
    out = h["edit_memory"]({"bucket_id": "abc"})
    assert "没有指定" in out
    assert ob.calls == [], "什么都没改就不该调 OB"


def test_edit_requires_bucket_id(ob, h):
    out = h["edit_memory"]({"resolved": 1})
    assert "bucket_id" in out
    assert ob.calls == []


def test_edit_can_delete(ob, h):
    """糖糖说了要给他删除权限。"""
    h["edit_memory"]({"bucket_id": "abc", "delete": True})
    assert ob.calls[-1][1]["delete"] is True


def test_edit_delete_false_is_not_passed(ob, h):
    """delete=false 不该透传 —— 传过去可能被当成显式指令。"""
    h["edit_memory"]({"bucket_id": "abc", "delete": False, "pinned": 1})
    assert "delete" not in ob.calls[-1][1]


# ---------------------------------------------------------------- merge

def test_merge_normal(ob, h):
    h["merge_memory"]({"target_id": "t1", "source_ids": ["s1", "s2"]})
    assert ob.calls[-1] == ("merge", {"target_id": "t1", "source_ids": ["s1", "s2"]})


def test_merge_rejects_self(ob, h):
    """把桶合并进它自己 —— OB 那边行为未定义，这里直接挡掉。"""
    out = h["merge_memory"]({"target_id": "t1", "source_ids": ["t1", "s2"]})
    assert "自己" in out
    assert ob.calls == []


def test_merge_needs_both(ob, h):
    assert "需要" in h["merge_memory"]({"target_id": "t1", "source_ids": []})
    assert ob.calls == []


def test_merge_cleans_empty_ids(ob, h):
    h["merge_memory"]({"target_id": "t1", "source_ids": ["s1", "  ", ""]})
    assert ob.calls[-1][1]["source_ids"] == ["s1"]


# ---------------------------------------------------------------- 其余

def test_archive_rejects_empty(ob, h):
    assert "为空" in h["archive_memory"]({"content": "  "})
    assert ob.calls == []


def test_status_passes_include_archive(ob, h):
    h["memory_status"]({"include_archive": True})
    assert ob.calls[-1][1]["include_archive"] is True


def test_review_handles_empty_result(h, ob):
    ob.dream = lambda: MemoryResult(True, text="")
    assert "没有新增" in h["review_memory"]({})


# ---------------------------------------------------------------- 失败

@pytest.mark.parametrize("tool,args", [
    ("archive_memory", {"content": "x"}),
    ("memory_status", {}),
    ("review_memory", {}),
    ("edit_memory", {"bucket_id": "a", "resolved": 1}),
    ("merge_memory", {"target_id": "t", "source_ids": ["s"]}),
])
def test_failures_raise_so_loop_reports_them(tool, args):
    """OB 挂了要抛出去，让 loop 按「工具失败」原样回传给模型。

    在这里 try/except 转成一句「失败了」，正是会让他开始编造的那个错误。
    """
    broken = FakeOB(ok=False)
    handlers = make_handlers(broken)
    with pytest.raises(RuntimeError):
        handlers[tool](args)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
