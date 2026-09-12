"""工具名不许重复。

2026-08-05 踩过：`tools/todo.py` 注册了一个 `add_todo`，和
`tools/daily.py` 里早就有的同名工具撞了。后注册的赢，结果 Nox
再也没法往 App 的「今天」页记东西 —— 没有任何报错，静默换了行为。

模型看到的是一张扁平的工具表，重名就是覆盖。这条守着别再来一次。
"""

from __future__ import annotations

from collections import Counter

from tools import amap as amap_tools
from tools import daily as daily_tools
from tools import didi as didi_tools
from tools import intimate as intimate_tools
from tools import kd100 as kd100_tools
from tools import luckin as luckin_tools
from tools import mcd as mcd_tools
from tools import taobao as taobao_tools
from tools import train as train_tools
from tools import kd100 as kd100_tools


def _spec_names(module) -> list[str]:
    from agent.llm import ToolSpec

    return [
        v.name for v in vars(module).values()
        if isinstance(v, ToolSpec)
    ]


def test_no_duplicate_tool_names_across_modules():
    names: list[str] = []
    for mod in (amap_tools, daily_tools, didi_tools, intimate_tools,
                kd100_tools, luckin_tools, mcd_tools, taobao_tools, train_tools):
        names += _spec_names(mod)

    dupes = [n for n, c in Counter(names).items() if c > 1]
    assert not dupes, f"工具重名会互相覆盖: {dupes}"


def test_待办工具全在daily里():
    """2026-08-18：GitHub todo.md 退役，读写都走 bridge 的本地清单。

    2026-09-12：`tools/todo.py` **整个删掉了** —— 那个往 GitHub 写的
    写入器、以及给 `api/server.py` 存档端点用的纯函数，都没有调用者了
    （连端点一起删的）。所以「那个模块不许定义 ToolSpec」这条断言无从评估。

    这条测试守住的是剩下那半句、也是真正值钱的那半句：
    **待办的工具只许在 `daily.py` 里** —— 别处再定义一个同名 ToolSpec
    会静默覆盖，结果是他反而没法往清单里记东西（2026-08-05 真发生过）。
    """
    assert "add_todo" in _spec_names(daily_tools)
    assert "complete_todo" in _spec_names(daily_tools)


def test_add_todo_能传时间模型():
    """没有这几个参数，他就只能记「随时」档 —— 那种永远不会提醒她。"""
    props = daily_tools.ADD_TODO_SPEC.parameters["properties"]
    for key in ("at", "repeat", "due", "weekdays", "times"):
        assert key in props, f"add_todo 少了 {key}，那种时间模型他设不了"
    assert set(props["repeat"]["enum"]) == {
        "anytime", "once", "daily", "weekly", "weekly_count"
    }
