"""日常工具（相册 / 待办 / 日记）。

重点测的不是"能调通"，是那几个**接口约定**：
字段名、必须显式传的参数、以及"发图"这个动作怎么跨进程传出去。
这几处错了不会报错，只会静悄悄干错事 —— 收藏变成取消收藏、
日记署名成糖糖。
"""

from __future__ import annotations

import pytest

from tools import context
from tools.bridge_client import BridgeResult
from tools.daily import make_handlers


class FakeBridge:
    """记下每次调用，按路径返回预设数据。"""

    def __init__(self, responses: dict | None = None) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses = responses or {}

    def _reply(self, path: str) -> BridgeResult:
        if path in self.responses:
            return self.responses[path]
        return BridgeResult(True, {"ok": True})

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self._reply(path)

    def post(self, path, body=None):
        self.calls.append(("POST", path, body))
        return self._reply(path)


IMG = {"id": "img-1", "url": "/uploads/a.jpg", "album": "夏天", "favorited": 0}


def _handlers(**responses):
    bridge = FakeBridge(responses)
    return make_handlers(bridge), bridge


# ---------------------------------------------------------------- 相册


def test_发图把图片记进上下文():
    h, _ = _handlers(**{"/api/gallery/list": BridgeResult(True, [IMG])})
    with context.scope() as ctx:
        out = h["send_gallery_image"]({"filter": "recent"})

    # Core 够不着 bridge 那条 SSE 连接，只能把意图挂在上下文里传出去
    assert len(ctx.attachments) == 1
    assert ctx.attachments[0]["url"] == "/uploads/a.jpg"
    assert ctx.attachments[0]["album"] == "夏天"
    assert "已发送" in out


def test_相册空的时候不报错也不挂附件():
    h, _ = _handlers(**{"/api/gallery/list": BridgeResult(True, [])})
    with context.scope() as ctx:
        out = h["send_gallery_image"]({})
    assert ctx.attachments == []
    assert "没有" in out


def test_不在轮次里也不炸():
    """被直接调用（没有 scope）时只记日志，不能抛异常。"""
    h, _ = _handlers(**{"/api/gallery/list": BridgeResult(True, [IMG])})
    out = h["send_gallery_image"]({})
    assert "已发送" in out


def test_收藏筛选走收藏那条():
    h, bridge = _handlers(**{"/api/gallery/list": BridgeResult(True, [IMG])})
    with context.scope():
        h["send_gallery_image"]({"filter": "favorited"})
    assert bridge.calls[0][2] == {"filter": "favorites"}


def test_读相册失败必须抛出去():
    """不能吞成一句"失败了" —— 那样他会接着编"图发过去了"。"""
    h, _ = _handlers(**{"/api/gallery/list": BridgeResult(False, error="连不上")})
    with pytest.raises(RuntimeError, match="读相册失败"):
        h["send_gallery_image"]({})


def test_收藏必须显式传_favorited():
    """那个接口是 UPDATE favorited=?，不传等于传 false —— 会取消收藏。"""
    h, bridge = _handlers(**{"/api/gallery/list": BridgeResult(True, [IMG])})
    h["favorite_image"]({})
    method, path, body = bridge.calls[-1]
    assert (method, path) == ("POST", "/api/gallery/img-1/favorite")
    assert body == {"favorited": True}


def test_收藏指定_id_时不用先查列表():
    h, bridge = _handlers()
    h["favorite_image"]({"image_id": "img-9"})
    assert len(bridge.calls) == 1
    assert bridge.calls[0][1] == "/api/gallery/img-9/favorite"


# ---------------------------------------------------------------- 待办


def test_加待办带上时间和日期():
    """`time` 是旧参数名，2026-08-18 改叫 `at`，但老会话里他可能还这么传 —— 仍然认。"""
    h, bridge = _handlers()
    out = h["add_todo"]({"text": "买猫粮", "time": "14:30", "date": "2026-07-29"})
    assert bridge.calls[0] == (
        "POST", "/api/today",
        {"text": "买猫粮", "at": "14:30", "date": "2026-07-29"},
    )
    assert "买猫粮" in out


def test_加循环待办带上时间模型():
    """糖糖 2026-08-18 举的三个例子，他都得能设出来。"""
    h, bridge = _handlers()
    h["add_todo"]({"text": "健身", "repeat": "weekly_count", "times": 3, "at": "20:00"})
    assert bridge.calls[0][2] == {
        "text": "健身", "at": "20:00", "repeat": "weekly_count", "times": 3,
    }


def test_没时间的待办要说清楚不会提醒():
    """不说的话她以为记了就会被叫，到点没动静 —— 比没记还糟。"""
    h, _ = _handlers(**{"/api/today": BridgeResult(True, {"ok": True, "repeat": "anytime"})})
    out = h["add_todo"]({"text": "买传感器"})
    assert "不会来叫你" in out


def test_划掉循环待办要说明它明天还回来():
    h, _ = _handlers(**{"/api/todo/complete": BridgeResult(
        True, {"ok": True, "text": "背单词", "closed": False, "doneThisWeek": 2})})
    out = h["complete_todo"]({"keyword": "背单词"})
    assert "明天还会回来" in out


def test_划掉一次性待办就是划掉():
    h, _ = _handlers(**{"/api/todo/complete": BridgeResult(
        True, {"ok": True, "text": "预约复查", "closed": True})})
    out = h["complete_todo"]({"keyword": "复查"})
    assert "划掉了" in out


def test_空待办不发请求():
    h, bridge = _handlers()
    h["add_todo"]({"text": "   "})
    assert bridge.calls == []


def test_待办清单标出没做的():
    rows = [
        {"text": "买猫粮", "time": "14:30", "done": 0},
        {"text": "写稿", "time": "", "done": 1},
    ]
    h, _ = _handlers(**{"/api/today": BridgeResult(True, rows)})
    out = h["get_todos"]({})
    assert "⬜ 14:30 买猫粮" in out
    assert "✅ 写稿" in out
    assert "1 条没做" in out


def test_待办为空说清楚是哪天():
    h, _ = _handlers(**{"/api/today": BridgeResult(True, [])})
    assert "2026-07-29" in h["get_todos"]({"date": "2026-07-29"})


# ---------------------------------------------------------------- 日记


def test_日记字段名跟着_bridge_走():
    """正文字段叫 content 不叫 body，author 必须显式传 Nox ——
    缺省会署名糖糖，还会触发 AI 评论（他给自己的日记写评论）。"""
    h, bridge = _handlers()
    h["write_diary"]({"body": "今天她睡得晚。", "mood": "安静"})
    _, path, sent = bridge.calls[0]
    assert path == "/api/diary"
    assert sent == {"content": "今天她睡得晚。", "author": "Nox", "mood": "安静"}


def test_空日记不发请求():
    h, bridge = _handlers()
    h["write_diary"]({"body": ""})
    assert bridge.calls == []


def test_写日记失败必须抛出去():
    h, _ = _handlers(**{"/api/diary": BridgeResult(False, error="HTTP 403")})
    with pytest.raises(RuntimeError, match="写日记失败"):
        h["write_diary"]({"body": "写点什么"})
