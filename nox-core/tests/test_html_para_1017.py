"""10-17：DeepSeek 在段首写 HTML 的 `<p>`，Chat 页是纯文本，原样露给她（当天 5 条，进了历史就跟着学）。

守：段落标签摘掉、`<br>` 换行；流式里标签被切成几片也认得；只认完整标签，`a<p`、`<y>` 这种不碰；
收尾（strip_inner_voice）和清历史（history_sanitizer 走 normalize_tags）同一条规矩。
"""

from __future__ import annotations

import pytest

from agent.llm import InnerVoiceFilter, normalize_tags, strip_inner_voice


@pytest.mark.parametrize("raw,want", [
    ("剩的扫了。\n\n<p>剩了多少", "剩的扫了。\n\n剩了多少"),
    ("<p>一句</p>", "一句"),
    ("<P >大写也认</p >", "大写也认"),
    ("上一句<br>下一句<br/>再下一句", "上一句\n下一句\n再下一句"),
    ("a<p 不是标签，x<y 也不是", "a<p 不是标签，x<y 也不是"),
    ("<pre>代码</pre>", "<pre>代码</pre>"),
])
def test_摘段落标签(raw, want):
    assert normalize_tags(raw) == want


def _stream(chunks):
    f = InnerVoiceFilter()
    out = []
    for c in chunks:
        out += f.feed(c)
    out += f.flush()
    return "".join(t for k, t in out if k == "text")


def test_红线_流式里切成几片也摘掉():
    assert _stream(["好的\n\n<", "p", ">米饭", "留一半<b", "r>晚上", "</", "p>"]) == "好的\n\n米饭留一半\n晚上"


def test_流式_不是这几个标签的原样放出():
    assert _stream(["x<", "y>z", " a<", "pre>"]) == "x<y>z a<pre>"


def test_收尾那条路也摘():
    assert strip_inner_voice("<心里>想她</心里>在呢\n\n<p>吃了吗") == "在呢\n\n吃了吗"
