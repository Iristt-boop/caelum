"""HealthProvider 测试。不打网络。

守两件事：
  1. **报数据必须带日期** —— 最近一条不一定是昨天的，她哪天没同步就可能是前天
  2. **不下医疗判断** —— 只摆数字，阈值是通用人群的不是她的基线
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.base import Turn  # noqa: E402
from context.providers.health import HealthProvider  # noqa: E402

# 真实形状：一条记录里两个日期。
#   date=8-02       步数/心率是 8-2 一整天的
#   sleep_date=8-03 这一觉是 8-2 夜里睡、8-3 早上醒的（Apple 归到醒来那天）
SAMPLE = json.dumps({
    "id": 3, "date": "2026-08-02", "sleep_date": "2026-08-03", "steps": 916,
    "distance_km": 0.564, "active_energy": 23.5,
    "resting_heart_rate": 58.0, "hrv_ms": 74.8,
    "sleep_duration_min": 432.0, "deep_sleep_min": 43.0,
    "rem_sleep_min": 88.0, "core_sleep_min": 301.0, "awake_min": 95.0,
}, ensure_ascii=False)


class FakeResult:
    def __init__(self, ok=True, text="", error=None):
        self.ok, self.text, self.error = ok, text, error


class FakeMcp:
    def __init__(self, ok=True, text=SAMPLE):
        self.ok, self.text, self.calls = ok, text, []

    def call(self, tool, args):
        self.calls.append(tool)
        if not self.ok:
            return FakeResult(ok=False, error="health-mcp 连不上")
        return FakeResult(text=self.text)


def test_parses_latest_health():
    s = HealthProvider(FakeMcp()).get_state(Turn())
    assert s["has_data"] is True
    assert s["date"] == "2026-08-02"          # 活动数据的自然日
    assert s["sleep_date"] == "2026-08-03"    # 这一觉的归属日，差一天是对的
    assert s["steps"] == 916
    assert s["sleep_min"] == 432.0


def test_uses_the_renamed_tool():
    """昨天把 get_yesterday_health 改成了 get_latest_health（它取的是最近一条不是昨天）。
    get_menstrual_cycle 是 2026-08-05 加的，同一次 fetch 顺手拉经期。"""
    c = FakeMcp()
    HealthProvider(c).get_state(Turn())
    assert "get_latest_health" in c.calls


def test_sleep_and_activity_carry_different_dates():
    """**这条是这次改动的核心**：睡眠和活动差一天，必须分开说。

    Apple 把睡眠归到醒来那天：8-2 夜里睡、8-3 早上醒 → 健康 App 里是「8月3日」。
    而同一条记录里的步数是 8-2 一整天的。
    揉成一句挂同一个日期，他就会说「你 8-2 睡了 X」，那其实是 8-1 晚上那觉。
    """
    p = HealthProvider(FakeMcp())
    text = p.render(p.get_state(Turn()))

    sleep_line = [l for l in text.splitlines() if "睡眠" in l][0]
    act_line = [l for l in text.splitlines() if "活动" in l][0]

    assert "2026-08-03" in sleep_line and "2026-08-02" not in sleep_line
    assert "2026-08-02" in act_line and "2026-08-03" not in act_line
    assert "7 小时 12 分" in sleep_line
    assert "深睡 43 分" in sleep_line
    assert "916 步" in act_line


def test_old_row_without_sleep_date_says_it_is_unsure():
    """老数据没有 sleep_date，不能默认当成昨晚 —— 要如实说不确定。"""
    old = json.loads(SAMPLE)
    del old["sleep_date"]
    p = HealthProvider(FakeMcp(text=json.dumps(old, ensure_ascii=False)))
    text = p.render(p.get_state(Turn()))
    assert "不确定" in text


def test_render_states_facts_not_verdicts():
    """只摆数字，不下判断 —— check_health_warnings 那套阈值是通用人群的，
    不是糖糖的基线（她平均睡 7.2 小时、深睡本来就偏少）。"""
    p = HealthProvider(FakeMcp())
    text = p.render(p.get_state(Turn()))
    for word in ("异常", "偏高", "偏低", "不足", "警告", "建议"):
        assert word not in text, f"render 里不该出现判断性词汇：{word}"


def test_no_data_is_not_an_error():
    """「还没有数据」是语义上的空，不是故障，不该报 available: False。"""
    s = HealthProvider(FakeMcp(text="暂无数据")).get_state(Turn())
    assert s["has_data"] is False
    assert s.get("available") is not False
    assert HealthProvider(FakeMcp(text="暂无数据")).render(s) == ""


def test_failure_is_not_swallowed():
    """读不到和没有是两件事。吞成空的话他会以为她没有健康数据。"""
    s = HealthProvider(FakeMcp(ok=False)).get_state(Turn())
    assert s["available"] is False
    assert "读健康数据失败" in s["error"]


def test_bad_json_raises():
    s = HealthProvider(FakeMcp(text="这不是 JSON")).get_state(Turn())
    assert s["available"] is False
    assert "不是合法 JSON" in s["error"]


def test_stale_is_marked():
    p = HealthProvider(FakeMcp())
    p.get_state(Turn())
    p.cache.set(p.name, p.cache.get(p.name).value, timedelta(0))
    p.client = FakeMcp(ok=False)

    s = p.get_state(Turn())
    assert s["stale"] is True
    assert "缓存" in p.render(s)


def test_caches_and_is_not_volatile():
    """健康数据一天才更新一次，没必要反复拉。

    ⚠️ **只打一个工具了**（2026-08-19）。以前是两个（health + 经期）——
    经期那个 `get_menstrual_cycle` 读的是 `health.db` 的 menstrual 表，
    那张表被快捷指令写坏过而且停更，写入侧已经切到 World Model。
    现在经期走 `_cycle()` 读 World Model，不再打 MCP。
    """
    c = FakeMcp()
    p = HealthProvider(c)
    for _ in range(5):
        p.get_state(Turn())
    assert c.calls == ["get_latest_health"]
    assert p.volatile is False
    assert p.ttl == timedelta(hours=6)


def test_没有world时经期就是空的():
    """`world_ref` 没给（或者 World Model 还没造出来）时不许崩，
    也不许退回去读那张停更的表 —— 宁可他不知道。"""
    p = HealthProvider(FakeMcp())
    assert p.get_state(Turn())["_menstrual"] == {}


class _Ev:
    def __init__(self, date):
        self.raw = {"event": "start", "date": date}


class _FakeWorld:
    def __init__(self, starts):
        self.starts = starts

    # ⚠️ 形参就叫 `type`（World Model 的真实签名如此），
    # 所以**函数体里不能再用内置的 type()** —— 会被它遮住
    def query(self, type, days=None, limit=30, now=None):
        return [_Ev(d) for d in self.starts]


def test_周期长度从两次start算出来():
    """**不拿 28 天顶** —— 那是「一般女性」的数，她实测 26 天。"""
    p = HealthProvider(FakeMcp(), world_ref=lambda: _FakeWorld(
        ["2026-07-19", "2026-08-14"]))
    mc = p.get_state(Turn())["_menstrual"]
    assert mc["平均周期"] == "26 天"
    assert mc["预计下次"] == "2026-09-09"


def test_只有一次记录就说不知道():
    p = HealthProvider(FakeMcp(), world_ref=lambda: _FakeWorld(["2026-08-14"]))
    mc = p.get_state(Turn())["_menstrual"]
    assert "今天周期第几天" in mc
    assert "平均周期" not in mc, "只有一次 start 时不该编一个周期长度出来"


def test_只有一次记录时不渲染空壳():
    """**这条是 2026-08-20 线上验证时抓到的。**

    原来的 render 无脑拼「平均 {avg}｜预计下次 {next}（{days} 后）」，
    只有一次 start 时那三个值都是空字符串，渲染出来是
    `【经期｜周期第 7 天｜平均 ｜预计下次 （ 后）】` —— 他会照着这段空壳说话。
    """
    p = HealthProvider(FakeMcp(), world_ref=lambda: _FakeWorld(["2026-08-14"]))
    text = p.render(p.get_state(Turn()))
    line = [l for l in text.splitlines() if "经期" in l][0]
    assert "周期第" in line
    assert "预计下次" not in line
    assert "算不出" in line


def test_算得出周期时才说预计下次():
    p = HealthProvider(FakeMcp(), world_ref=lambda: _FakeWorld(
        ["2026-07-19", "2026-08-14"]))
    line = [l for l in p.render(p.get_state(Turn())).splitlines() if "经期" in l][0]
    assert "平均 26 天" in line
    assert "预计下次 2026-09-09" in line


def test_section_is_user():
    """World State 顶层只有 5 个字段，健康归 user（架构文档 UserContext.health）。"""
    assert HealthProvider(FakeMcp()).section == "user"


def test_not_in_the_per_turn_lineup():
    """健康数据日更，每轮塞进去只是白付一段常量文本的钱。
    它是给 Daily Planner 用的。"""
    src = (Path(__file__).resolve().parents[1] / "nox.py").read_text(encoding="utf-8")
    lineup = src.split('self.context.render(')[1].split(')')[0]
    assert '"health"' not in lineup


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
