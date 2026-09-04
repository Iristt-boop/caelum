"""`web_search` —— 他睁眼看世界（2026-09-04）。

守的核心是一条：**「没搜到」和「没搜成」不许混成一句话。**

前者是事实（这世上可能真没有），后者是故障。混了的话他会拿训练时的
旧知识硬答，而且语气笃定 —— 她没法从话里看出他其实是瞎的。
这跟共影那条「他必须知道自己没看见」是同一条规矩。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.search import make_handlers  # noqa: E402


class _Res:
    def __init__(self, ok=True, data=None, error=""):
        self.ok, self.data, self.error = ok, data, error


class _FakeTavily:
    """假的 Tavily。记下每次 POST 的 body，好断言参数拼对了没有。"""

    def __init__(self, res=None):
        self.res = res or _Res(True, {"results": []})
        self.calls: list[tuple[str, dict]] = []

    def post(self, path, body=None):
        self.calls.append((path, body or {}))
        return self.res


def _handlers(res=None):
    fake = _FakeTavily(res)
    return make_handlers(fake)["web_search"], fake


_ONE = {
    "results": [
        {"title": "绵羊侦探团", "url": "https://example.com/a",
         "content": "一群绵羊破案的故事。"},
    ],
    "response_time": 1.2,
}


# --------------------------------------------------------------- 参数

def test_默认走便宜的那档():
    """免费档一个月就 1000 credits，advanced 是双倍价，不能默认顶格。"""
    search, fake = _handlers(_Res(True, _ONE))
    search({"query": "绵羊侦探团"})
    _, body = fake.calls[0]
    assert body["search_depth"] == "basic"


def test_deep_才升到_advanced():
    search, fake = _handlers(_Res(True, _ONE))
    search({"query": "x", "deep": True})
    assert fake.calls[0][1]["search_depth"] == "advanced"


def test_news_切到新闻话题():
    search, fake = _handlers(_Res(True, _ONE))
    search({"query": "x", "news": True})
    assert fake.calls[0][1]["topic"] == "news"
    search({"query": "x"})
    assert fake.calls[1][1]["topic"] == "general"


@pytest.mark.parametrize("given,want", [
    (None, 5), (3, 3), (999, 10), (0, 1), ("abc", 5),
])
def test_条数夹在合理区间(given, want):
    """模型可能给 0、给 999、给一句话。夹住，别让它把上下文撑爆。"""
    search, fake = _handlers(_Res(True, _ONE))
    args = {"query": "x"}
    if given is not None:
        args["max_results"] = given
    search(args)
    assert fake.calls[-1][1]["max_results"] == want


def test_空_query_不发请求():
    search, fake = _handlers(_Res(True, _ONE))
    out = search({"query": "   "})
    assert fake.calls == []
    assert "query" in out


# --------------------------------------------------------------- 「没搜成」

def test_请求失败要说得出原因而且警告别硬答():
    """⚠️ 这条是整个文件最重要的。"""
    search, _ = _handlers(_Res(False, error="HTTP 432: credits exhausted"))
    out = search({"query": "绵羊侦探团"})
    assert "没搜成" in out
    assert "credits exhausted" in out      # 原样透出，不含糊成「查询失败」
    assert "旧信息" in out                  # 明确拦住「拿记忆硬答」


def test_没搜到和没搜成说的不是一句话():
    got_none, _ = _handlers(_Res(True, {"results": []}))
    failed, _ = _handlers(_Res(False, error="连不上"))
    a = got_none({"query": "x"})
    b = failed({"query": "x"})
    assert "没搜到" in a and "没搜成" not in a
    assert "没搜成" in b


def test_没搜到时要提醒别当成不存在():
    """中文内容它本来就弱，搜不到不代表这事没有。"""
    search, _ = _handlers(_Res(True, {"results": []}))
    out = search({"query": "某个国产剧"})
    assert "不存在" in out          # 是「别当成不存在」这句提醒


# --------------------------------------------------------------- 结果整理

def test_结果里必须带链接():
    """他要引用来源。没有链接就没法引，只能说得像自己本来就知道。"""
    search, _ = _handlers(_Res(True, _ONE))
    out = search({"query": "绵羊侦探团"})
    assert "https://example.com/a" in out
    assert "绵羊侦探团" in out


def test_正文过长要截断():
    """一条最多 3 个 500 字片段，5 条就 7500 字，全塞进去太占上下文。"""
    long = {"results": [{"title": "t", "url": "u", "content": "长" * 5000}]}
    search, _ = _handlers(_Res(True, long))
    out = search({"query": "x"})
    assert len(out) < 1500
    assert "…" in out


def test_概括要标注是二手的():
    """Tavily 的 answer 是它自己生成的，可能有误。

    不标注的话他会当成检索到的事实直接转述 —— 那就等于凭空多了一层幻觉。
    """
    with_answer = dict(_ONE, answer="绵羊侦探团是一部动画。")
    search, _ = _handlers(_Res(True, with_answer))
    out = search({"query": "x"})
    assert "非原文" in out
    assert "绵羊侦探团是一部动画。" in out


def test_回包缺字段也不炸():
    """results 里混进 None、缺 title/url —— 别让整次搜索挂掉。"""
    messy = {"results": [None, {}, {"title": "只有标题"}]}
    search, _ = _handlers(_Res(True, messy))
    out = search({"query": "x"})
    assert "只有标题" in out


def test_data_不是字典也不炸():
    search, _ = _handlers(_Res(True, "这不是 JSON 对象"))
    out = search({"query": "x"})
    assert "没搜到" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
