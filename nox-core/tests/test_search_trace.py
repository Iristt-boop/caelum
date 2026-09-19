"""web_search 要把「搜到哪些网页」带出来（糖糖 2026-09-19）。

她的原话：「`web_search` 的话就是 `web_search`，但是下方要列都搜索了哪些网址，
要有来源」。

所以搜到的每条结果都上报一条子步骤（`type="source"` + `url`），前端渲染成
能点开的链接 + 域名。**只报标题和链接** —— 正文片段是给模型读的那一份。

⚠️ 同时钉住「模型读到的那份没变」：来源是**加**了一层展示，不是把结果换了。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import context as tool_context
from tools import search


class FakeClient:
    """只实现 web_search 用到的那一个方法（`res.ok` / `res.data`）。"""

    def __init__(self, data):
        self._data = data

    def post(self, path, body):  # noqa: ARG002
        return SimpleNamespace(ok=True, data=self._data, error=None)


def test_web_search_reports_each_source_as_a_step():
    data = {
        "response_time": 1.2,
        "results": [
            {"title": "绵羊侦探团", "url": "https://zh.example/x", "content": "正文" * 400},
            {"title": "", "url": "https://b.example/y", "content": "正文"},
            {"title": "没有链接的一条", "content": "正文"},  # 没 url → 不报
        ],
    }
    ctx = tool_context.ToolContext()
    handlers = search.make_handlers(FakeClient(data))

    with tool_context.bind(ctx):
        out = handlers["web_search"]({"query": "绵羊侦探团"})

    steps = ctx.pending_steps
    assert [s.get("url") for s in steps] == ["https://zh.example/x", "https://b.example/y"], \
        "每条有链接的结果都该上报一条来源"
    assert steps[0]["type"] == "source"
    assert steps[0]["desc"] == "绵羊侦探团"
    #: 没标题的那种用链接兜底 —— 前端那行显示成空就等于列了个寂寞
    assert steps[1]["desc"] == "https://b.example/y"

    #: 给模型读的那份没动：链接照样在里面（他要引用来源）
    assert "https://zh.example/x" in out
    assert "搜到 3 条" in out


def test_没有结果时不报来源也不炸():
    ctx = tool_context.ToolContext()
    handlers = search.make_handlers(FakeClient({"results": []}))

    with tool_context.bind(ctx):
        out = handlers["web_search"]({"query": "什么也没搜到"})

    assert ctx.pending_steps == []
    assert "没搜到结果" in out
