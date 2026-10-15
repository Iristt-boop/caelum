"""set_mood 原生工具 + XML 假调用回归（2026-10-15 Aru 化改造）。

她截图实锤的两条漏网：
  1. `<send_meme tag="笨爪开心">` —— 表情调用被写成 XML 正文，原过滤器只认
     `[!名字]` 行形态，整条出街
  2. `<[mood:平静]>` —— mood 标签的嵌套括号变体，_OPEN/_CLOSE 单字符字符集
     必然失配

这个文件守：
- set_mood 工具本体（enum 收死、update + 落盘、开关 off 时 mood_tool_on() 为 False）
- XML 假调用的提取（extract_fake_calls）与 rescue（_rescue_fake_calls 的 set_mood 分支）
- MoodTagFilter 流式侧：XML 假调用 / 嵌套 mood 都不漏；普通尖括号、HTML 不误伤
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import MoodTagFilter, extract_fake_calls  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402
from nox import _rescue_fake_calls  # noqa: E402
from personality import mood as mood_mod  # noqa: E402
from personality.mood import Mood, extract, mood_tool_on  # noqa: E402
from tools import mood_tool  # noqa: E402


# ---------------------------------------------------------------- set_mood 工具本体

def _tool_handler():
    loop = AgentLoop(adapter=None)
    saved: list[bool] = []
    m = Mood()
    mood_tool.register_all(loop, mood_ref=lambda: m, save=lambda: saved.append(True))
    return loop.tools["set_mood"].handler, m, saved


def test_set_mood_名单内的词_记下并落盘():
    h, m, saved = _tool_handler()
    out = h({"mood": "撒娇"})
    assert m.her_emotion == "撒娇"
    assert saved == [True]
    assert "撒娇" in out


def test_set_mood_名单外的词_退回重选_不落盘():
    h, m, saved = _tool_handler()
    out = h({"mood": "心疼"})          # 「心疼」不在她七情绪里，历史漏过同款
    assert m.her_emotion != "心疼"
    assert saved == []
    assert "重新调一次" in out and "心疼" in out


def test_set_mood_注册进loop_副作用是none():
    loop = AgentLoop(adapter=None)
    mood_tool.register_all(loop, mood_ref=lambda: Mood(), save=lambda: None)
    assert loop.tools["set_mood"].spec.side_effect == "none"
    assert loop.tools["set_mood"].spec.parameters["properties"]["mood"]["enum"] == [
        "开心", "难过", "烦躁", "撒娇", "兴奋", "疲惫", "平静",
    ]


def test_开关_默认开_off时关掉(monkeypatch):
    monkeypatch.delenv("NOX_MOOD_TOOL", raising=False)
    assert mood_tool_on() is True
    monkeypatch.setenv("NOX_MOOD_TOOL", "off")
    assert mood_tool_on() is False
    monkeypatch.setenv("NOX_MOOD_TOOL", "0")
    assert mood_tool_on() is False


# ---------------------------------------------------------------- XML 假调用提取

def test_她截图的send_meme标签_提取成假调用():
    cleaned, calls = extract_fake_calls("你喜欢，以后你写东西的时候我就拿它垫着。<send_meme tag=\"笨爪开心\">")
    assert calls == [("send_meme", "笨爪开心")]
    assert "send_meme" not in (cleaned or "")


def test_成对形态_内文当参数():
    cleaned, calls = extract_fake_calls("在呢<send_meme>举爪开心</send_meme>")
    assert calls == [("send_meme", "举爪开心")]
    assert "send_meme" not in (cleaned or "")


def test_set_mood_xml_属性和内文都认():
    _, calls = extract_fake_calls("<set_mood mood=\"开心\">")
    assert calls == [("set_mood", "开心")]
    _, calls = extract_fake_calls("<set_mood>平静</set_mood>")
    assert calls == [("set_mood", "平静")]


def test_没收口的开标签剥掉_后面的正文留着():
    cleaned, calls = extract_fake_calls("<send_meme tag=\"开心\">她说她累了，那我少说两句")
    assert calls == [("send_meme", "开心")]
    assert "她说她累了" in (cleaned or "")


def test_自闭合形态():
    _, calls = extract_fake_calls("<set_mood mood=\"平静\"/>")
    assert calls == [("set_mood", "平静")]


def test_普通尖括号不误伤():
    raw = "看 <div class=\"x\"> 这个，a<b 但 c>d"
    cleaned, calls = extract_fake_calls(raw)
    assert calls == []
    assert cleaned == raw


# ---------------------------------------------------------------- rescue：set_mood 转真调用

def test_rescue_set_mood_词在名单_转成update():
    m = Mood()
    saved: list[bool] = []
    _rescue_fake_calls([], [("set_mood", "开心")], mood=m, save=lambda: saved.append(True))
    assert m.her_emotion == "开心" and saved == [True]


def test_rescue_set_mood_词不在名单_只剥不记():
    m = Mood()
    before = m.her_emotion
    _rescue_fake_calls([], [("set_mood", "心疼")], mood=m, save=lambda: None)
    assert m.her_emotion == before


def test_rescue_表情和set_mood同轮都能转():
    m = Mood()
    attachments: list[dict] = []
    _rescue_fake_calls(attachments, [
        ("set_mood", "撒娇"),
        ("send_meme", "举爪开心"),
        ("remind_myself", "90 | 她累了一天该睡了"),   # 假的其他调用不替他执行
    ], mood=m, save=lambda: None)
    assert m.her_emotion == "撒娇"
    assert attachments == [{"type": "meme", "tag": "举爪开心"}]


# ---------------------------------------------------------------- MoodTagFilter 流式

def _stream(text: str, *, strip_stage: bool = True, step: int = 1) -> str:
    f = MoodTagFilter(strip_stage=strip_stage)
    out = "".join(f.feed(text[i:i + step]) for i in range(0, len(text), step))
    return out + f.flush()


@pytest.mark.parametrize("step", [1, 3, 7])
def test_她截图的send_meme标签_流式不漏(step):
    raw = "你喜欢，以后你写东西的时候我就拿它垫着。<send_meme tag=\"笨爪开心\">"
    assert "send_meme" not in _stream(raw, step=step)
    assert "笨爪开心" not in _stream(raw, step=step)


@pytest.mark.parametrize("step", [1, 3, 7])
def test_嵌套mood标签_流式不漏(step):
    raw = "嗯。<" + "[mood:平静]>"
    assert _stream(raw, step=step) == "嗯。"


@pytest.mark.parametrize("step", [1, 3, 7])
def test_经典mood标签_流式照旧不漏(step):
    assert _stream("在呢[mood:开心]", step=step) == "在呢"
    assert _stream("<mood:撒娇>好嘛", step=step) == "好嘛"


@pytest.mark.parametrize("raw", [
    "看 <div class=\"x\"> 这个",
    "a < b 但 c>d",
    "</send_meme>",            # 落单收口不算开标签（历史遗留），原样放行——收尾兜
])
def test_普通尖括号流式不误伤(raw):
    assert _stream(raw) == raw


# ---------------------------------------------------------------- mood.extract 嵌套归一化

def test_嵌套mood标签_收尾extract接得住():
    cleaned, emo = extract("嗯。<" + "[mood:平静]>")
    assert emo == "平静"
    assert "mood" not in (cleaned or "")
    assert "<" not in (cleaned or "")


def test_正文里的尖括号数组不被归一化误伤():
    raw = "数组 a[0] 和 map[key]>value 的写法"
    cleaned, emo = extract(raw)
    assert emo is None
    assert cleaned == raw
