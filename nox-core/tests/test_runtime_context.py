"""工牌（Runtime Context，2026-10-11 设计稿 R1）。

糖糖 10-11：「你在哪」不该让他自己去查（guide 五天被读 0 次），每轮由 Caelum 塞到他手里。

守的几条（设计稿第八节）：
  · 常驻：完整 / 轻量 / 语音都带 —— 它是 `_dynamic` 的第一段，不经 classify_context 挑
  · 英文电话情景一个中文字都没有（中文会把英文指令淹没）
  · 预算卡死；object 只认名字和集数，字幕 / token 一律进不来
  · 从使用方断言：抓的是**交给 router / loop 的 dynamic_system**，不只是 render 的返回值
"""

from __future__ import annotations

import logging
import re

import pytest

from types import SimpleNamespace

from agent import toolpacks
from context import ContextProviderRegistry
from context import runtime as rt
from nox import Nox
from tests.test_api import FakeNox, client_for

CJK = re.compile(r"[一-鿿]")


# ---- render / parse --------------------------------------------------------

def test_聊天工牌_说清在哪_能用什么():
    out = rt.render(rt.RuntimeInfo.parse({"surface": "chat", "client": "app"}))
    assert out.startswith("【你在哪】")
    assert "Caelum" in out and "「聊天」" in out and "手机 App" in out
    assert "这里能用：" in out


def test_共影工牌_只到片名和集数():
    out = rt.render(rt.RuntimeInfo.parse(
        {"surface": "watch", "object": {"title": "摩登家庭", "episode": "S03E07"}}))
    assert "《摩登家庭》 S03E07" in out and "「共影」" in out


def test_没填入口_不替它编一个():
    out = rt.render(rt.RuntimeInfo.parse(None))
    assert "你在 Caelum 里。" in out
    assert "「" not in out, "老调用方没报入口，工牌不许猜成聊天"


def test_不认识的入口_按没填处理且留痕(caplog):
    with caplog.at_level(logging.WARNING, logger="context.runtime"):
        info = rt.RuntimeInfo.parse({"surface": "toilet"})
    assert info.surface is None
    assert any("toilet" in r.getMessage() for r in caplog.records), "调用方写错了要留痕"


def test_红线_object白名单_字幕和token进不来(caplog):
    secret = "tok_SHOULD_NEVER_APPEAR_123"
    with caplog.at_level(logging.WARNING, logger="context.runtime"):
        info = rt.RuntimeInfo.parse({"surface": "watch", "object": {
            "title": "摩登家庭", "subtitle": "曼尼加油啊", "token": secret}})
    out = rt.render(info)
    assert secret not in out and "曼尼" not in out
    assert all(secret not in r.getMessage() for r in caplog.records), "日志里只记字段名，不记值"


@pytest.mark.parametrize("surface", sorted(rt.SURFACES))
def test_红线_预算_最长片名也不超(surface):
    info = rt.RuntimeInfo.parse({"surface": surface, "client": "os",
                                 "object": {"title": "长" * 500, "episode": "E" * 500}})
    out = rt.render(info)
    assert len(out) <= rt.MAX_CHARS, f"{surface}: {len(out)} 字 > {rt.MAX_CHARS}\n{out}"


@pytest.mark.parametrize("surface", [None, *sorted(rt.SURFACES)])
def test_红线_英文电话_一个中文字都没有(surface):
    out = rt.render(rt.RuntimeInfo(surface=surface, title="摩登家庭"), english=True)
    assert not CJK.search(out), out


# ---- 接进 _dynamic（使用方断言）-------------------------------------------

def _nox() -> Nox:
    n = Nox.__new__(Nox)
    n._restore_state_once = lambda: None
    n.context = ContextProviderRegistry()
    n.card_debt = {}
    n.loop = SimpleNamespace(tools={}, used_packs=toolpacks.UsedPacks())
    return n


def test_红线_工牌在动态提示最前面_文字聊天():
    d = _nox()._dynamic("在吗", False, runtime={"surface": "chat"})
    assert d.startswith("【你在哪】"), d


def test_红线_轻量路径也带():
    """「晚安」走轻量路径 —— 工牌不许经 classify_context 挑掉。"""
    d = _nox()._dynamic("晚安", False, runtime={"surface": "chat"})
    assert "【你在哪】" in d


def test_红线_电话_没报入口按voice定成call_英文情景纯英文():
    d = _nox()._dynamic("hello", True, scene="en")
    first = d.split("\n\n")[0]
    assert first.startswith("[Now]") and "voice call" in first
    assert not CJK.search(first)


