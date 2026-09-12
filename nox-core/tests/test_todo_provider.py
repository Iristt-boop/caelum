"""TodoProvider 测试 —— 只测**活的那条路**（bridge 的本地清单）。

⚠️ **2026-09-12：这个文件原来有一半在测 GitHub 那条路**（`_md()` 拼 markdown、
`_p()` 装假 GitHub、`RestResult` 假响应）。而 GitHub 的 `todo.md` 2026-08-18
就退役了，2026-09-12 连代码一起删了 —— **那些测试测的是一条不在跑的路**。

而且它们造成过真实损害：`test_render_is_within_budget` 走 GitHub 路一直是绿的，
因为它那条路 `due_soon` 只有一两条；与此同时线上跑的本地清单路把 800 字预算吃光，
`Context 超出 800 字预算，这轮略过: todo` 48 小时喊了 61 次。
**测试覆盖的是没在跑的那条路，正在跑的那条一条没测。**

所以现在只留两类，都打在本地清单这条路上：
  1. **Provider 级别** —— TTL / 只读 / 不在每轮清单里 / 失败不许吞 / 没源要报错
  2. **渲染级别** —— 裁条数、只取标题、不吃光预算、空清单不说话
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.base import Turn  # noqa: E402
from context.providers.todo import TodoProvider  # noqa: E402
from tools.http import RestResult  # noqa: E402


class _FakeBridge:
    """假的 bridge，只回 `/api/todo/list`。

    刻意**不提供** post/put —— provider 只该读。真发了写请求会 AttributeError，
    比"断言没写过"更硬。
    """

    def __init__(self, sections: dict | None = None, ok: bool = True,
                 error: str = "") -> None:
        self.sections = sections or {}
        self.ok = ok
        self.error = error
        self.calls: list[str] = []

    def get(self, path, params=None):
        self.calls.append(path)
        assert path == "/api/todo/list", f"只该读清单，读到 {path}"
        if not self.ok:
            return RestResult(False, error=self.error or "HTTP 500: bridge 挂了")
        return RestResult(True, {"ok": True, "sections": self.sections})


def _local(**sections):
    """返回 (provider, state)。"""
    p = TodoProvider(bridge=_FakeBridge(sections))
    return p, p.get_state(Turn())


# --------------------------------------------------------------- 渲染级别

def test_本地清单的进行中全进了_due_soon():
    """先钉住这个**含义差异**本身 —— 它就是 2026-09-08 那次事故的病根。

    走 GitHub 时 due_soon = 今明两天到期的；走本地清单时 = 整个「进行中」。
    以后谁再换源，这条会提醒他回去看渲染那一段。
    """
    _, s = _local(进行中=["每天 09:00 吃药", "9月8日 交房租"], 随时=["读尼采"])
    assert len(s["due_soon"]) == 2
    assert all(x.startswith("今天：") for x in s["due_soon"])
    assert s["total_open"] == 3


def test_今天要做的也要裁掉破折号后面的说明():
    """那些说明是写给糖糖看的，他只要知道是哪件事。"""
    p, s = _local(进行中=["写晨检卡 —— 要接日志摘要、还要处理静音名单和突增判据"])
    line = p.render(s)
    assert "写晨检卡" in line
    assert "静音名单" not in line, f"细节说明不该出现：{line}"


def test_今天要做的有条数上限():
    p, s = _local(进行中=[f"第{i}件事" for i in range(12)])
    line = [x for x in p.render(s).splitlines() if x.startswith("【要做的】")][0]
    assert line.count("、") == p.max_due - 1
    assert f"还有 {12 - p.max_due} 项" in line


def test_手头在做的有条数上限():
    """这段每轮都要付未命中价，不能让它无限长。

    （原来是走 GitHub 路测的；那条路删了，但**渲染是共享的**，
      所以这条必须留着，只是换成打在活的那条路上。）
    """
    p, s = _local(随时=[f"挂着的{i}" for i in range(9)])
    line = [x for x in p.render(s).splitlines() if x.startswith("【手头在做】")][0]
    assert line.count("、") == p.max_ongoing - 1
    assert f"还有 {9 - p.max_ongoing} 项" in line


def test_手头在做的只取标题():
    """不裁的话这一行能到 200 多字，吃掉 800 预算的四分之一。"""
    p, s = _local(随时=["读尼采 —— 从《查拉图斯特拉如是说》第三卷开始，每天两页"])
    line = [x for x in p.render(s).splitlines() if x.startswith("【手头在做】")][0]
    assert "读尼采" in line
    assert "查拉图斯特拉" not in line, "细节说明不该出现"


def test_render_is_within_budget():
    p, s = _local(进行中=["写晨检卡"], 随时=["读尼采"], 近期=["拼豆黑豹"])
    text = p.render(s)
    assert 0 < len(text) < 200, f"太长了：{len(text)} 字"


def test_一整屏待办也不许吃光预算():
    """🔴 这条是 2026-09-08 那次事故的正身。

    12 条「进行中」、每条都带一长段说明 —— 线上真实形状。修之前这里能渲染到
    **一千多字**，而整个 dynamic_system 的预算只有 800，
    于是 todo 每次都被 `ContextRegistry.render()` 整条丢掉。
    """
    p, s = _local(
        进行中=[f"第{i}件事 —— {'细' * 60}" for i in range(12)],
        随时=[f"挂着的{i} —— {'节' * 60}" for i in range(8)],
        近期=[f"近期{i} —— {'说' * 60}" for i in range(5)],
    )
    text = p.render(s)
    assert len(text) < 300, f"太长了：{len(text)} 字\n{text}"


def test_没有待办时什么都不说():
    """🔴 不许编一个「你今天没什么事」出来 —— 那和「我没查到」是两回事。"""
    p, s = _local(进行中=[], 随时=[], 近期=[])
    assert p.render(s) == ""


def test_清单里只有已完成时也不说话():
    """bridge 是 `WHERE done = 0`，已完成的根本不来；真给空 sections 也不能出声。"""
    p, s = _local()
    assert s["total_open"] == 0
    assert p.render(s) == ""


# -------------------------------------------------------------- Provider 级别

def test_caches_for_30min():
    """30 分钟内只该拉一次。

    ⚠️ 这个 TTL 同时是**「她刚记下的事，他最长能多久当作没听见」** ——
       所以写路径必须挂 invalidate()，不能只靠它自己过期。
    """
    b = _FakeBridge({"进行中": ["吃药"]})
    p = TodoProvider(bridge=b)
    for _ in range(5):
        p.get_state(Turn())
    assert b.calls == ["/api/todo/list"], "30 分钟内只该拉一次"
    assert p.ttl == timedelta(minutes=30)
    assert p.volatile is False


def test_只读_只发_GET():
    """只读。写待办走 `tools/daily.py` 那条路（bridge 的 SQLite）。"""
    b = _FakeBridge({"进行中": ["吃药"]})
    p = TodoProvider(bridge=b)
    p.get_state(Turn())
    assert all(c == "/api/todo/list" for c in b.calls)
    assert not hasattr(b, "post"), "provider 不该有写的能力"


def test_读不到清单时如实报错不吞():
    """bridge 挂了必须让上层看见 `available: False`，不能悄悄回一份空清单 ——
    「没有源」和「她真的没事」是两件事。"""
    p = TodoProvider(bridge=_FakeBridge(ok=False, error="HTTP 500: bridge 挂了"))
    s = p.get_state(Turn())
    assert s["available"] is False
    assert "读本地清单失败" in s["error"]
    assert "bridge 挂了" in s["error"]


def test_没有_bridge_时明确报错():
    """2026-09-12 起没有 GitHub 兜底了 —— 没源就报错，不许端一份退役存档当她的待办。"""
    s = TodoProvider().get_state(Turn())
    assert s["available"] is False
    assert "bridge" in s["error"]
    assert "退役" in s["error"]


def test_not_in_the_per_turn_lineup():
    """待办不在「每轮都要」的那份清单里 —— 它有 30 分钟缓存，没必要每轮付价。"""
    src = (Path(__file__).resolve().parents[1] / "nox.py").read_text(encoding="utf-8")
    lineup = src.split('self.context.render(')[1].split(')')[0]
    assert '"todo"' not in lineup


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
