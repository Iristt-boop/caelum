"""Context 分级测试：这轮该加载哪些 Provider。

核心判据：
  多加载一个 = 几百毫秒 + 几十 token；少加载一个 = 他答不上来。
  所以宁可偶尔多拉，但**最小集必须真的小** ——
  闲聊每轮多打一次 ha-mcp 和 health-mcp 是纯浪费。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router.intent import classify, classify_context  # noqa: E402


def test_default_is_the_minimal_set():
    """普通聊天只有 time + mood，两个都是本地计算，零外部调用。"""
    assert classify_context("今天做了个梦挺奇怪的") == ["time", "mood"]


def test_light_path_forces_minimal():
    """轻量路径存在的意义就是快 —— 为一句「早上好」去打两个 MCP 是自相矛盾。

    注意「早上好」本身命中了健康触发词（问候常带着问身体），
    但它走轻量路径，必须被强制压回最小集。
    """
    assert classify_context("早上好", light=True) == ["time", "mood"]
    assert classify_context("开空调", light=True) == ["time", "mood"]


@pytest.mark.parametrize("text", [
    "把灯关了", "空调开到26度", "风扇还开着吗", "电热毯打开",
    "客厅几度", "我回来了", "要睡了", "蒸蛋器帮我开一下",
])
def test_home_words_pull_home(text):
    assert "home" in classify_context(text), text


@pytest.mark.parametrize("text", [
    "我昨晚睡得怎么样", "睡了几个小时", "今天走了多少步",
    "我心率正常吗", "怎么这么累", "今天怎么样", "最近睡眠质量如何",
])
def test_health_words_pull_health(text):
    assert "health" in classify_context(text), text


@pytest.mark.parametrize("text", [
    "今天天气怎么样", "外面下雨了吗", "今天穿什么", "要带伞吗",
    "外面冷不冷", "我要出门了",
])
def test_weather_words_pull_weather(text):
    assert "weather" in classify_context(text), text


@pytest.mark.parametrize("text", [
    "今天有什么安排", "我今天要做什么", "待办还有哪些", "最近忙什么",
    "还有什么没做完",
])
def test_todo_words_pull_todo(text):
    assert "todo" in classify_context(text), text


def test_today_how_pulls_both_health_and_todo():
    """「今天怎么样」既在问身体也在问安排 —— 两个都拉才答得全。
    这正是 Daily Planner 的典型入口。"""
    names = classify_context("今天怎么样")
    assert "health" in names and "todo" in names


def test_unrelated_talk_pulls_nothing_extra():
    """闲聊不该把三个外部数据源都惊动一遍。"""
    for text in ["《底特律》那个结局你怎么看", "帮我改一下这段代码", "想你了"]:
        names = classify_context(text)
        assert names == ["time", "mood"], text


def test_can_pull_several():
    names = classify_context("我要睡了，昨晚没睡好，把灯关了")
    assert "home" in names and "health" in names

    names = classify_context("外面下雨了吗，要不要把窗帘关上")
    assert "weather" in names and "home" in names


def test_memory_is_never_auto_loaded():
    """**这条守着决策 7**：记忆是工具不是每轮强塞。

    一次检索约 7 秒，且每轮塞不同记忆会让 dynamic_system 每轮都变、
    缓存命中率从 98.9% 掉到 62.5%。日常对话走 recall_memory 工具。
    """
    for text in ["你还记得我们第一天吗", "上次说的那个", "以前你说过",
                 "开灯", "昨晚睡得怎么样", "随便聊聊"]:
        assert "memory" not in classify_context(text), text


def test_always_includes_the_two_locals():
    """time 和 mood 是每轮的底 —— 少了他连几点、她什么情绪都不知道。"""
    for text in ["开灯", "睡得好吗", "在吗", ""]:
        names = classify_context(text)
        assert names[:2] == ["time", "mood"], text


def test_order_is_stable():
    """输出顺序稳定 —— 顺序变了 dynamic_system 就变，白掉一次缓存。"""
    a = classify_context("把灯关了，我昨晚没睡好")
    b = classify_context("把灯关了，我昨晚没睡好")
    assert a == b == ["time", "mood", "home", "health"]

    # 全命中时的顺序
    assert classify_context("外面冷吗，我没睡好，把电热毯开上，今天还有什么安排") == \
        ["time", "mood", "home", "health", "weather", "todo"]


def test_greeting_is_light_so_stays_minimal():
    """端到端串一遍：问候 → 轻量路径 → 最小集。"""
    d = classify("早安")
    assert d.light
    assert classify_context("早安", light=d.light) == ["time", "mood"]


def test_real_home_request_is_full_path_and_pulls_home():
    """「开灯」很短但含动作词，走完整路径，且要拉家居状态。"""
    d = classify("开灯")
    assert not d.light
    assert "home" in classify_context("开灯", light=d.light)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
