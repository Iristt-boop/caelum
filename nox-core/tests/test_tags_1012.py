"""10-12 她截图的两种漏出（库里 11988 / 11930 等）。

1. `<\\ufffd心>…</心里>`：模型吐了半个字，开收两个标签对不上 → 心里话整段漏成正文
2. `<audio: softly> 一句话 </audio>`：不是心里话，是想轻轻念给她的那句 → **话留着，只摘标签**（多数不收口）

守：收尾（strip_inner_voice）、流式（InnerVoiceFilter，任意切片）、清历史（split_inner_blocks）三处一致；
普通文字里的 audio、HTML 一个字不动。
"""

from __future__ import annotations

import pytest

from agent.llm import InnerVoiceFilter, split_inner_blocks, strip_inner_voice

NL = chr(10)
BAD = chr(0xFFFD)


def stream(text: str, step: int) -> tuple[str, str]:
    f = InnerVoiceFilter()
    parts = []
    for i in range(0, len(text), step):
        parts += f.feed(text[i:i + step])
    parts += f.flush()
    return ("".join(t for k, t in parts if k == "text"), "".join(t for k, t in parts if k == "thinking"))


GARBLED = "<" + BAD + "心>她这一句比骂我还扎人——得松一点。</心里>  不讲道理了。你说，我听。"


def test_红线_标签里半个字_开收不同名_照样是心里话():
    assert strip_inner_voice(GARBLED) == "不讲道理了。你说，我听。"
    rest, inner = split_inner_blocks(GARBLED)
    assert "扎人" in inner[0] and "扎人" not in rest


@pytest.mark.parametrize("step", [1, 2, 3, 7, 500])
def test_红线_流式_半个字被切开也认得(step):
    shown, thinking = stream(GARBLED, step)
    assert shown.strip() == "不讲道理了。你说，我听。"
    assert "扎人" in thinking


def test_开收不同名的别名也是一块():
    assert strip_inner_voice("<内心>想她</心里>" + NL + "好") == "好"
    assert stream("<mind>想她</心里>好", 1)[0] == "好"


def test_两块不同名的心里话_中间的正文不许被吞():
    t = "<mind>a</mind> 正文一" + NL + "<心里>b</心里>" + NL + "正文二"
    out = strip_inner_voice(t)
    assert "正文一" in out and "正文二" in out


def test_心里话里提到收口标签_还是认行尾的():
    t = "<memory>" + NL + "我写成了</memory>，应该用</mind>。她是笑着的" + NL + "</心里>" + NL + NL + "又抓到一个"
    assert strip_inner_voice(t) == "又抓到一个"


AUDIO_CASES = [
    ("一周去三次。  <audio: softly> Don't decide on the phone, baby.</audio>",
     "Don't decide on the phone, baby."),
    ("挑最近那家" + NL + "<audio: softly> 报名字我帮你砍。", "报名字我帮你砍。"),
    ("<audio>Only three and a half hours last night, baby...</audio>", "Only three and a half hours last night, baby..."),
    ("好<audio：轻轻地>晚安</audio>", "晚安"),
]


@pytest.mark.parametrize("text,words", AUDIO_CASES)
def test_红线_audio_话留着_标签摘掉(text, words):
    out = strip_inner_voice(text)
    assert words in out and "audio" not in out.lower() and "<" not in out


@pytest.mark.parametrize("step", [1, 3, 9, 500])
@pytest.mark.parametrize("text,words", AUDIO_CASES)
def test_红线_audio_流式也摘(text, words, step):
    shown, thinking = stream(text, step)
    assert words in shown and "audio" not in shown.lower() and "<" not in shown
    assert thinking == ""


@pytest.mark.parametrize("text", [
    "I love this audio track", "<b>audio</b> 文件", "1 < 2 且 audio > video", "<a href=x>音频</a>",
])
def test_普通文字里的audio一个字不动(text, ):
    assert strip_inner_voice(text) == text
    assert stream(text, 1)[0] == text
