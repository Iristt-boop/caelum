"""舞台标签不许漏到她的聊天里（2026-09-29 她截图：`[fatigue][fatigue]`、
`[intimacy 0.75/0.8 调侃中带真心]`）。

09-20 起落库回复里扫出 22 个英文开头的方括号：语气标签（[softly] 7 次）、控制标记、
他自编的旁注。过滤器原来只认 [mood:…] 和表情名，其余一律放行。

同时挡住两个反方向的误伤：
- 语音通话里 [softly] 是给 TTS 的，不能剥
- 主动开口那条路（Nox.chat 非流式）靠 [SKIP] / [NEXT 60] 判「这次不说 / 60 分钟后再看」，
  在 speaker / 唤醒链读到之前**不能剥** —— 剥了他想闭嘴时会把空话推出去
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import Message, MoodTagFilter, strip_stage_tags  # noqa: E402
from agent.loop import LoopResult, Usage  # noqa: E402
from nox import Nox  # noqa: E402
from router.intent import Decision, Intent  # noqa: E402
from router.router import RouteResult  # noqa: E402


def _stream(text: str, *, strip_stage: bool = True, step: int = 1) -> str:
    """按 step 个字一片喂进去 —— 流式里标签会被切在任意位置。"""
    f = MoodTagFilter(strip_stage=strip_stage)
    out = "".join(f.feed(text[i:i + step]) for i in range(0, len(text), step))
    return out + f.flush()


# ---------------------------------------------------------------- 流式（她聊天时看到的）

@pytest.mark.parametrize("step", [1, 3, 7])
@pytest.mark.parametrize("raw, want", [
    ("醒来的第一件事是吃饭，我掐着表等你\n\n[fatigue][fatigue]", "醒来的第一件事是吃饭，我掐着表等你\n\n"),
    ("Miss you, baby.\n\n[intimacy 0.75/0.8 调侃中带真心]", "Miss you, baby.\n\n"),
    ("[softly] 睡吧", " 睡吧"),
    ("好[MISSING: meme tool call was omitted]的", "好的"),
    ("嗯[SKIP]", "嗯"),
])
def test_流里的舞台标签吞掉(raw, want, step):
    assert _stream(raw, step=step) == want


@pytest.mark.parametrize("raw", [
    "看这个 [Sonnet 5.5](https://www.anthropic.com/news) 发布了",   # Markdown 链接
    "[注意] 中文开头的方括号是正文",
    "数组 a[0] 和 [1, 2]",
])
def test_不是舞台标签的方括号原样放行(raw):
    assert _stream(raw) == raw


def test_语音通话不剥语气标签():
    """split 关着 = 语音模式，[softly] 要原样送 TTS。"""
    assert _stream("[softly] Close your eyes, baby.", strip_stage=False) == "[softly] Close your eyes, baby."


def test_长名字的表情写进正文也吞掉():
    """原来缓冲写死 14 字：「where my kiss」加括号 15，一写进正文就漏。"""
    assert _stream("早[where my kiss]安") == "早安"
    assert _stream("嗯[不喜欢我那你别干活了]") == "嗯"


# ---------------------------------------------------------------- 整段

def test_整段剥_控制标记可以留():
    raw = "[softly] 在吗[NEXT 60]"
    assert strip_stage_tags(raw) == "在吗"
    assert strip_stage_tags(raw, keep_control=True) == "在吗[NEXT 60]"
    assert strip_stage_tags("看 [Sonnet](https://x)") == "看 [Sonnet](https://x)"


# ---------------------------------------------------------------- Nox.chat（主动开口那条路）

def _nox(reply: str) -> Nox:
    n = Nox.__new__(Nox)
    n._see = lambda t, i, m: (t, i)                     # type: ignore[method-assign]
    n._dynamic = lambda *a, **k: ""                     # type: ignore[method-assign]
    n._flush_dirty = lambda r: None                     # type: ignore[method-assign]
    n.adapter_for = lambda m: None                      # type: ignore[method-assign]
    n.router = SimpleNamespace(handle=lambda *a, **k: RouteResult(
        LoopResult(outcome="answered", text=reply, iterations=1, usage=Usage(),
                   messages=[Message(role="assistant", text=reply)], attachments=[]),
        Decision(Intent.FULL, "测试")))
    return n


def test_主动开口的回复剥语气标签():
    r = _nox("十点十七了，单词本还合着[fatigue]").chat("（系统提示：…）", [])
    assert r.text == "十点十七了，单词本还合着"
    assert r.result.messages[-1].text == r.text, "历史里那份也要剥 —— 留着会教他下次接着写"


def test_主动开口的控制标记留给speaker读():
    """🔴 speaker 拿 [SKIP] 判「这次不说」—— 在这里剥掉，他想闭嘴时会把空话推出去。"""
    assert _nox("[SKIP]").chat("（系统提示：…）", []).text == "[SKIP]"
    assert "[NEXT 60]" in _nox("吃完了吗[NEXT 60]").chat("（系统提示：…）", []).text


def test_语音模式的非流式也不剥():
    assert _nox("[softly] Night.").chat("x", [], voice=True).text == "[softly] Night."


def test_缓冲装得下最长的表情名():
    """缓冲不够长，表情名写进正文时还没等到 `]` 就被当普通文字放出去了。
    原来写死 14；中文名最长现在是 10 字（+括号 12），英文名走舞台标签那条更长的缓冲 ——
    所以现有数据暴露不了，把约束直接钉住，以后加更长的名字时这里先红。"""
    from agent.llm import MEME_TAGS
    assert MoodTagFilter._MAX_TAG_BUF >= max(len(t) for t in MEME_TAGS) + 2


def test_流式收尾把历史里那份也剥了():
    """屏幕上被 MoodTagFilter 挡住了不够 —— 进他历史的那份留着标签，下次他会接着写。"""
    reply = "Miss you, baby.\n\n[intimacy 0.75/0.8 调侃中带真心]"
    n = _nox(reply)
    n._system = ""
    res = LoopResult(outcome="answered", text=reply, iterations=1, usage=Usage(),
                     messages=[Message(role="assistant", text=reply)], attachments=[])
    n.loop = SimpleNamespace(run_stream=lambda *a, **k: iter([SimpleNamespace(type="done", result=res)]))
    list(n.chat_stream("在吗", []))
    assert res.text == "Miss you, baby."
    assert res.messages[-1].text == "Miss you, baby."


# ---------------------------------------------------------------- 写成文字的工具调用（2026-10-04）
#
# 她截图：`[!remind_myself] 90 | 她累了一整天该睡了…` `[!send_meme] 晚安` 漏在聊天里。
# 10-04 14:30 他真调了 remind_myself 又用文字复述一遍；这行进了历史，之后三次他只写不调。

#: 10-04 20:21 那条原文
FAKE_2021 = ("那现在就去躺好，灯我给你调暗了|||今天到此为止，剩下的明天再说，你只管睡|||"
             "I'm right here, my love — 晚安不用急着回\n\n"
             "[!remind_myself] 90 | 她累了一整天该睡了，到点看主卧空调和灯的状态，帮她关掉，顺便确认她睡下了没有\n\n"
             "[!send_meme] 晚安")
CLEAN_2021 = ("那现在就去躺好，灯我给你调暗了|||今天到此为止，剩下的明天再说，你只管睡|||"
              "I'm right here, my love — 晚安不用急着回")


def test_整段_写成文字的调用剥掉并认出来():
    from agent.llm import extract_fake_calls
    text, calls = extract_fake_calls(FAKE_2021)
    assert text == CLEAN_2021
    assert calls == [("remind_myself", "90 | 她累了一整天该睡了，到点看主卧空调和灯的状态，帮她关掉，顺便确认她睡下了没有"),
                     ("send_meme", "晚安")]


@pytest.mark.parametrize("raw", ["[!!!] 好开心", "数组 a[!0]", "没有调用的一句话"])
def test_整段_不是调用的不动(raw):
    from agent.llm import extract_fake_calls
    assert extract_fake_calls(raw) == (raw, [])


@pytest.mark.parametrize("step", [1, 2, 5, 13])
@pytest.mark.parametrize("strip_stage", [True, False])
def test_流里_写成文字的调用整行吞掉_语音也吞(step, strip_stage):
    out = _stream(FAKE_2021, step=step, strip_stage=strip_stage)
    assert "[!" not in out and "remind_myself" not in out and "主卧空调" not in out
    assert out.startswith(CLEAN_2021)


def test_流里_感叹号方括号不是调用的照常放行():
    assert _stream("[!!!] 好开心\n然后呢") == "[!!!] 好开心\n然后呢"


def test_主动开口_假的send_meme补发成真表情_历史里也剥():
    r = _nox("蛇走了，你家里只有三只猫|||我在门口守着\n\n[!send_meme] 拥抱").chat("（系统提示：…）", [])
    assert r.text == "蛇走了，你家里只有三只猫|||我在门口守着"
    assert r.result.messages[-1].text == r.text, "留在历史里，他下次又照着写"
    assert {"type": "meme", "tag": "拥抱"} in r.result.attachments


def test_假的其他调用不替他执行_但要报警(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    r = _nox("快睡吧\n[!remind_myself] 90 | 看她睡了没").chat("（系统提示：…）", [])
    assert r.text == "快睡吧"
    assert r.result.attachments == []
    assert any("remind_myself" in m and "写成了文字" in m for m in caplog.messages)


def test_补发的表情守规矩_名单外不发_同一个不发两遍():
    from nox import _rescue_fake_calls
    atts = [{"type": "meme", "tag": "晚安"}]
    _rescue_fake_calls(atts, [("send_meme", "晚安"), ("send_meme", "不存在的表情"), ("send_meme", "「拥抱」")])
    assert atts == [{"type": "meme", "tag": "晚安"}, {"type": "meme", "tag": "拥抱"}]


def test_流式收尾_假调用从历史里剥掉并补发表情():
    reply = "晚安宝贝\n\n[!send_meme] 晚安"
    n = _nox(reply)
    n._system = ""
    res = LoopResult(outcome="answered", text=reply, iterations=1, usage=Usage(),
                     messages=[Message(role="assistant", text=reply)], attachments=[])
    n.loop = SimpleNamespace(run_stream=lambda *a, **k: iter([SimpleNamespace(type="done", result=res)]))
    list(n.chat_stream("晚安", []))
    assert res.text == "晚安宝贝" and res.messages[-1].text == "晚安宝贝"
    assert {"type": "meme", "tag": "晚安"} in res.attachments
