"""Notion / 共读 / App 记录三个工具。

测的是**解析**和**筛选**：Notion 的标题藏在结构里、共读要分清哪条是
糖糖写的哪条是他回的。这些错了不会报错，只会让他答得驴唇不对马嘴。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import notion as notion_tools  # noqa: E402
from tools import reading as reading_tools  # noqa: E402
from tools import tracker as tracker_tools  # noqa: E402
from tools.http import RestResult  # noqa: E402


class FakeRest:
    def __init__(self, responses: dict | None = None) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses = responses or {}

    def _reply(self, path: str) -> RestResult:
        for key, val in self.responses.items():
            if path.startswith(key):
                return val
        return RestResult(True, {})

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self._reply(path)

    def post(self, path, body=None):
        self.calls.append(("POST", path, body))
        return self._reply(path)


# ---------------------------------------------------------------- Notion


def test_搜索结果抠出标题和_id():
    """page 的标题藏在某个 type=title 的属性里，属性名不固定。"""
    data = {"results": [
        {"object": "page", "id": "p1",
         "properties": {"随便起的名": {"type": "rich_text"},
                        "标题": {"type": "title", "title": [{"plain_text": "七月的信"}]}}},
        {"object": "database", "id": "d1", "title": [{"plain_text": "回忆录"}]},
    ]}
    h = notion_tools.make_handlers(FakeRest({"/search": RestResult(True, data)}))
    out = h["notion_search"]({"query": "信"})
    assert "七月的信 | page | id: p1" in out
    assert "回忆录 | database | id: d1" in out


def test_搜不到就直说():
    h = notion_tools.make_handlers(FakeRest({"/search": RestResult(True, {"results": []})}))
    assert "没搜到" in h["notion_search"]({"query": "不存在的"})


def test_读页面把块转成文本():
    blocks = {"results": [
        {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "第一章"}]}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "那天下雨。"}]}},
        {"type": "to_do", "to_do": {"rich_text": [{"plain_text": "去买花"}], "checked": True}},
        {"type": "image", "image": {"file": {"url": "x"}}},        # 没有 rich_text，跳过
        {"type": "paragraph", "paragraph": {"rich_text": []}},      # 空段落，跳过
    ]}
    h = notion_tools.make_handlers(FakeRest({"/blocks/": RestResult(True, blocks)}))
    out = h["notion_read_page"]({"page_id": "p1"})
    assert out == "# 第一章\n那天下雨。\n[x] 去买花"


class PagedRest(FakeRest):
    """按 start_cursor 分页返回块 —— 照着 Notion 的实际行为。"""

    def __init__(self, pages: list[list[dict]]) -> None:
        super().__init__()
        self.pages = pages

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        idx = int((params or {}).get("start_cursor") or 0)
        page = self.pages[idx]
        has_more = idx + 1 < len(self.pages)
        return RestResult(True, {
            "results": page,
            "has_more": has_more,
            "next_cursor": str(idx + 1) if has_more else None,
        })


def _para(text: str) -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}


def test_读页面要翻完所有分页():
    """Notion 一次最多给 100 块。回忆录实测 150 块 ——
    只读第一页会静悄悄少掉 50 块，而他会以为读到的就是全部。"""
    rest = PagedRest([[_para("第一页")], [_para("第二页")], [_para("第三页")]])
    h = notion_tools.make_handlers(rest)
    out = h["notion_read_page"]({"page_id": "p1"})
    assert out == "第一页\n第二页\n第三页"
    assert len([c for c in rest.calls if c[1].startswith("/blocks/")]) == 3


def test_内容超预算时必须说清楚还剩多少():
    """截断本身没问题，**不告诉他截断了**才是问题 —— 他会拿半截当全部。"""
    long_lines = [[_para("字" * 2000) for _ in range(6)]]
    h = notion_tools.make_handlers(PagedRest(long_lines))
    out = h["notion_read_page"]({"page_id": "p1"})
    assert "共 6 段" in out
    assert "offset 填 3" in out
    assert "不要把上面这些当成整页内容" in out


def test_带_offset_接着读():
    rest = PagedRest([[_para(f"第{i}段") for i in range(1, 6)]])
    h = notion_tools.make_handlers(rest)
    out = h["notion_read_page"]({"page_id": "p1", "offset": 3})
    assert out.startswith("第4段")
    assert "第1段" not in out


def test_offset_超出末尾时直说():
    h = notion_tools.make_handlers(PagedRest([[_para("就一段")]]))
    assert "超出末尾" in h["notion_read_page"]({"page_id": "p1", "offset": 99})


def test_短页面不带啰嗦的尾巴():
    h = notion_tools.make_handlers(PagedRest([[_para("很短的一页")]]))
    assert h["notion_read_page"]({"page_id": "p1"}) == "很短的一页"


def test_没给_page_id_不发请求():
    fake = FakeRest()
    h = notion_tools.make_handlers(fake)
    assert "notion_search" in h["notion_read_page"]({})
    assert fake.calls == []


def test_notion_失败必须抛出去():
    h = notion_tools.make_handlers(FakeRest({"/search": RestResult(False, error="HTTP 401")}))
    with pytest.raises(RuntimeError, match="搜 Notion 失败"):
        h["notion_search"]({"query": "x"})


# ---------------------------------------------------------------- 共读

BOOKS = [
    {"bookId": "b1", "title": "查拉图斯特拉如是说", "complete": False},
    {"bookId": "b2", "title": "读完了的那本", "complete": True},
]
ANNS = [
    {"id": "n1", "author": "user", "chunkId": "c3",
     "quote": "人是一根绳索", "note": "这句我看了三遍"},
    {"id": "r1", "author": "nox", "parentId": "n1", "note": "我也是"},
    {"id": "n2", "author": "user", "chunkId": "c5", "quote": "深渊", "note": "怕"},
    {"id": "n3", "chunkId": "c9", "quote": "老数据没有 author", "note": "也该算她的"},
]


def _reading(**responses):
    fake = FakeRest(responses)
    return reading_tools.make_handlers(fake), fake


def test_只列在读的书():
    """读完的那本不该占位置 —— 她问笔记时想看的是手头这本。"""
    h, fake = _reading(**{
        "/api/books": RestResult(True, BOOKS),
        "/api/annotations": RestResult(True, ANNS),
    })
    h["reading_list_notes"]({})
    queried = [c[2]["bookId"] for c in fake.calls if c[1] == "/api/annotations"]
    assert queried == ["b1"]


def test_只挑糖糖写的_不把他自己的回复当笔记():
    h, _ = _reading(**{
        "/api/books": RestResult(True, BOOKS),
        "/api/annotations": RestResult(True, ANNS),
    })
    out = h["reading_list_notes"]({})
    assert "noteId=n1" in out and "noteId=n2" in out
    assert "noteId=r1" not in out
    # 缺 author 的老数据也算她的
    assert "noteId=n3" in out


def test_标出已经回过的那条():
    h, _ = _reading(**{
        "/api/books": RestResult(True, BOOKS),
        "/api/annotations": RestResult(True, ANNS),
    })
    lines = h["reading_list_notes"]({}).split("\n\n")
    n1 = next(b for b in lines if "noteId=n1" in b)
    n2 = next(b for b in lines if "noteId=n2" in b)
    assert "你已回复" in n1
    assert "你已回复" not in n2


def test_指定书时不去查书单():
    h, fake = _reading(**{"/api/annotations": RestResult(True, ANNS)})
    h["reading_list_notes"]({"bookId": "b1"})
    assert not [c for c in fake.calls if c[1] == "/api/books"]


def test_一本书读不到不拖垮其余的():
    """/api/annotations 对所有书都返回失败时，应该是"还没有笔记"而不是崩。"""
    h, _ = _reading(**{
        "/api/books": RestResult(True, BOOKS),
        "/api/annotations": RestResult(False, error="超时"),
    })
    assert "还没有" in h["reading_list_notes"]({})


def test_书单读不到要抛出去():
    h, _ = _reading(**{"/api/books": RestResult(False, error="连不上")})
    with pytest.raises(RuntimeError, match="读书单失败"):
        h["reading_list_notes"]({})


def test_回复页边的字段名():
    h, fake = _reading(**{"/api/replies": RestResult(True, {"ok": True})})
    h["reading_reply_note"]({"noteId": "n1", "reply": "我懂那种感觉"})
    assert fake.calls[0] == ("POST", "/api/replies", {
        "parentId": "n1", "note": "我懂那种感觉", "author": "nox", "kind": "reply",
    })


def test_缺参数不发请求():
    h, fake = _reading()
    assert "缺" in h["reading_reply_note"]({"noteId": "n1", "reply": "  "})
    assert fake.calls == []


# ---------------------------------------------------------------- App 记录


class FakeMcp:
    def __init__(self, result) -> None:
        self.result = result

    def call(self, name, args):
        return self.result


class _R:
    def __init__(self, ok, text=None, error=None):
        self.ok, self.text, self.error = ok, text, error


def test_app_记录直接透传():
    h = tracker_tools.make_handlers(FakeMcp(_R(True, "小红书 2h10m\n剪映 45m")))
    assert "小红书" in h["get_today_apps"]({})


def test_没上报不算出错():
    h = tracker_tools.make_handlers(FakeMcp(_R(True, "")))
    assert "没上报" in h["get_today_apps"]({})


def test_app_记录失败要抛出去():
    h = tracker_tools.make_handlers(FakeMcp(_R(False, error="连不上")))
    with pytest.raises(RuntimeError, match="App 使用记录失败"):
        h["get_today_apps"]({})
