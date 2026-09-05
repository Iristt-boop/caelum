"""Context 分级测试：这轮该加载哪些 Provider。

核心判据：
  多加载一个 = 几百毫秒 + 几十 token；少加载一个 = 他答不上来。
  所以宁可偶尔多拉，但**最小集必须真的小** ——
  闲聊每轮多打一次 ha-mcp 和 health-mcp 是纯浪费。

⚠️ 判据是「**要不要打外部调用**」，不是「名单有几个」。
2026-09-04 加 `resonance` 进最小集时对过这条：它和 time / mood 一样是
纯内存计算、零外部调用，所以进得来。名单会长，那条线不动。

所以下面几条不再钉死列表，改成断言 `MINIMAL` 这个常量 ——
以后再加本地 Provider 只改一处，而"最小集里不许有打网络的"另有测试守。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router.intent import classify, classify_context  # noqa: E402

#: 最小集：全是本地计算，零外部调用。
#:   time           算时间
#:   mood           她的情绪（本轮文本 + 内存状态）
#:   resonance      他自己的情绪（2026-09-04 加，纯内存读 Registry）
#:   understanding  他理解着她的哪几件事（2026-09-05 加，同样纯内存读 Registry）
MINIMAL = ["time", "mood", "resonance", "understanding"]

#: 这几个要打外部（MCP / HTTP），**永远不许进最小集**
EXTERNAL = {"home", "health", "weather", "todo", "location", "music", "memory"}



def test_default_is_the_minimal_set():
    """普通聊天只有 time + mood，两个都是本地计算，零外部调用。"""
    assert classify_context("今天做了个梦挺奇怪的") == MINIMAL


def test_light_path_forces_minimal():
    """轻量路径存在的意义就是快 —— 为一句「早上好」去打两个 MCP 是自相矛盾。

    注意「早上好」本身命中了健康触发词（问候常带着问身体），
    但它走轻量路径，必须被强制压回最小集。
    """
    assert classify_context("早上好", light=True) == MINIMAL
    assert classify_context("开空调", light=True) == MINIMAL


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
        assert names == MINIMAL, text


def test_can_pull_several():
    names = classify_context("我要睡了，昨晚没睡好，把灯关了")
    assert "home" in names and "health" in names

    names = classify_context("外面下雨了吗，要不要把窗帘关上")
    assert "weather" in names and "home" in names


#: 🔴 memory 2026-09-05 解禁。**这一组替换了原来那条
#: `test_memory_is_never_auto_loaded`**（它守的是「永远不自动加载」）。
#:
#: 改这条测试是有意的：封它的两条理由都失效了 ——
#: 7 秒 → 650ms（VPS 实测），砸缓存那条被「动态块挪到尾部」修掉了。
#: 但决策 7 那句「最好轻量、响应快」没过期，所以边界从「永不加载」
#: 变成「两条件命中其一才加载」，下面几条守的是新边界。


@pytest.mark.parametrize("text", [
    "你还记得我们第一天吗", "上次说的那个", "以前你说过",
    "那时候你还不会做饭", "我们第一次见面", "去年这时候",
])
def test_past_pointing_talk_pulls_memory(text):
    """她这句话本身就在指向过去 —— 那就该去翻。"""
    assert "memory" in classify_context(text), text


@pytest.mark.parametrize("text", [
    "开灯", "今天几号", "放首歌", "外面下雨了吗", "随便聊聊", "在吗",
])
def test_ordinary_talk_still_never_touches_ob(text):
    """🔴 memory 是唯一会打外部服务的按需 Provider（约 650ms）。

    「今天几号」为此多等半秒是纯浪费 —— 决策 7 守的就是这个。
    """
    assert "memory" not in classify_context(text), text


def test_understanding_alone_pulls_memory():
    """🔴 **主路**：他心里正搁着一件事，就该带着记忆去接她的话。

    这是理解层驱动的那条 —— 同样一句「好累」，他心里有事的时候
    和没事的时候，值不值得翻记忆是不一样的。
    """
    assert "memory" not in classify_context("好累")
    assert "memory" in classify_context("好累", has_understanding=True)


def test_emotion_words_alone_do_not_pull_memory():
    """⚠️ 情绪词**不该**进 `_NEED_MEMORY`。

    「累」要不要联系过去，取决于他是不是正为她那件事惦记着，
    不取决于这两个字 —— 那是理解层的判断，不是正则的。
    """
    for text in ["好累", "好烦", "心情不好", "难受"]:
        assert "memory" not in classify_context(text), text


def test_light_path_never_pulls_memory_even_with_understanding():
    """轻量路径存在的意义就是快。他心里有事也不能在这条路上花 650ms。"""
    assert "memory" not in classify_context("早上好", light=True,
                                            has_understanding=True)


def test_always_includes_the_two_locals():
    """time 和 mood 是每轮的底 —— 少了他连几点、她什么情绪都不知道。"""
    for text in ["开灯", "睡得好吗", "在吗", ""]:
        names = classify_context(text)
        assert names[:2] == ["time", "mood"], text


def test_order_is_stable():
    """输出顺序稳定 —— 顺序变了 dynamic_system 就变，白掉一次缓存。"""
    a = classify_context("把灯关了，我昨晚没睡好")
    b = classify_context("把灯关了，我昨晚没睡好")
    assert a == b == MINIMAL + ["home", "health"]

    # 全命中时的顺序
    assert classify_context("外面冷吗，我没睡好，把电热毯开上，今天还有什么安排") == \
        MINIMAL + ["home", "health", "weather", "todo"]


def test_greeting_is_light_so_stays_minimal():
    """端到端串一遍：问候 → 轻量路径 → 最小集。"""
    d = classify("早安")
    assert d.light
    assert classify_context("早安", light=d.light) == MINIMAL


def test_real_home_request_is_full_path_and_pulls_home():
    """「开灯」很短但含动作词，走完整路径，且要拉家居状态。"""
    d = classify("开灯")
    assert not d.light
    assert "home" in classify_context("开灯", light=d.light)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_最小集里不许有打网络的():
    """🔴 这条才是真正的那条线。

    上面几条钉的是"名单长什么样"，会随着加本地 Provider 而变；
    **这条钉的是判据本身** —— 轻量路径的意义就是快，
    混进一个要打 MCP 的，那条路就白设了。
    """
    for text in ["早上好", "开空调", "我没睡好", "外面冷吗"]:
        assert not (set(classify_context(text, light=True)) & EXTERNAL), text
