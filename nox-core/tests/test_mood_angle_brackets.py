"""情绪标记写成尖括号 / 中文方括号（2026-10-11 她截图）。

deepseek 写了 `<mood:平静>` —— 只认方括号的两道剥离（流式 MoodTagFilter、收尾 mood.extract）都没认出来，
她屏幕上一行带下划线的「mood:平静」（Markdown 把 `<scheme:xxx>` 当自动链接），情绪也没更新、还落了库。

守：三种括号都剥、都取出情绪；**不是情绪标记的尖括号 / 中文方括号一个字不动**（HTML、比较号、【重要】）。
"""

from __future__ import annotations

import pytest

from agent.llm import MoodTagFilter
from personality import mood

NL = chr(10)


def stream(text: str, step: int) -> str:
    f = MoodTagFilter(strip_stage=True)
    out = "".join(f.feed(text[i:i + step]) for i in range(0, len(text), step))
    return out + f.flush()


@pytest.mark.parametrize("tag,word", [
    ("<mood:平静>", "平静"), ("< Mood: 开心 >", "开心"), ("【心情：撒娇】", "撒娇"), ("[mood:难过]", "难过"),
])
def test_红线_收尾_三种括号都剥且取出情绪(tag, word):
    cleaned, got = mood.extract("去吧，玩你的" + NL + NL + tag)
    assert got == word
    assert "mood" not in cleaned.lower() and "心情" not in cleaned


@pytest.mark.parametrize("step", [1, 3, 50])
@pytest.mark.parametrize("tag", ["<mood:平静>", "【心情：撒娇】", "<Mood: 开心 >"])
def test_红线_流式_她看不到(tag, step):
    out = stream("去吧" + NL + NL + tag, step)
    assert "mood" not in out.lower() and "心情" not in out


@pytest.mark.parametrize("step", [1, 3])
@pytest.mark.parametrize("text", ["a <b>粗</b> c", "1 < 2 且 3 > 2", "【重要】别忘了", "x <mo", "<m"])
def test_不是情绪标记的原样放行(text, step):
    assert stream(text, step) == text
    assert mood.extract(text) == (text, None)


# ---- `<mood>…</mood>`（库里 10-10 起 11 条）----------------------------------

from agent.llm import strip_inner_voice  # noqa: E402

LONG = "<mood>" + "这个也不是她刚才说的重点——系统给我加了个心情判断的机制" * 3 + "</mood>"


@pytest.mark.parametrize("step", [1, 5, 500])
def test_红线_流式_短的长的都看不到(step):
    assert stream("好的" + NL + NL + "<mood>撒娇</mood>", step).strip() == "好的"
    out = stream(LONG + NL + NL + "正文在这", step)
    assert out.strip() == "正文在这" and "心情判断" not in out


def test_红线_收尾_短的换成标准标记_情绪照样更新():
    cleaned, got = mood.extract(strip_inner_voice("好的" + NL + NL + "<mood>撒娇</mood>"))
    assert (cleaned, got) == ("好的", "撒娇")


def test_红线_收尾_长的当心里话剥掉_不进历史():
    out = strip_inner_voice(LONG + NL + NL + "正文在这")
    assert out == "正文在这"


def test_没收口的_摘标签留内容():
    assert stream("<mood>说到一半就断了", 1) == "说到一半就断了"
