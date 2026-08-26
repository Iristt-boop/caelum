"""HomeProvider 测试。不打网络，ha-mcp 用假的。

重点守：**「查不到」不能被当成「关着」**。
HA 在设备离线时保留断电前的最后状态，看着跟正常的一模一样 ——
2026-07-27 床头灯就是这么骗了他一次，他理直气壮说「灯开好了」而灯根本没亮。
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.base import Turn  # noqa: E402
from context.providers.home import HomeProvider  # noqa: E402

SNAPSHOT = """家里现在的状态：
- 主卧 风扇：off
- 主卧 电热毯：off
- 客厅 电视：off
- 电竞房 空调：cool（室温 29°，设定 26°）
- 主卧 空调：off
- 客厅 空调：off
- 主卧 床头灯：on（亮度 10%）
- 主卧 风扇摆风：查不到(HTTP 404)"""


class FakeResult:
    def __init__(self, ok=True, text="", error=None):
        self.ok, self.text, self.error = ok, text, error


class FakeMcp:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def call(self, tool, args):
        self.calls.append(tool)
        if not self.ok:
            return FakeResult(ok=False, error="ha-mcp 连不上")
        return FakeResult(text=SNAPSHOT)


def test_uses_snapshot_not_eight_round_trips():
    """一个往返拿全部，而不是对 8 个设备逐个查。"""
    c = FakeMcp()
    HomeProvider(c).get_state(Turn())
    assert c.calls == ["hass_snapshot"]


def test_parses_devices():
    s = HomeProvider(FakeMcp()).get_state(Turn())
    assert len(s["devices"]) == 8
    assert s["on_count"] == 2          # 床头灯 on + 电竞房空调 cool
    assert s["unknown_count"] == 1


def test_unknown_is_not_treated_as_off():
    """**这条是 2026-07-27 那次故障买来的。**

    设备离线时 HA 保留断电前的最后状态，看着跟正常的一模一样。
    把「查不到」算成「关着」，他就会理直气壮地说错话。
    """
    p = HomeProvider(FakeMcp())
    s = p.get_state(Turn())
    text = p.render(s)
    assert "查不到状态" in text
    assert "别当成关着的" in text


def test_failure_is_not_swallowed():
    """吞成空字典的话，他会以为家里什么都没开。"""
    s = HomeProvider(FakeMcp(ok=False)).get_state(Turn())
    assert s["available"] is False
    assert "读家居状态失败" in s["error"]


def test_stale_says_how_old():
    p = HomeProvider(FakeMcp())
    p.get_state(Turn())
    p.cache.set(p.name, p.cache.get(p.name).value, timedelta(0))
    p.client = FakeMcp(ok=False)

    s = p.get_state(Turn())
    assert s["stale"] is True
    assert "秒前的状态" in p.render(s)


def test_ttl_is_30s_and_not_volatile():
    p = HomeProvider(FakeMcp())
    assert p.ttl == timedelta(seconds=30)
    assert p.volatile is False


def test_caches_within_ttl():
    c = FakeMcp()
    p = HomeProvider(c)
    for _ in range(4):
        p.get_state(Turn())
    assert len(c.calls) == 1, "30 秒内不该反复打 ha-mcp"


def test_section_is_environment():
    """World State 顶层只有 5 个字段，home 挂在 environment 下。"""
    assert HomeProvider(FakeMcp()).section == "environment"


def test_not_in_the_per_turn_lineup():
    """架构文档第八节：只有家居类请求才加载 home，普通聊天不加载。
    在 Router 的 Context 分级做好之前它不该被调用。"""
    src = (Path(__file__).resolve().parents[1] / "nox.py").read_text(encoding="utf-8")
    lineup = src.split('self.context.render(')[1].split(')')[0]
    assert '"home"' not in lineup


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
