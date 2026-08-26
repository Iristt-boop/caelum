"""`watching_history` —— 他说得出「我们一起看过什么」（共影 P1，2026-08-22）。

守的核心是一条：**「读不到」和「没看过」不许混成一句话。**
前者是故障，后者是事实。混了的话她会以为他忘了。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.watching import make_handlers  # noqa: E402


class _Res:
    def __init__(self, ok=True, data=None, error=""):
        self.ok, self.data, self.error = ok, data, error


class _FakeBridge:
    """按路径给不同的回应。

    ⚠️ `calls` 记的是 `(path, params)` 两个参数 —— 真的 `RestClient.get()`
    是在**它自己内部**把 params 拼进 query 的，假对象这一层看到的还是分开的两个。
    照着 URL 去断言会永远失败（第一版就是这么错的）。
    """

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        for prefix, res in self.routes.items():
            if path.startswith(prefix):
                return res
        return _Res(ok=False, error="没这个路由")


def _run(routes, args=None):
    bridge = _FakeBridge(routes)
    return make_handlers(bridge)["watching_history"](args or {}), bridge


_NOT_WATCHING = _Res(data={"watching": False, "session": None})


def test_列出一起看过的():
    out, _ = _run({
        "/api/watch/state": _NOT_WATCHING,
        "/api/watch/history": _Res(data={"items": [
            {"id": "a", "title": "盗梦空间", "started_at": "2026-08-21T20:00:00Z",
             "ended_at": "2026-08-21T22:08:00Z"},
        ]}),
    })
    assert "盗梦空间" in out
    assert "2026-08-21" in out
    assert "看了 128 分钟" in out


def test_正在看的排在最前面并且不重复报():
    """正在看的那一场也在 history 里。报两遍会让他说「你在看 X，你们看过 X」。"""
    live = {"id": "now1", "title": "星际穿越", "position_ms": 900_000}
    out, _ = _run({
        "/api/watch/state": _Res(data={"watching": True, "session": live}),
        "/api/watch/history": _Res(data={"items": [
            {"id": "now1", "title": "星际穿越", "started_at": "2026-08-22T20:00:00Z"},
            {"id": "old", "title": "降临", "started_at": "2026-08-10T20:00:00Z",
             "ended_at": "2026-08-10T21:56:00Z"},
        ]}),
    })
    assert out.index("现在正在看") < out.index("一起看过")
    assert out.count("星际穿越") == 1, "正在看的不该在历史里再报一遍"
    assert "第 15 分钟" in out
    assert "降临" in out


def test_真没看过就说没看过():
    out, _ = _run({
        "/api/watch/state": _NOT_WATCHING,
        "/api/watch/history": _Res(data={"items": []}),
    })
    assert "还没一起看过" in out
    assert "不是我忘了" in out, "要让她分得清是「没有」不是「忘了」"


def test_读不到和没看过是两回事():
    """⚠️ **这条是这个文件的重点。**

    bridge 挂了的时候如果回「你们还没一起看过」，她会以为他把昨晚忘了。
    """
    out, _ = _run({
        "/api/watch/state": _NOT_WATCHING,
        "/api/watch/history": _Res(ok=False, error="连不上 bridge"),
    })
    assert "读不到" in out
    assert "还没一起看过" not in out
    assert "连不上 bridge" in out, "要带上真实原因"


def test_现在状态读不到不影响列历史():
    out, _ = _run({
        "/api/watch/state": _Res(ok=False, error="超时"),
        "/api/watch/history": _Res(data={"items": [
            {"id": "a", "title": "降临", "started_at": "2026-08-10T20:00:00Z",
             "ended_at": "2026-08-10T21:56:00Z"},
        ]}),
    })
    assert "读不到她现在在不在看片" in out
    assert "降临" in out, "一半读不到不该把另一半也吞掉"


def test_没结束的那场如实说没看完():
    out, _ = _run({
        "/api/watch/state": _NOT_WATCHING,
        "/api/watch/history": _Res(data={"items": [
            {"id": "a", "title": "某片", "started_at": "2026-08-20T20:00:00Z",
             "ended_at": None},
        ]}),
    })
    assert "没看完就走了" in out


def test_limit夹在合理范围():
    _, bridge = _run({"/api/watch/state": _NOT_WATCHING,
                      "/api/watch/history": _Res(data={"items": []})},
                     {"limit": 999})
    hist = [p for path, p in bridge.calls if path.startswith("/api/watch/history")]
    assert hist and hist[0] == {"limit": 20}, bridge.calls


def test_limit给了脏值也不崩():
    out, _ = _run({"/api/watch/state": _NOT_WATCHING,
                   "/api/watch/history": _Res(data={"items": []})},
                  {"limit": "很多"})
    assert "还没一起看过" in out


def test_没片名时不许渲染出None():
    out, _ = _run({
        "/api/watch/state": _NOT_WATCHING,
        "/api/watch/history": _Res(data={"items": [
            {"id": "a", "started_at": "2026-08-20T20:00:00Z",
             "ended_at": "2026-08-20T21:00:00Z"},
        ]}),
    })
    assert "None" not in out
    assert "没记住片名" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
