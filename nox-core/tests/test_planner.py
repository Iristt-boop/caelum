"""Daily Planner 测试。

守的是这几条：
  - 早报不靠关键词猜，一次拉全套
  - 一个 Provider 挂了，简报照出，但要在 unavailable 里留痕
  - 拿到的是旧数据要标 stale，不能当新鲜的说
  - 数据全没有时**不推送**，而不是让他编一句
  - 生成的推送文本不超长、不带 ||| 分段标记

全部不打网络。
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message  # noqa: E402
from context import BaseContextProvider, ContextProviderRegistry  # noqa: E402
from planner.daily import DEFAULT_SECTIONS, build_brief  # noqa: E402
from planner.push import (  # noqa: E402
    MAX_PUSH_CHARS, finalize_push_text, prepare_morning,
)


class Fake(BaseContextProvider):
    """可控的假 Provider。"""

    ttl = timedelta(minutes=5)

    def __init__(self, name: str, payload: dict, *, fail: bool = False, **kw):
        self.name = name
        super().__init__(**kw)
        self.payload = payload
        self.fail = fail
        self.calls = 0

    def _fetch(self, turn) -> dict:
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.name} 上游挂了")
        return dict(self.payload)

    def render(self, state) -> str:
        if state.get("available") is False:
            return ""
        return f"【{self.name}】{state.get('say', '')}"


def _registry(*providers) -> ContextProviderRegistry:
    reg = ContextProviderRegistry()
    for p in providers:
        reg.register(p)
    return reg


# ------------------------------------------------------------ 聚合

def test_brief_pulls_everything_without_keywords():
    """早报不看她说了什么，全都要 —— 这正是它和 classify_context 的区别。"""
    reg = _registry(
        Fake("time", {"date": "2026-08-04", "weekday": "周二", "clock": "10:00", "say": "上午十点"}),
        Fake("health", {"say": "昨晚睡了 5 小时 40 分"}),
        Fake("todo", {"say": "今天要交装修合同"}),
    )
    b = build_brief(reg, sections=["time", "health", "todo"])

    assert b.date == "2026-08-04"
    assert b.weekday == "周二"
    assert "昨晚睡了" in b.text
    assert "装修合同" in b.text
    assert not b.degraded


def test_missing_provider_is_reported_not_silent():
    """没注册的 Provider 要在 missing 里留痕，不能悄悄少一栏。"""
    reg = _registry(Fake("time", {"date": "2026-08-04", "weekday": "周二",
                                  "clock": "10:00", "say": "上午"}))
    b = build_brief(reg, sections=["time", "health", "todo"])

    assert b.missing == ["health", "todo"]
    assert b.degraded


def test_failed_provider_lands_in_unavailable():
    """Provider 挂了，简报照出，但要说出来缺了哪一项。"""
    reg = _registry(
        Fake("time", {"date": "2026-08-04", "weekday": "周二", "clock": "10:00", "say": "上午"}),
        Fake("health", {}, fail=True),
    )
    b = build_brief(reg, sections=["time", "health"])

    assert b.unavailable == ["health"]
    assert b.degraded
    assert "上午" in b.text          # 没挂的那个照常出现


def test_stale_data_is_flagged():
    """退回旧数据时要标 stale —— 他可以说旧的，但得说明是旧的。"""
    h = Fake("health", {"say": "睡了 6 小时"})
    reg = _registry(h)

    build_brief(reg, sections=["health"], force_refresh=True)   # 先存一份
    h.fail = True
    b = build_brief(reg, sections=["health"], force_refresh=True)

    assert b.stale == ["health"]
    assert "睡了 6 小时" in b.text


def test_force_refresh_bypasses_cache():
    """早报默认强制刷新 —— health 的 TTL 是「当天」，
    不刷新的话早上拿到的很可能还是昨天那条睡眠记录。"""
    h = Fake("health", {"say": "睡了 6 小时"})
    reg = _registry(h)

    build_brief(reg, sections=["health"])
    build_brief(reg, sections=["health"])
    assert h.calls == 2                              # 两次都真去拉了

    build_brief(reg, sections=["health"], force_refresh=False)
    assert h.calls == 2                              # 这次吃了缓存


def test_memory_is_opt_in():
    """memory 一次检索 7 秒，默认不进早报。"""
    m = Fake("memory", {"say": "最近在聊装修"})
    reg = _registry(m)

    build_brief(reg, sections=["time"])
    assert m.calls == 0

    build_brief(reg, sections=["time"], include_memory=True)
    assert m.calls == 1


def test_default_sections_order_puts_health_before_weather():
    """超预算时从后往前截 —— 「你昨晚没睡好」比「今天多云」重要。"""
    assert DEFAULT_SECTIONS.index("health") < DEFAULT_SECTIONS.index("weather")
    assert DEFAULT_SECTIONS.index("todo") < DEFAULT_SECTIONS.index("weather")


def test_budget_truncates_and_says_so():
    """超预算要明说略过了什么，不能悄悄少说。"""
    reg = _registry(
        Fake("time", {"date": "2026-08-04", "weekday": "周二", "clock": "10:00", "say": "上午"}),
        Fake("health", {"say": "睡" * 200}),
        Fake("todo", {"say": "今天要交装修合同"}),
    )
    b = build_brief(reg, sections=["time", "health", "todo"], budget=60)
    assert "没放进来" in b.text


# ------------------------------------------------------------ 取数与拼词

def _two_provider_registry():
    return _registry(
        Fake("time", {"date": "2026-08-04", "weekday": "周二", "clock": "10:00", "say": "上午"}),
        Fake("health", {"say": "昨晚睡了 5 小时 40 分"}),
    )


def test_prepare_builds_prompt_with_brief():
    p = prepare_morning(_two_provider_registry(), include_memory=False)

    assert not p.skipped
    assert "5 小时 40 分" in p.prompt      # 简报进了递给他的话
    assert "锁屏" in p.prompt              # 指令也在
    assert p.text == ""                    # 这一步还没生成


def test_prepare_skips_when_only_time_available():
    """只剩时间的时候不推 —— 一份没有信息量的早报只会让他瞎编。"""
    reg = _registry(Fake("time", {"date": "2026-08-04", "weekday": "周二",
                                  "clock": "10:00", "say": "上午"}))
    p = prepare_morning(reg, include_memory=False)

    assert p.skipped
    assert p.prompt == ""


def test_prepare_skips_when_everything_failed():
    p = prepare_morning(_registry(Fake("health", {}, fail=True)), include_memory=False)
    assert p.skipped


# ------------------------------------------------------------ 文本整理

def test_joins_lines_with_punctuation_not_space():
    """换行是句子边界。压成空格会在中文句子中间留个突兀的空隙 ——
    线上第一版就出现过「没睡好吧 今天有小雨」。"""
    t = finalize_push_text("深睡也才四十分钟，没睡好吧\n今天有小雨，记得带伞")
    assert "没睡好吧。今天有小雨" in t
    assert "吧 今天" not in t


def test_uses_question_mark_for_questions():
    """「是没睡好吗。」读起来是平的，而他问这句的时候是在关心她。"""
    t = finalize_push_text("昨晚看你醒了一个多小时，是没睡好吗\n今天的事不急")
    assert "是没睡好吗？今天的事不急" in t
    assert "吗。" not in t


def test_does_not_double_punctuation():
    t = finalize_push_text("昨晚没睡好吧。\n今天有小雨")
    assert "。。" not in t
    assert "没睡好吧。今天有小雨" in t


def test_strips_segment_markers():
    """||| 是聊天气泡的分段标记，推送里没有气泡。"""
    t = finalize_push_text("早|||昨晚没睡够吧")
    assert "|||" not in t
    assert "早。昨晚没睡够吧" == t


def test_keeps_space_between_latin_words():
    assert finalize_push_text("FSR402 到货了吗") == "FSR402 到货了吗"


def test_truncates_for_lockscreen():
    t = finalize_push_text("好" * 500)
    assert len(t) <= MAX_PUSH_CHARS
    assert t.endswith("…")


# ------------------------------------------------------------ 接口层

class FakeChatNox:
    """记下 chat() 被怎么调的。不打模型、不打网络。"""

    def __init__(self, registry, *, reply="早，昨晚没睡够吧。", ok=True):
        self.context = registry
        self.bridge = None
        self.system_prompt = "（静态前缀）"
        self.reply = reply
        self.ok = ok
        self.chat_calls: list[str] = []

        class _Loop:
            tools = {"daily_summary": None}

            #: 同 test_api.py —— `create_app` 会注册她电脑那五件工具
            def register(self, spec, handler):
                pass

        class _Cfg:
            class primary:  # noqa: N801
                model = "fake-model"
            db_path = ":memory:"
            history_limit = 40
            recent_window_tokens = 8000
            context_budget_tokens = 20000

            class router:
                light_adapter = None

        self.loop = _Loop()
        self.cfg = _Cfg()

    def model_name(self, model=None):
        return model or "fake-model"

    def chat(self, text, history=None, **kw):
        self.chat_calls.append(text)
        result = type("LR", (), {
            "ok": self.ok, "text": self.reply,
            "outcome": "answered" if self.ok else "error", "detail": "",
        })()
        return type("RR", (), {
            "result": result,
            "messages": [*(history or []),
                         Message(role="user", text=text),
                         Message(role="assistant", text=self.reply)],
        })()


def _client(nox):
    from fastapi.testclient import TestClient
    from api.server import create_app
    return TestClient(create_app(nox))


def test_endpoint_goes_through_chat_so_it_lands_in_history():
    """最要紧的一条：这句话必须进他的会话，否则她回「嗯有点」时他一脸懵。"""
    nox = FakeChatNox(_two_provider_registry())
    r = _client(nox).post("/daily-summary",
                          json={"session_id": "s1", "include_memory": False})

    assert r.status_code == 200
    assert len(nox.chat_calls) == 1              # 走的是 chat 不是 loop.run
    assert "5 小时 40 分" in nox.chat_calls[0]   # 简报确实递过去了
    assert r.json()["text"] == "早，昨晚没睡够吧。"
    assert r.json()["session_id"] == "s1"


def test_endpoint_skips_without_calling_model():
    """数据不够时连模型都不调 —— 省下这笔钱。"""
    reg = _registry(Fake("time", {"date": "2026-08-04", "weekday": "周二",
                                  "clock": "10:00", "say": "上午"}))
    nox = FakeChatNox(reg)
    r = _client(nox).post("/daily-summary", json={"include_memory": False})

    assert r.json()["skipped"] is True
    assert r.json()["pushed"] is False
    assert nox.chat_calls == []


def test_endpoint_does_not_push_by_default():
    """推送归 bridge 做（它管订阅表和 conversations），Core 默认不碰。"""
    nox = FakeChatNox(_two_provider_registry())
    r = _client(nox).post("/daily-summary", json={"include_memory": False})

    assert r.json()["pushed"] is False


def test_endpoint_reports_empty_generation():
    nox = FakeChatNox(_two_provider_registry(), reply="")
    r = _client(nox).post("/daily-summary", json={"include_memory": False})

    assert r.json()["skipped"] is True
    assert "没给出文本" in r.json()["reason"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