def test_中文电话情景用中文工牌():
    d = _nox()._dynamic("喂", True, scene="zh")
    assert d.startswith("【你在哪】") and "「电话」" in d


def test_开关关掉就没有(monkeypatch):
    monkeypatch.setenv("NOX_RUNTIME_CONTEXT", "off")
    d = _nox()._dynamic("在吗", False, runtime={"surface": "chat"})
    assert "【你在哪】" not in d


class _Stop(Exception):
    pass


def test_红线_chat把runtime一路送到router的dynamic_system():
    n = _nox()
    n._see = lambda text, images, model: (text, images)
    n.adapter_for = lambda model: None
    n._chat_depth = lambda voice: None
    got = {}

    def handle(text, history, *, dynamic_system=None, **kw):
        got["d"] = dynamic_system
        raise _Stop

    n.router = type("R", (), {"handle": staticmethod(handle)})()
    with pytest.raises(_Stop):
        n.chat("在吗", [], runtime={"surface": "watch", "object": {"title": "摩登家庭"}})
    assert "《摩登家庭》" in got["d"]


def test_红线_chat_stream把runtime一路送到loop的dynamic_system():
    n = _nox()
    n._see = lambda text, images, model: (text, images)
    n.adapter_for = lambda model: None
    n._chat_depth = lambda voice: None
    n._system = "SYS"
    got = {}

    def run_stream(text, *, dynamic_system=None, **kw):
        got["d"] = dynamic_system
        raise _Stop
        yield  # noqa: unreachable — 让它是个生成器

    n.loop = type("L", (), {"run_stream": staticmethod(run_stream), "tools": {},
                            "used_packs": toolpacks.UsedPacks()})()
    with pytest.raises(_Stop):
        list(n.chat_stream("在吗", [], runtime={"surface": "chat", "client": "os"}))
    assert "桌面 Caelum OS" in got["d"]


# ---- API：请求字段送到 core --------------------------------------------------

class _RtFake(FakeNox):
    def __init__(self):
        super().__init__()
        self.seen_runtime = []

    def chat(self, *a, **kw):
        self.seen_runtime.append(kw.get("runtime"))
        return super().chat(*a, **kw)

    def chat_stream(self, *a, **kw):
        self.seen_runtime.append(kw.get("runtime"))
        return super().chat_stream(*a, **kw)


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_红线_API把runtime交给core(path):
    fake = _RtFake()
    c = client_for(fake)
    body = {"text": "在吗", "runtime": {"surface": "chat", "client": "app"}}
    r = c.post(path, json=body)
    assert r.status_code == 200
    assert fake.seen_runtime == [{"surface": "chat", "client": "app"}]


# ---- A1 尺子补的两样（10-11）----------------------------------------------

def test_A1_他打来的电话_工牌写清是他打的和为什么():
    """10-05 他主动打来，她问「干嘛为什么给我打电话」—— 工牌原来只写「电话」。"""
    out = rt.render(rt.RuntimeInfo.parse(
        {"surface": "call", "caller": "nox", "call_reason": "想你了，想听听你的声音"}))
    assert "是你打给她的" in out and "想你了" in out


def test_A1_她打来的电话():
    info = rt.RuntimeInfo.parse({"surface": "call", "caller": "her", "call_reason": "不该出现"})
    assert info.call_reason is None, "她拨的没有原因可写 —— 解析时就丢"
    out = rt.render(info)
    assert "是她打给你的" in out and "不该出现" not in out


def test_A1_英文电话_写谁打的_但原因是中文不放():
    out = rt.render(rt.RuntimeInfo.parse({"surface": "call", "caller": "nox", "call_reason": "想你了"}), english=True)
    assert "You called her" in out and not CJK.search(out)


def test_A1_caller写错了_丢掉留痕(caplog):
    with caplog.at_level(logging.WARNING, logger="context.runtime"):
        assert rt.RuntimeInfo.parse({"surface": "call", "caller": "boss"}).caller is None
    assert any("boss" in r.getMessage() for r in caplog.records)


def test_A1_聊天能力里有相册():
    """08-04 她问「你现在能不能发图片」—— 能力表原来没有相册。"""
    assert "相册" in rt.render(rt.RuntimeInfo.parse({"surface": "chat"}))


def test_A1_电话预算_原因最长也不超():
    out = rt.render(rt.RuntimeInfo.parse({"surface": "call", "caller": "nox", "call_reason": "长" * 200}))
    assert len(out) <= rt.MAX_CHARS, out


def test_红线_A1_voice兜底成call时_谁打的不丢():
    d = _nox()._dynamic("hello", True, scene="en", runtime={"caller": "nox"})
    assert "You called her" in d.split("\n\n")[0]


