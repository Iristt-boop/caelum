"""工具调用被写成文字（10-23 实录）。

她说「你用一下自己的小电脑我看看」：那一轮 desk_* 没挂上（「自己的电脑」中间多了个「小」），
deepseek-flash 在正文里写了一串 invoke / parameter 标签（caelum_map、ha_list_devices），一次真调用都没有，
她屏幕上是一整屏标签，而且这条会进历史、下一轮他照着写（10-10 伪输出放大的同一条路）。

守的几条：
  · 🔴 流式：标签行一个字都不流到她那边（文字聊天 / 打电话都一样）
  · 🔴 历史：进历史的那份没有标签
  · 剥完一个字都不剩 → 重问一次，带「你把工具调用写成了文字」那句；正文里还有话 → 只剥不重问
  · 正文里聊到尖括号（不在行首）不误伤；打电话时英文行照常一个字一个字流（不为了拦标签去攒英文行）
  · 她那句话现在挂得上我电脑那包

⚠️ 标签字符串用拼的，不在源码里写成整段（写这份测试的那一端会把整段标签当成真调用吃掉）。
"""

from __future__ import annotations

from agent import contracts, toolpacks
from agent.llm import LineDraftFilter
from agent.loop import EMPTY_NOTE, LEAK_NOTE
from tests.test_empty_reply import _Ad, _end, _loop

LT, NS = chr(60), "an" + "tml:"


def _open(tag, attrs=""):
    return f"{LT}{NS}{tag}{attrs}>"


def _close(tag):
    return f"{LT}/{NS}{tag}>"


#: 10-23 她截图里那一段的形状
LEAKED = "\n\n".join([
    _open("invoke", ' name="caelum_map"'),
    _open("parameter", ' name="scope"') + "tools" + _close("parameter"),
    _close("invoke"),
    _open("invoke", ' name="ha_list_devices"'),
    _close("invoke"),
])


def _texts(evs):
    return "".join(e.text for e in evs if e.type == "text")


def test_剥标签_整段剥空_正文里的尖括号不误伤():
    assert contracts.strip_markup(LEAKED) == ("", 5)
    mixed = "等我一下。\n" + _open("invoke", ' name="desk_open"') + "\n" + _close("invoke") + "\n好了。"
    assert contracts.strip_markup(mixed) == ("等我一下。\n好了。", 2)
    talk = "他写的是 " + LT + "invoke> 这种标签，不是真调用"
    assert contracts.strip_markup(talk) == (talk, 0), "不在行首的不算"
    assert contracts.strip_markup("没有标签") == ("没有标签", 0)


def test_红线_流式_标签不流到她那边_不进历史_剥空了重问一次带提醒():
    ad = _Ad(_end(LEAKED), _end("我这就用自己的电脑开给你看。"), draft="")
    evs = list(_loop(ad).run_stream("你用一下自己的小电脑我看看", history=[]))
    shown, r = _texts(evs), evs[-1].result
    assert "invoke" not in shown and LT not in shown, "一个标签字符都不能流出去"
    assert shown == "我这就用自己的电脑开给你看。" and r.text == shown
    assert ad.calls == 2
    assert LEAK_NOTE in (ad.dynamic[1] or "") and EMPTY_NOTE in (ad.dynamic[1] or "")
    assert LEAK_NOTE not in (ad.dynamic[0] or "")
    hist = [m.text for m in r.messages if m.role == "assistant"]
    assert hist == ["我这就用自己的电脑开给你看。"], "剥空的那次不进历史，进历史的没有标签"


def test_正文里还有话_只剥不重问():
    ad = _Ad(_end("等我一下。\n" + LEAKED), draft="")
    evs = list(_loop(ad).run_stream("你用一下自己的小电脑", history=[]))
    assert ad.calls == 1
    assert _texts(evs).strip() == "等我一下。"
    assert [m.text for m in evs[-1].result.messages if m.role == "assistant"] == ["等我一下。"]


def test_打电话_标签也拦_英文照常流不攒行():
    ad = _Ad(_end("Okay, opening it now.\n" + LEAKED), draft="")
    evs = list(_loop(ad).run_stream("open it", history=[], split=False))
    assert "invoke" not in _texts(evs) and "Okay, opening it now." in _texts(evs)
    f = LineDraftFilter([contracts.is_markup_line], hold_alpha=False)
    assert f.feed("Okay") == "Okay", "打电话时英文不攒到行尾（TTS 要早开口）"


