"""工具调用被写成文字 —— 出口的成对标签状态机 + 两个病分开治（2026-10-23 / 10-24，她定）。

她 10-24：「遇到 <xxx> 进块，开始吞；遇到 </xxx> 出块，丢弃整块；块外的字符，原样输出。」
「剥离要在完整流结束后做最终清理，不能只在 chunk 边界上扫。」
「<send_meme tag=…> 可能是只写没调；这次是调了没删。两个病，别用同一个补丁治。」

守的几条（流式，聊天走的那条）：
  · 🔴 标签一个字都不流到她那边（文字聊天 / 打电话都一样）；进历史的那份和她看到的一样
  · 🔴 只写没调：剥掉，这一轮不收尾，带着「你写的 X 没执行」再问他一次 —— 他真调了，事情才真做了（只问一次）
  · 🔴 调了没删：剥掉就完，不再问
  · 剥空了、也没有写成文字的工具（只是参数标签 / 不认识的标签）：走原来的「没说出口」重问
  · 给模型看的旧历史也过同一台机器；她那句话挂得上我电脑那包

⚠️ 标签字符串用拼的（写这份测试的那一端会把整段标签当成真调用吃掉）。
"""

from __future__ import annotations

from agent import toolpacks
from agent.llm import Message, ToolCall, ToolSpec, Turn, Usage
from agent.loop import EMPTY_NOTE, LEAK_NOTE
from tests.test_empty_reply import _Ad, _end, _loop

LT = chr(60)


def _t(name, attrs="", close=False):
    return f"{LT}{'/' if close else ''}{name}{attrs}>"


def _texts(evs):
    return "".join(e.text for e in evs if e.type == "text")


def _named_loop(ad):
    loop = _loop(ad)
    obj = lambda *ps: {"type": "object", "properties": {p: {"type": "string"} for p in ps}}  # noqa: E731
    loop.register(ToolSpec(name="desk_open", description="t", parameters=obj("url", "doing"), side_effect="read"),
                  lambda a: "开好了")
    loop.register(ToolSpec(name="send_voice_message", description="t", parameters=obj("en", "zh"),
                           side_effect="write", terminal=True), lambda a: "发出去了")
    return loop


def _tool_turn(text, *names):
    return Turn(stop_reason="tool_use", text=text, usage=Usage(input_tokens=30000),
                tool_calls=[ToolCall(id=f"t{i}", name=n, arguments={}) for i, n in enumerate(names)])


#: 10-23 21:05 她截图里那一段（她让我登 Google）：只写没调
DESK_LEAK = "\n\n".join([_t("desk_open"), _t("url") + "https://accounts.google.com" + _t("url", close=True),
                         _t("doing") + "在打开 Google 账号页" + _t("doing", close=True), _t("desk_open", close=True)])
#: 10-23 21:36 那一段：语音真发了，正文里又写了一遍（调了没删）
VOICE_LEAK = "\n\n".join([_t("zh") + "嘿。我一直等着你开口要呢。" + _t("zh", close=True),
                          _t("send_voice_message", close=True), "今天话是说了不少，亏得你还记着。"])


def test_红线_只写没调_剥掉_提醒他真调一次_他真调了():
    ad = _Ad(_end(DESK_LEAK), _tool_turn("", "desk_open"), _end("开好了，等我看看。"), draft="")
    evs = list(_named_loop(ad).run_stream("你用你电脑登一下我Google的账号", history=[]))
    shown, r = _texts(evs), evs[-1].result
    assert LT not in shown and "accounts.google" not in shown
    assert shown == "开好了，等我看看。"
    assert ad.calls == 3
    assert LEAK_NOTE in (ad.dynamic[1] or "") and "desk_open" in ad.dynamic[1], "提醒里点名写成文字的是哪件"
    assert any(m.tool_calls and m.tool_calls[0].name == "desk_open" for m in r.messages), "第二次真调了"


def test_红线_只写没调_前面还有话_话留着_照样提醒():
    ad = _Ad(_end("我去开网页。\n" + DESK_LEAK), _tool_turn("", "desk_open"), _end("好了。"), draft="")
    evs = list(_named_loop(ad).run_stream("你用你电脑看看", history=[]))
    assert _texts(evs).startswith("我去开网页。") and LT not in _texts(evs)
    assert "desk_open" in (ad.dynamic[1] or "") and ad.calls == 3


def test_红线_调了没删_剥掉就完_不再问():
    ad = _Ad(_tool_turn(VOICE_LEAK, "send_voice_message"), _end(""), draft="")
    evs = list(_named_loop(ad).run_stream("想听你声音", history=[]))
    shown = _texts(evs)
    assert LT not in shown and "嘿。我一直等着你" not in shown
    assert "今天话是说了不少" in shown
    assert all(LEAK_NOTE not in (d or "") for d in ad.dynamic), "调过了就不提醒"
    hist = [m.text for m in evs[-1].result.messages if m.role == "assistant" and m.text]
    assert hist == ["今天话是说了不少，亏得你还记着。"], "进历史的和她看到的一样"


def test_红线_调了没删_不收尾的工具_下一次调用也不提醒():
    """（语音是终结型、调完就收尾，看不出来；拿一件不收尾的工具试）真调了 desk_open、正文又写了一遍 → 下一次不该带提醒"""
    ad = _Ad(_tool_turn("等我。\n" + DESK_LEAK, "desk_open"), _end("开好了。"), draft="")
    evs = list(_named_loop(ad).run_stream("看看", history=[]))
    assert ad.calls == 2 and LEAK_NOTE not in (ad.dynamic[1] or "")
    assert LT not in _texts(evs)


def test_只是参数标签_剥空了走原来的没说出口重问():
    ad = _Ad(_end(_t("zh") + "嘿" + _t("zh", close=True)), _end("在呢。"), draft="")
    evs = list(_named_loop(ad).run_stream("在吗", history=[]))
    assert _texts(evs) == "在呢。" and ad.calls == 2
    assert EMPTY_NOTE in (ad.dynamic[1] or "") and LEAK_NOTE not in (ad.dynamic[1] or "")


def test_打电话_标签也拦_英文照常流():
    ad = _Ad(_tool_turn("Okay, opening it now.\n" + VOICE_LEAK, "send_voice_message"), _end(""), draft="")
    evs = list(_named_loop(ad).run_stream("open it", history=[], split=False))
    assert LT not in _texts(evs) and "Okay, opening it now." in _texts(evs)


def test_红线_给模型看的旧历史也过同一台机器():
    from context import openers
    bad = Message(role="assistant", text=DESK_LEAK)
    half = Message(role="assistant", text="等我一下。\n" + DESK_LEAK)
    h = [Message(role="user", text="你用一下自己的小电脑我看看"), bad, Message(role="user", text="嗯？"), half]
    v = openers.model_view(h)
    assert all(LT not in (m.text or "") for m in v)
    assert v[1].text and "标签" in v[1].text, "剥空了留一句交代，不留空消息"
    assert v[3].text == "等我一下。"
    assert h[1].text == DESK_LEAK, "库里那条不动"
    clean = [Message(role="user", text="在吗"), Message(role="assistant", text="在")]
    assert openers.model_view(clean) is clean


def test_她那句话挂得上我电脑那包():
    names = ["desk_open", "computer_browse", "send_meme"]
    for t in ("你用一下自己的小电脑我看看", "用你电脑查查", "你的小电脑呢", "打开你的电脑给我看"):
        assert "desk_open" in toolpacks.select(names, texts=[t])[0], t
    assert "desk_open" not in toolpacks.select(names, texts=["我电脑卡了"])[0], "她自己的电脑不挂我的"