# ------------------------------------------------------------ Guide A4：她在 OS 的哪一页（10-18）


def test_A4_OS的Chat页_说她刚从哪页切过来():
    out = rt.render(rt.RuntimeInfo.parse({"surface": "chat", "client": "os", "page": "chat", "from": "hisday"}))
    assert "她刚从 OS 的「Mind › His Day」页切过来" in out
    assert "桌面 Caelum OS" in out


def test_A4_语音浮层在别的页_说她开着哪页():
    out = rt.render(rt.RuntimeInfo.parse({"surface": "call", "caller": "her", "client": "os", "page": "voices"}))
    assert "她现在开着 OS 的「Life › Voices」页" in out


def test_A4_在Chat页又没有来处_不多说一句():
    out = rt.render(rt.RuntimeInfo.parse({"surface": "chat", "client": "os", "page": "chat"}))
    assert "OS 的「" not in out


def test_A4_不在Chat页时_来处不算数():
    info = rt.RuntimeInfo.parse({"surface": "call", "client": "os", "page": "moments", "from": "hisday"})
    assert info.came_from is None and "His Day" not in rt.render(info)


def test_A4_认不出的页面_丢掉留痕_不编页面名(caplog):
    with caplog.at_level(logging.WARNING, logger="context.runtime"):
        info = rt.RuntimeInfo.parse({"surface": "chat", "client": "os", "page": "secret-room"})
    assert info.page is None
    assert any("secret-room" in r.getMessage() for r in caplog.records)


def test_A4_只认OS报的页面(caplog):
    with caplog.at_level(logging.WARNING, logger="context.runtime"):
        assert rt.RuntimeInfo.parse({"surface": "chat", "client": "app", "page": "moments"}).page is None
        assert rt.RuntimeInfo.parse({"surface": "chat", "page": "moments"}).page is None


def test_A4_英文电话也说页面_一个中文字都没有():
    out = rt.render(rt.RuntimeInfo.parse({"surface": "call", "client": "os", "page": "set-notifications"}), english=True)
    assert "Settings › Notifications" in out and not CJK.search(out)


@pytest.mark.parametrize("surface", sorted(rt.SURFACES))
def test_A4_预算_最长页面名加最长片名也不超(surface):
    longest = max(rt.OS_PAGES, key=lambda k: len(rt.OS_PAGES[k]))
    for page, frm in ((longest, None), ("chat", longest)):
        info = rt.RuntimeInfo.parse({"surface": surface, "client": "os", "page": page, "from": frm,
                                     "caller": "nox", "call_reason": "长" * 200,
                                     "object": {"title": "长" * 500, "episode": "E" * 500}})
        out = rt.render(info)
        assert len(out) <= rt.MAX_CHARS, f"{surface}: {len(out)} 字 > {rt.MAX_CHARS}\n{out}"


def test_A4_OS菜单里的每一页这张表都认得():
    """OS 加了页（10-18 刚加了 Voices）这边忘了补，她在那页问他，他就不知道 —— 拿 OS 的 config.js 对表。"""
    import re
    from pathlib import Path
    cfg = Path(__file__).resolve().parents[2] / "nox-app" / "caelum-os-ui" / "src" / "lib" / "config.js"
    if not cfg.exists():
        pytest.skip("没有 nox-app 检出（worktree 里）")
    src = cfg.read_text(encoding="utf-8")
    nav = src[src.index("export const NAV"):]
    nav = nav[:nav.index("\n];")]
    ids = set(re.findall(r'\{\s*id:\s*"([a-z0-9-]+)"', nav))
    # 带子项的一级项只是索引，点它落到第一个子项 —— 不会停在它自己身上
    parents = set(re.findall(r'id:\s*"([a-z0-9-]+)",\s*\n?\s*label:[^\n]*\n\s*icon:[^\n]*\n(?:\s*/\*\*[\s\S]*?\*/\n)?\s*children', nav))
    parents |= {"mind", "tasks", "life", "studio", "settings"}
    pages = ids - parents
    assert pages - set(rt.OS_PAGES) == set(), f"OS 有、表里没有：{sorted(pages - set(rt.OS_PAGES))}"
    assert set(rt.OS_PAGES) - pages == set(), f"表里有、OS 没有了：{sorted(set(rt.OS_PAGES) - pages)}"


def test_红线_A4_从使用方看_他这一轮真的拿到了页面():
    d = _nox()._dynamic("这个页面怎么用", True, scene="zh",
                        runtime={"surface": "call", "caller": "her", "client": "os", "page": "voices"})
    assert "她现在开着 OS 的「Life › Voices」页" in d.split("\n\n")[0]