def test_她那句话挂得上我电脑那包():
    names = ["desk_open", "computer_browse", "send_meme"]
    for t in ("你用一下自己的小电脑我看看", "用你电脑查查", "你的小电脑呢", "打开你的电脑给我看"):
        assert "desk_open" in toolpacks.select(names, texts=[t])[0], t
    assert "desk_open" not in toolpacks.select(names, texts=["我电脑卡了"])[0], "她自己的电脑不挂我的"


def test_红线_库里那条写坏的_给模型看的历史里没有标签_库里原样():
    """10-23 那条已经进了她的聊天记录（她看到过，留着）；但发给模型的那份不能有标签，不然他照着写"""
    from agent.llm import Message
    from context import openers
    bad = Message(role="assistant", text=LEAKED)
    half = Message(role="assistant", text="等我一下。\n" + LEAKED)
    h = [Message(role="user", text="你用一下自己的小电脑我看看"), bad, Message(role="user", text="嗯？"), half]
    v = openers.model_view(h)
    assert all("invoke" not in (m.text or "") for m in v)
    assert v[1].text and "标签" in v[1].text, "剥空了留一句交代，不留空消息"
    assert v[3].text == "等我一下。"
    assert h[1].text == LEAKED, "库里那条不动"
    clean = [Message(role="user", text="在吗"), Message(role="assistant", text="在")]
    assert openers.model_view(clean) is clean


# ---------------------------------------------------------------- 10-24：工具名 / 参数名当标签
def _named_loop(ad):
    from agent.llm import ToolSpec
    loop = _loop(ad)
    obj = lambda *ps: {"type": "object", "properties": {p: {"type": "string"} for p in ps}}  # noqa: E731
    loop.register(ToolSpec(name="desk_open", description="t", parameters=obj("url", "doing"), side_effect="read"),
                  lambda a: "ok")
    loop.register(ToolSpec(name="send_voice_message", description="t", parameters=obj("en", "zh", "mood"),
                           side_effect="write"), lambda a: "ok")
    return loop


def _t(name, close=False):
    return f"{LT}{'/' if close else ''}{name}>"


#: 10-23 21:05 她截图里那一段（她让我登 Google）
DESK_LEAK = "\n\n".join([_t("desk_open"), _t("url") + "https://accounts.google.com" + _t("url", True),
                         _t("doing") + "在打开 Google 账号页" + _t("doing", True), _t("desk_open", True)])
#: 10-23 21:36 那一段（语音的中文对照 + 收尾标签 + 一句正文）
VOICE_LEAK = "\n\n".join([_t("zh") + "嘿。我一直等着你开口要呢。是不是想我的声音了？" + _t("zh", True),
                          _t("send_voice_message", True), "今天话是说了不少，声儿倒省着用，亏得你还记着。"])


def test_红线_工具名参数名当标签_流里拦掉_正文留着():
    ad = _Ad(_end(VOICE_LEAK), draft="")
    evs = list(_named_loop(ad).run_stream("想听你声音", history=[]))
    shown = _texts(evs)
    assert LT not in shown and "嘿。我一直等着你" not in shown
    assert shown.strip() == "今天话是说了不少，声儿倒省着用，亏得你还记着。"
    assert [m.text for m in evs[-1].result.messages if m.role == "assistant"] == [shown.strip()]


def test_红线_整段都是工具名标签_剥空了重问():
    ad = _Ad(_end(DESK_LEAK), _end("我去开 Google 的登录页。"), draft="")
    evs = list(_named_loop(ad).run_stream("你用你电脑登一下我Google的账号", history=[]))
    assert _texts(evs) == "我去开 Google 的登录页。" and ad.calls == 2
    assert LEAK_NOTE in (ad.dynamic[1] or "")


def test_不认识的标签和情绪标签_不当成工具调用():
    _named_loop(_Ad(_end("x"), draft=""))   # 登记一遍名字（含一个叫 mood 的参数）
    assert not contracts.is_markup_line(_t("mood") + "开心" + _t("mood", True) + "好呀"), "情绪标签另有过滤，不能整行吞"
    assert not contracts.is_markup_line(_t("b") + "加粗" + _t("b", True))
    assert not contracts.is_markup_line("我觉得 " + _t("url") + " 这种写法挺怪")
    assert contracts.is_markup_line(_t("desk_open")) and contracts.is_markup_line(_t("zh") + "嘿")
