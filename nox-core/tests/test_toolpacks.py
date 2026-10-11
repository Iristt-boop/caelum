"""按需工具包（Runtime Context R2，2026-10-11）。

R0：工具表 ≈ 23K token、占固定前缀 85%，164 个里 97 个四周没调过。
回放：核心集 + 按需工具包，后半段漏 1.9%，省一半多。

守的几条：
  · 从使用方断言：抓的是**交给 adapter 的工具表**（流式 / 非流式都抓），不只是 select() 的返回值
  · 没登记进包的工具默认都在 —— 不许悄悄砍
  · 三种挂包：触发词 / 他 30 分钟内用过（失败的也算）/ 工牌入口
  · 顺序不变（顺序一变前缀就变）；没挂的工具他照着历史调了也能执行（兜底）
  · NOX_TOOL_PACKS=off 退回全量
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import toolpacks
from agent.llm import Message, StreamEvent, ToolCall, ToolSpec, Turn, Usage
from agent.loop import AgentLoop
from nox import Nox

NAMES = ["send_meme", "search_food", "amap_weather", "caelum_map", "write_moment",
         "luckin_order", "luckin_shops", "mcd_menu", "computer_browse", "start_long_task",
         "listen_play", "send_gallery_image", "stackchan_look"]


class Adapter:
    name = "fake"

    def __init__(self, script=None):
        self.script = list(script or [])
        self.offered: list[list[str]] = []

    def _next(self, tools):
        self.offered.append([t.name for t in tools])
        return self.script.pop(0) if self.script else Turn(stop_reason="end_turn", text="嗯")

    def complete(self, messages, tools, **kw):
        return self._next(tools)

    def stream(self, messages, tools, **kw):
        turn = self._next(tools)
        if turn.text:
            yield StreamEvent("text", text=turn.text)
        yield StreamEvent("done", turn=turn)


def _loop(script=None) -> AgentLoop:
    loop = AgentLoop(adapter=Adapter(script))
    for n in NAMES:
        loop.register(ToolSpec(name=n, description="测试", parameters={"type": "object", "properties": {}},
                               side_effect="read"), lambda args, n=n: f"{n} ok")
    return loop


def _nox(loop) -> Nox:
    n = Nox.__new__(Nox)
    n.loop = loop
    return n


# ---- select：规则本身 ------------------------------------------------------

def test_闲聊只给核心集_没登记进包的都在():
    offer, mounted = toolpacks.select(NAMES, texts=["今天好累"])
    assert mounted == set()
    assert {"send_meme", "search_food", "write_moment", "caelum_map"} <= offer, "核心集不许少"
    assert not {"luckin_order", "mcd_menu", "computer_browse", "listen_play"} & offer


def test_主动型工具不进包():
    """她旅行时说「到酒店了」，他自己查天气 —— amap 不能被关键词门控。"""
    offer, _ = toolpacks.select(NAMES, texts=["到酒店了"])
    assert "amap_weather" in offer and "caelum_map" in offer


def test_触发词挂包():
    offer, mounted = toolpacks.select(NAMES, texts=["给我点杯生椰拿铁"])
    assert mounted == {"luckin"}
    assert {"luckin_order", "luckin_shops"} <= offer


def test_她的10_11拍板_长任务跟电脑走_相册按需():
    assert toolpacks.pack_of("start_long_task") == "computer"
    assert toolpacks.pack_of("send_gallery_image") == "gallery"
    offer, _ = toolpacks.select(NAMES, texts=["发张相册里的照片给我"])
    assert "send_gallery_image" in offer


@pytest.mark.parametrize("text,pack", [
    ("我的喜欢列表能看到吗？从里面挑一首", "listen"),   # A1 尺子 #19（08-08 她的原话）
    ("能看到网易云吗", "listen"),
    ("你看看能不能看到我", "stackchan"),                # A1 尺子 #20（09-24 她的原话）
])
def test_A1尺子抓到的触发词漏洞(text, pack):
    _, mounted = toolpacks.select(NAMES, texts=[text])
    assert pack in mounted


def test_工牌入口挂包():
    offer, mounted = toolpacks.select(NAMES, texts=["这首好听"], surface="stackchan")
    assert "stackchan" in mounted and "stackchan_look" in offer


def test_开关关掉就是全量(monkeypatch):
    monkeypatch.setenv("NOX_TOOL_PACKS", "off")
    assert toolpacks.select(NAMES, texts=["嗯"]) == (None, set())


def test_用过就留着_30分钟():
    u = toolpacks.UsedPacks()
    u.note("s", ["luckin_shops✗"], now=1000)          # 失败的也算用过
    assert u.recent("s", now=1000 + 29 * 60) == {"luckin"}
    assert u.recent("s", now=1000 + 31 * 60) == set()
    assert u.recent("别的会话", now=1000) == set()
    u.note("s", ["send_gallery_image✗"], now=1000)    # 点名的工具：✗ 不剥就认不出
    assert "gallery" in u.recent("s", now=1000)


# ---- 接进 loop / Nox：从 adapter 收到的工具表断言 --------------------------

def test_红线_非流式_adapter只收到核心集_且顺序不变():
    loop = _loop()
    offer = _nox(loop)._offer("今天好累", [], "s", None)
    loop.run("今天好累", offer=offer, session_id="s")
    got = loop.adapter.offered[0]
    assert "luckin_order" not in got and "send_meme" in got
    assert got == [n for n in NAMES if n in got], "登记顺序变了 —— 前缀一变缓存就冷"


def test_红线_流式_也只收到挑过的():
    loop = _loop()
    offer = _nox(loop)._offer("今天好累", [], "s", None)
    list(loop.run_stream("今天好累", offer=offer, session_id="s"))
    assert "computer_browse" not in loop.adapter.offered[0]


def test_红线_点单多轮_后面那句没关键词也还挂着():
    """回放里训练段漏判 7.7% → 1.3% 主要靠这条：「就第一家」「好」「下单」都没有关键词。"""
    call = Turn(stop_reason="tool_use", tool_calls=[ToolCall(id="c1", name="luckin_shops", arguments={})])
    loop = _loop([call, Turn(stop_reason="end_turn", text="找到三家")])
    n = _nox(loop)
    loop.run("给我点杯生椰拿铁", offer=n._offer("给我点杯生椰拿铁", [], "s", None), session_id="s")
    # 下一轮：历史里没有触发词（模拟隔了好几句），只能靠「用过就留着」
    loop.run("就第一家", offer=n._offer("就第一家", [Message(role="user", text="好")], "s", None),
             session_id="s")
    assert "luckin_order" in loop.adapter.offered[-1]


def test_红线_流式也记用过():
    call = Turn(stop_reason="tool_use", tool_calls=[ToolCall(id="c1", name="mcd_menu", arguments={})])
    loop = _loop([call, Turn(stop_reason="end_turn", text="菜单在这")])
    list(loop.run_stream("麦当劳有啥", offer=None, session_id="s"))
    assert loop.used_packs.recent("s") == {"mcd"}


def test_历史里最近两句也算触发词():
    loop = _loop()
    hist = [Message(role="user", text="想喝瑞幸"), Message(role="assistant", text="好呀")]
    assert "luckin_order" in _nox(loop)._offer("要冰的", hist, "s2", None)


def test_没挂的工具他照着历史调了_照样能执行():
    """漏挂时的兜底：给模型看的少了，执行表没少 —— 不报「没有这个工具」。"""
    call = Turn(stop_reason="tool_use", tool_calls=[ToolCall(id="c1", name="computer_browse", arguments={})])
    loop = _loop([call, Turn(stop_reason="end_turn", text="看了")])
    r = loop.run("嗯", offer={"send_meme"}, session_id="s")
    res = [x for m in r.messages for x in m.tool_results]
    assert res and not res[0].is_error, res


def test_手搓的Nox没有loop_给全量不炸():
    assert Nox.__new__(Nox)._offer("嗯", [], "s", None) is None


class _Stop(Exception):
    pass


def _full_nox(loop):
    from context import ContextProviderRegistry
    from router.router import Router
    n = _nox(loop)
    n._restore_state_once = lambda: None
    n.context = ContextProviderRegistry()
    n.card_debt = {}
    n._see = lambda text, images, model: (text, images)
    n.adapter_for = lambda model: None
    n._chat_depth = lambda voice: None
    n._system = "SYS"
    n.router = Router(loop, "SYS")
    return n


def test_红线_Nox_chat_经router把挑好的工具表送到adapter():
    loop = _loop()
    n = _full_nox(loop)
    n._flush_dirty = lambda r: (_ for _ in ()).throw(_Stop())   # 跑完 loop 就停，后处理不管
    with pytest.raises(_Stop):
        n.chat("帮我查查这道菜多少热量", [], session_id="s")    # 不走轻量路径的一句
    assert loop.adapter.offered, "没走到 loop"
    assert "luckin_order" not in loop.adapter.offered[0] and "search_food" in loop.adapter.offered[0]


def test_红线_Nox_chat_stream_把挑好的工具表送到adapter():
    loop = _loop()
    n = _full_nox(loop)
    for _ in n.chat_stream("给我点杯拿铁", [], session_id="s"):
        if loop.adapter.offered:
            break
    assert "luckin_order" in loop.adapter.offered[0] and "mcd_menu" not in loop.adapter.offered[0]
