"""国内 MCP 五件套的包装层测试（高德/滴滴/快递100/12306 + scout 热榜）。

全走 FakeMcp，不打网络。冒烟（真连服务端）在 tests/smoke_*.py。

红线守卫：**滴滴没有下单工具** —— tools/didi.py 是糖糖 2026-09-05 定的边界
（他算好价格、发链接，扣扳机的永远是她）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import amap as amap_tools  # noqa: E402
from tools import didi as didi_tools  # noqa: E402
from tools import kd100 as kd100_tools
from tools import mcd as mcd_tools  # noqa: E402
from tools import train as train_tools  # noqa: E402
from topic_pool import scout  # noqa: E402


class FakeResult:
    def __init__(self, ok=True, text="", error=None):
        self.ok, self.text, self.error = ok, text, error


class FakeMcp:
    """记下每次调用的 (tool, args)，按预设返回。"""

    def __init__(self, ok=True, text=""):
        self.ok, self.text = ok, text
        self.error = "连接失败"
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool, args):
        self.calls.append((tool, args))
        if not self.ok:
            return FakeResult(ok=False, error=self.error)
        return FakeResult(text=self.text)


# ------------------------------------------------------------ 通用行为


@pytest.mark.parametrize("module", [amap_tools, didi_tools, kd100_tools, mcd_tools, train_tools])
def test_success_passthrough(module):
    """调用成功 → 服务端文本原样返回。"""
    client = FakeMcp(ok=True, text="返回内容")
    handlers = module.make_handlers(client)
    first_name = list(handlers)[0]
    out = handlers[first_name]({})
    assert out == "返回内容"
    assert client.calls[0][0] == module.SERVER_TOOLS[first_name]


@pytest.mark.parametrize("module", [amap_tools, didi_tools, kd100_tools, mcd_tools, train_tools])
def test_failure_raises_not_silence(module):
    """MCP 失败必须 raise —— 吞了他会编造「查到了」（ha.py 的老教训）。"""
    handlers = module.make_handlers(FakeMcp(ok=False))
    first_name = list(handlers)[0]
    with pytest.raises(RuntimeError):
        handlers[first_name]({})


def test_server_tool_mapping_covers_all_specs():
    """每个 ToolSpec 都要有对应的服务端工具名映射，防手滑。"""
    for module in (amap_tools, didi_tools, kd100_tools, mcd_tools, train_tools):
        spec_names = {s.name for s in module._SPECS}
        assert spec_names == set(module.SERVER_TOOLS)


# ------------------------------------------------------------ 滴滴边界


def test_didi_has_no_ordering_tools():
    """🔴 他不下单：create/cancel 不许出现在注册清单里。"""
    names = {s.name for s in didi_tools._SPECS}
    assert "didi_order" not in names
    for s in didi_tools._SPECS:
        assert "create" not in didi_tools.SERVER_TOOLS[s.name]
        assert "cancel" not in didi_tools.SERVER_TOOLS[s.name]


# ------------------------------------------------------------ scout 中文热榜


class _TrendsClient:
    def __init__(self, ok=True, text=""):
        self.ok, self.text = ok, text
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool, args):
        self.calls.append((tool, args))
        if not self.ok:
            return FakeResult(ok=False, error="桥挂了")
        return FakeResult(text=self.text)


WEIBO_SAMPLE = (
    "<title>避孕药 血栓</title>\n"
    "<description>避孕药 血栓</description>\n"
    "<popularity>1183226</popularity>\n"
    "<link>https://s.weibo.com/weibo?q=x</link>\n"
    "<title>如何看待某长问题标题特别长的知乎风条目需要被完整保留下来呢</title>\n"
    "<description>近日，江苏徐州一家饭店停业后……</description>\n"
)


def test_cn_trending_parses_xml_blocks(monkeypatch):
    """服务端实测返回 XML 标签块：title/link/popularity 各取所长。"""
    client = _TrendsClient(text=WEIBO_SAMPLE)
    monkeypatch.setattr(scout, "_TRENDS", {"client": client})
    out = scout.cn_trending("get_weibo_trending")
    titles = [c.title for c in out]
    assert "避孕药 血栓" in titles
    assert "如何看待某长问题标题特别长的知乎风条目需要被完整保留下来呢" in titles
    first = out[0]
    assert first.url == "https://s.weibo.com/weibo?q=x"
    assert "热度 1183226" in first.summary
    assert all(c.source == "trendshub" for c in out)
    assert all(c.source_id.startswith("trend:") for c in out)
    assert all(c.category == "" for c in out)  # 方向由 DIRECTIONS 盖
    assert client.calls == [("get_weibo_trending", {})]


def test_cn_trending_douban_rating(monkeypatch):
    """豆瓣源把评分拼进 summary。"""
    client = _TrendsClient(text=(
        "<type_name>电影</type_name>\n"
        "<title>杀死比尔：血色全传</title>\n"
        "<info>美国 / 动作 犯罪</info>\n"
        "<link>https://m.douban.com/movie/subject/10756537/</link>\n"
        "<popularity>1193000</popularity>\n"
        "<rating_count>77873</rating_count>\n"
        "<rating_value>8.7</rating_value>\n"
    ))
    monkeypatch.setattr(scout, "_TRENDS", {"client": client})
    out = scout.cn_trending("get_douban_rank", {"type": "movie"})
    assert out[0].title == "杀死比尔：血色全传"
    assert "豆瓣 8.7 分（77873 人评）" in out[0].summary
    assert client.calls == [("get_douban_rank", {"type": "movie"})]


def test_cn_trending_bilibili_author(monkeypatch):
    """B站源把 UP 主拼进 summary；超长 description 截断。"""
    client = _TrendsClient(text=(
        "<title>万人追更的终南山怪虫，只有我们拍到了真相！</title>\n"
        "<description>" + "很" * 300 + "</description>\n"
        "<author>中国国家地理</author>\n"
        "<view>3228934</view>\n"
        "<link>https://b23.tv/BV1yuto66E8J</link>\n"
    ))
    monkeypatch.setattr(scout, "_TRENDS", {"client": client})
    out = scout.cn_trending("get_bilibili_rank")
    assert out[0].title == "万人追更的终南山怪虫，只有我们拍到了真相！"
    assert "UP：中国国家地理" in out[0].summary
    assert len(out[0].summary) <= 160


def test_cn_trending_inactive_without_client(monkeypatch):
    """没配 trends 桥 → 静默返回空（本地开发的默认状态）。"""
    monkeypatch.setattr(scout, "_TRENDS", {"client": None})
    assert scout.cn_trending("get_weibo_trending") == []


def test_cn_trending_failure_returns_empty(monkeypatch):
    """桥挂了只跳过自己 —— 不能连累同方向的其他抓取器。"""
    monkeypatch.setattr(scout, "_TRENDS", {"client": _TrendsClient(ok=False)})
    assert scout.cn_trending("get_zhihu_trending") == []


def test_trends_wired_into_existing_directions():
    """中文源挂在既有方向上（weird/film/books）—— 不新增类别。"""
    def consts(f):
        return str(getattr(f, "__code__", f).co_consts) if hasattr(f, "__code__") else str(f)
    assert any("get_weibo_trending" in consts(f) for f in scout.DIRECTIONS["weird"])
    assert any("get_zhihu_trending" in consts(f) for f in scout.DIRECTIONS["weird"])
    assert any("get_bilibili_rank" in consts(f) for f in scout.DIRECTIONS["weird"])
    assert any("get_douban_rank" in consts(f) for f in scout.DIRECTIONS["film"])
    assert any("get_weread_rank" in consts(f) for f in scout.DIRECTIONS["books"])


# ------------------------------------------------------------ 麦当劳边界


def test_mcd_action_tools_require_confirmation():
    """🟡 动作工具已放开（糖糖 2026-09-05 晚拍板），但必须确认制：
    四个动作工具的描述里必须写明「确认」流程；白名单外的动作类仍不许注册。"""
    action = {s.name: s for s in mcd_tools.ACTION_SPECS}
    assert set(action) == {"mcd_create_order", "mcd_draw_lottery",
                           "mcd_bind_coupons", "mcd_create_address"}
    for name, s in action.items():
        if name == "mcd_bind_coupons":
            continue  # 领券不花钱，她说领就领
        assert "确认" in s.description, f"{name} 的描述缺确认制"
    # 仍未放行的动作类：团餐下单 / 商城下单
    registered = set(mcd_tools.SERVER_TOOLS.values())
    assert "party-order-create" not in registered
    assert "mall-create-order" not in registered
