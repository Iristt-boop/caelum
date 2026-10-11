"""行动闸门读户外天气预报（2026-10-11，Runtime Context 设计稿 R3）。

🔴 起因：闸门 09-18 上线，温度从 World Model 的 weather 状态读 —— 而**从来没人写过这个状态**，
env_temp 三周永远是 None，电热毯那条 26°C 规则一次都没生效，闸门又不留日志，没人发现。
糖糖 10-11：温湿度传感器（embodied 0.2）还没买，先读户外天气预报；拿不到就先问。

从使用方断言：走 `ha_switch` 真 handler，看 HA 收没收到开机命令。
"""

from __future__ import annotations

import logging

from embodied.validator import gate, world_snapshot
from tools import ha
from tools.mcp_client import CallResult

BLANKET = "switch.xiaomi_mj1_00f2_electric_blanket"


class Weather:
    def __init__(self, state):
        self.state = state

    def get_state(self, turn=None):
        return self.state


class Client:
    def __init__(self):
        self.calls = []

    def call(self, tool, args=None):
        self.calls.append((tool, args))
        return CallResult(ok=True, text="ok")


def _switch(weather, user_text=""):
    from tools import context
    client = Client()
    h = ha.make_handlers(client, world_ref=None, weather_ref=lambda: weather)
    ctx = context.ToolContext(session_id="s", user_text=user_text)
    with context.bind(ctx):
        out = h["ha_switch"]({"entity_id": BLANKET, "state": "on"})
    turned_on = any(t == "hass_set_state" for t, _ in client.calls)
    return out, turned_on


def test_红线_户外28度_电热毯不开():
    out, on = _switch(Weather({"temp": 28.0}), user_text="她昨晚没睡好，暖一下")
    assert not on, "八月那次事故：夏天开电热毯 —— 现在必须拦住"
    assert "28" in out and "天气预报" in out


def test_户外10度_照常开(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    out, on = _switch(Weather({"temp": 10.0}))
    assert on


def test_红线_天气拿不到_她没开口_先问():
    out, on = _switch(Weather({"available": False, "error": "x"}), user_text="我睡了")
    assert not on
    assert "冷吗" in out


def test_天气拿不到_她自己说冷_开(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    _, on = _switch(Weather({"available": False}), user_text="好冷，开下电热毯")
    assert on


def test_没配和风_weather_ref给None_按不知道():
    _, on = _switch(None, user_text="")
    assert not on


def test_旧值三小时内还算数_再旧就不知道():
    assert world_snapshot(None, Weather({"temp": 20.0, "stale": True, "stale_age_s": 3600}))["env_temp"] == 20.0
    assert world_snapshot(None, Weather({"temp": 20.0, "stale": True, "stale_age_s": 4 * 3600}))["env_temp"] is None


def test_World_Model有温度时优先用它():
    class W:
        def get_state(self, key):
            if key == "weather":
                return type("S", (), {"status": "fresh", "value": {"temperature": 30}})()
            return None
    snap = world_snapshot(lambda: W(), Weather({"temp": 10.0}))
    assert snap["env_temp"] == 30.0 and snap["env_source"] == "world"


def test_天气Provider抛异常_不炸_按不知道():
    class Boom:
        def get_state(self, turn=None):
            raise RuntimeError("和风挂了")
    assert world_snapshot(None, Boom())["env_temp"] is None


def test_红线_拦下要留痕(caplog):
    with caplog.at_level(logging.INFO, logger="embodied.validator"):
        gate(BLANKET, "on", {"env_temp": 28.0, "env_source": "forecast"})
    assert any("environment_conflict" in r.getMessage() for r in caplog.records)


def test_红线_Nox接线_闸门读得到天气Provider(monkeypatch):
    """接线测试：Nox 注册家居工具时，闸门拿到的天气源就是 context 里那个 weather Provider。"""
    from agent.loop import AgentLoop
    from context import ContextProviderRegistry
    from context.base import BaseContextProvider
    from nox import Nox

    class FakeWeather(BaseContextProvider):
        name = "weather"
        section = "environment"

        def _fetch(self, turn):
            return {"temp": 31.0}

        def render(self, state):
            return ""

    n = Nox.__new__(Nox)
    n.loop = AgentLoop(adapter=None)
    n.world = None
    n.context = ContextProviderRegistry()
    client = Client()
    n._register_home(client)
    n.context.register(FakeWeather())          # 和 Nox 里一样：天气比家居晚注册
    from tools import context
    with context.bind(context.ToolContext(session_id="s", user_text="")):
        out = n.loop.tools["ha_switch"].handler({"entity_id": BLANKET, "state": "on"})
    assert not any(t == "hass_set_state" for t, _ in client.calls), out
    assert "31" in out
