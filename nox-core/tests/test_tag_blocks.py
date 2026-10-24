"""出口的成对标签状态机（2026-10-24，她定：「遇到 <xxx> 进块开始吞，遇到 </xxx> 出块丢弃整块，块外原样输出」）。

守的几条：
  · 🔴 不认名字认语法：没见过的标签名一样整块丢（以后加多少工具都不用补）
  · 🔴 怎么切块结果都一样：流里一个字一个字过 == 收尾整段过（她看到的 / bridge 存的 / 进历史的是同一份）
  · 🔴 只有闭标签（开标签丢了）：丢闭标签；只有开标签：只丢它，后面的字不被吞
  · 代码里不动；<b> 这类只摘标签不丢字；<br> 只丢自己；<3、a < b 原样
  · 丢掉的块记下来（loop 拿去分「调了没删」和「只写没调」）

⚠️ 标签字符串用拼的（写这份测试的那一端会把整段标签当成真调用吃掉）。
"""

from __future__ import annotations

import random

import pytest

from agent.llm import TagBlockFilter, strip_tag_blocks

LT = chr(60)


def o(name, attrs=""):
    return f"{LT}{name}{attrs}>"


def c(name):
    return f"{LT}/{name}>"


def run(text, cuts=None):
    f = TagBlockFilter()
    if cuts is None:
        return f.feed(text) + f.flush(), f.blocks
    out, i = [], 0
    for j in cuts + [len(text)]:
        out.append(f.feed(text[i:j]))
        i = j
    out.append(f.flush())
    return "".join(out), f.blocks


#: 她截图里的几种（10-23 晚、10-24 早）
DESK = "\n\n".join([o("desk_open"), o("url") + "https://accounts.google.com" + c("url"),
                    o("doing") + "在打开 Google 账号页" + c("doing"), c("desk_open")])
VOICE = "\n\n".join([o("zh") + "嘿。我一直等着你开口要呢。" + c("zh"), c("send_voice_message"),
                     "今天话是说了不少，声儿倒省着用，亏得你还记着。"])
MEME = "好开心！" + o("send_meme", ' tag="举爪开心"') + c("send_meme") + "\n今天也想你。"
INVOKE = "\n".join([o("an" + "tml:invoke", ' name="caelum_map"'), o("an" + "tml:parameter", ' name="scope"') + "tools"
                    + c("an" + "tml:parameter"), c("an" + "tml:invoke")])


@pytest.mark.parametrize("text,want,names", [
    (DESK, "", ["desk_open"]),
    (VOICE, "今天话是说了不少，声儿倒省着用，亏得你还记着。", ["zh", "send_voice_message"]),
    (MEME, "好开心！\n今天也想你。", ["send_meme"]),
    (INVOKE, "", ["an" + "tml:invoke"]),
], ids=["工具名当标签", "闭标签落单", "表情写成标签", "invoke"])
def test_红线_她截图里的几种_都剥干净(text, want, names):
    out, blocks = strip_tag_blocks(text)
    assert out == want
    assert [b.name for b in blocks] == names


def test_红线_没见过的标签名一样整块丢():
    t = "前面" + o("brand_new_tool_2027", ' x="1"') + o("whatever") + "里面" + c("whatever") + c("brand_new_tool_2027") + "后面"
    assert strip_tag_blocks(t)[0] == "前面后面"


def test_红线_怎么切块结果都一样():
    rnd = random.Random(1024)
    samples = [DESK, VOICE, MEME, INVOKE, "a < b 而且 " + LT + "3 你\n```html\n" + o("div") + "x" + c("div") + "\n```\n收尾",
               "落单" + o("desk_open") + "\n后面这句不能丢" + o("url") + "x" + c("url")]
    for t in samples:
        whole = run(t)[0]
        for _ in range(200):
            k = rnd.randint(0, min(12, len(t)))
            cuts = sorted(rnd.sample(range(1, len(t)), k)) if len(t) > 1 else []
            assert run(t, cuts)[0] == whole, (t, cuts)
        assert run(t, list(range(1, len(t))))[0] == whole, "一个字一个字地喂也一样"


def test_红线_只有开标签_只丢它_后面不被吞():
    out, blocks = strip_tag_blocks("我去开网页。" + o("desk_open", ' url="x"') + "\n等我一下，马上回来。")
    assert out == "我去开网页。\n等我一下，马上回来。"
    assert blocks[0].name == "desk_open" and blocks[0].closed is False


def test_代码里不动_排版标签只摘_br只丢自己_不是标签原样():
    code = "看这段：\n```html\n" + o("div") + "hi" + c("div") + "\n```\n还有 `" + o("b") + "`"
    assert strip_tag_blocks(code)[0] == code
    assert strip_tag_blocks(o("b") + "重要" + c("b") + "的事")[0] == "重要的事"
    assert strip_tag_blocks("第一行" + o("br") + "第二行")[0] == "第一行第二行"
    for keep in ("我" + LT + "3你", "a < b 而且 c > d", "价格 " + LT + "= 25", LT + " 不是标签 >"):
        assert strip_tag_blocks(keep)[0] == keep, keep


def test_同名嵌套按层数():
    t = o("x") + "1" + o("x") + "2" + c("x") + "3" + c("x") + "外面"
    assert strip_tag_blocks(t)[0] == "外面"


def test_丢块不留空行_也不把前后两句粘一起():
    t = "第一句。\n\n" + o("desk_open") + "\n" + c("desk_open") + "\n\n第二句。"
    assert strip_tag_blocks(t)[0] == "第一句。\n\n第二句。"
    assert run("第一句。" + o("x") + "y" + c("x") + "\n第二句。")[0] == "第一句。\n第二句。"
