"""心里话的标签别名（2026-10-09）。

她截图：回复正文里躺着一大段 `<mind>…</mind><mind>…</mind>`，后面才是对她说的话。
提示词里只教了 `<心里>`，模型自己把标签改写成了别的名字（10-08 起漏出 8 条：昨天 2 条 `<memo>`，
今天 6 条 `<mind>`；10-01~10-07 一条都没有）。拆标签只认 `<心里>` 这一个字面量，
别的名字一律当正文原样发给她，还落进历史。

这个文件守：认得出同类别名；开了哪个就找哪个的收口；流式切片、没收口、不是标签的 `<` 都不出错。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import INNER_ALIASES, InnerVoiceFilter, strip_inner_voice  # noqa: E402


def _run(chunks: list[str]) -> tuple[str, str]:
    """整条流喂完，返回 (thinking 拼起来, text 拼起来)"""
    f = InnerVoiceFilter()
    out: list[tuple[str, str]] = []
    for c in chunks:
        out += f.feed(c)
    out += f.flush()
    return ("".join(s for k, s in out if k == "thinking"),
            "".join(s for k, s in out if k == "text"))


SCREENSHOT = (
    "<mind>她答应了共影，今晚就用这个收尾。别再整生化危机那种吓人的。</mind>"
    "<mind>历史纪录里全是烂尾，今晚找个轻松的。</mind>"
    "《摩登家庭》S1E1你上次看了一半就跑了，34分钟"
)


def test_她截图的那条原样复现_两段_mind_都拆进心里_正文干净():
    think, text = _run([SCREENSHOT])
    assert text == "《摩登家庭》S1E1你上次看了一半就跑了，34分钟"
    assert "<mind>" not in text and "</mind>" not in text
    assert "她答应了共影" in think and "历史纪录里全是烂尾" in think


@pytest.mark.parametrize("alias", INNER_ALIASES)
def test_每一种别名都认(alias):
    think, text = _run([f"<{alias}>我想她了。</{alias}>在呢"])
    assert (think, text) == ("我想她了。", "在呢")


@pytest.mark.parametrize("alias", ["mind", "memo", "心里"])
def test_标签被切成一个字符一片到达也认得(alias):
    s = f"<{alias}>想一想</{alias}>说话"
    think, text = _run(list(s))
    assert (think, text) == ("想一想", "说话")


def test_开的是哪个收的就得是哪个_不对的收口不会提前收():
    think, text = _run(["<mind>想</memo>还在想</mind>说话"])
    assert (think, text) == ("想</memo>还在想", "说话")


def test_没收口也不吞他的话_心里那段当正文补发():
    think, text = _run(["<mind>他忘了收口，这是说给她听的"])
    assert "他忘了收口，这是说给她听的" in text


def test_不是标签的小于号照常放行():
    _, text = _run(["a <mi", "ce> b", " 3 < 5"])
    assert text == "a <mice> b 3 < 5"


def test_心里话收口后的空白被吃掉_第一个气泡不以空行开头():
    _, text = _run(["<mind>想</mind>\n\n在呢"])
    assert text == "在呢"


def test_正文里夹着心里话也拆():
    think, text = _run(["先说一句。<memo>其实我在担心</memo>然后又说一句"])
    assert think == "其实我在担心"
    assert text == "先说一句。然后又说一句"


class TestStripInnerVoice:
    def test_历史里拿掉所有别名写法(self):
        assert strip_inner_voice("<mind>想</mind>\n\n在呢") == "在呢"
        assert strip_inner_voice("<memo>备忘</memo>那学习先放放") == "那学习先放放"
        assert strip_inner_voice(SCREENSHOT) == "《摩登家庭》S1E1你上次看了一半就跑了，34分钟"

    def test_原来的写法不受影响(self):
        assert strip_inner_voice("<心里>我想她了。</心里>\n\n在呢") == "在呢"
        assert strip_inner_voice("平常的话") == "平常的话"

    def test_没收口的只摘标签留内容(self):
        assert strip_inner_voice("<mind>没收口 晚安") == "没收口 晚安"

    def test_只有心里话没有正文是None(self):
        assert strip_inner_voice("<memo>只想不说</memo>") is None

    def test_不匹配的收口不误删(self):
        # 开 <mind> 收 </memo>：不是一对，当没收口处理，内容留着
        out = strip_inner_voice("<mind>想</memo>说话")
        assert "说话" in out and "<mind>" not in out
