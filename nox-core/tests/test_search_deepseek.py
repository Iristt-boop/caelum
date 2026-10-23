"""搜索 DeepSeek 打头、Tavily 兜底；web_read 读整篇；按次记账（10-23，她：「就按你的建议来」）。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from agent import meter
from tools import search as S
from topic_pool import scout

DS_HIT = {"results": [{"title": "底特律八周年", "url": "https://news.17173.com/a", "date": "2026-05-24T16:00:00+00:00",
                       "site_name": "17173", "authority": 3, "summary": "《底特律》发售八周年……", "snippet": "八周年"}]}


class Client:
    def __init__(self, data=None, ok=True, err=None):
        self.data, self.ok, self.err, self.calls = data, ok, err, []

    def post(self, path, body=None):
        self.calls.append((path, body))
        return NS(ok=self.ok, data=self.data, error=self.err)


@pytest.fixture(autouse=True)
def bills(monkeypatch):
    got = []
    monkeypatch.setattr(meter, "record", lambda task, model, usage: got.append((task, model, usage.output_tokens)))
    monkeypatch.setattr(scout, "_DS", None)
    return got


def test_DeepSeek打头_搜到就不问Tavily_记一笔(bills):
    ds, tv = Client(DS_HIT), Client({"results": []})
    out = S.make_handlers(tv, ds)["web_search"]({"query": "底特律 新消息"})
    assert "底特律八周年" in out and "17173 · 2026-05-24 · 可信 3/4" in out and "（DeepSeek）" in out
    assert tv.calls == [] and ds.calls[0] == ("/search", {"query": "底特律 新消息", "count": 5})
    assert bills == [("search", "deepseek-search", 1)]


def test_查新闻只要这一周的():
    ds = Client(DS_HIT)
    S.make_handlers(None, ds)["web_search"]({"query": "AI 新闻", "news": True})
    assert " after:20" in ds.calls[0][1]["query"]


def test_DeepSeek挂了或没搜到_退回Tavily_不记账(bills):
    tv = Client({"results": [{"title": "T", "url": "https://t", "content": "tavily 的"}]})
    for ds in (Client(ok=False, err="HTTP 502: x"), Client({"results": []})):
        out = S.make_handlers(tv, ds)["web_search"]({"query": "x"})
        assert "tavily 的" in out
    assert bills == []


def test_deep_true直接走Tavily():
    ds, tv = Client(DS_HIT), Client({"results": [{"title": "T", "url": "https://t", "content": "c"}]})
    S.make_handlers(tv, ds)["web_search"]({"query": "x", "deep": True})
    assert ds.calls == [] and tv.calls


def test_只有DeepSeek_它也失败_照实说():
    out = S.make_handlers(None, Client(ok=False, err="HTTP 401: x"))["web_search"]({"query": "x"})
    assert "这次没搜成" in out and "401" in out


def test_读网页_成功记一笔_长文截断_读不出指去浏览器(bills):
    long = Client({"title": "长文", "content": "字" * 7000})
    out = S.make_handlers(None, long)["web_read"]({"url": "https://a.com/x"})
    assert out.startswith("《长文》\nhttps://a.com/x") and "后面还有 1000 字" in out
    assert out.count("字") <= S._READ_MAX + 5, "正文要真截到上限，别只是加一句话"
    assert bills == [("web-read", "deepseek-search", 1)]
    bad = S.make_handlers(None, Client({"title": "", "content": ""}))["web_read"]({"url": "https://a.com/y"})
    assert "desk_open" in bad and len(bills) == 1
    assert "http(s)" in S.make_handlers(None, long)["web_read"]({"url": "a.com"})


def test_web_read在desk包里_平常不背():
    from agent.toolpacks import pack_of
    assert pack_of("web_read") == "desk" and pack_of("web_search") is None


def test_登记_有DeepSeek才登记web_read():
    names = []
    loop = NS(register=lambda spec, fn: names.append(spec.name))
    S.register_all(loop, Client(), None)
    assert names == ["web_search"]
    names.clear()
    S.register_all(loop, None, Client())
    assert names == ["web_search", "web_read"]


def test_话题池按兴趣搜走DeepSeek_带日期_记一笔(bills):
    ds = Client(DS_HIT)
    scout.set_deepseek(ds)
    got = scout.ds_search("底特律 变人", days=7)
    assert got[0].source == "deepseek" and got[0].published_at == datetime(2026, 5, 24, 16, tzinfo=timezone.utc)
    assert " after:" in ds.calls[0][1]["query"]
    assert bills == [("topic-search", "deepseek-search", 1)]
    scout.set_deepseek(Client(ok=False, err="x"))
    assert scout.ds_search("x") is None, "失败返回 None，pool 退回 Google 新闻"
