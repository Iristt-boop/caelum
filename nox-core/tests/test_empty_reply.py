"""一整轮一个字都没说出口 → 再问一次（2026-10-18）。

她 10-18 上午两句话（「使命召唤23…好纠结买不买」「我买了个 行尸走肉」）都没收到回音：
Core 报 answered，账本两次 `calls=…/-/…`（每次调用都没有正文），bridge 没有一条可存，像被吞了。

守的几条（流式，聊天走的那条）：
  · 空了 → 再问一次，第二次带「把话说出来」那句；这次空的不进历史
  · 再问还是空 → 认了（只问一次，不绕圈）
  · 前面已经说过话 / 已经发出去一张表情或一段语音 → 最后空着是正常收尾，不重问
  · 日志里写明心里话 / 草稿各多少字（下次分得清是哪种空）
"""

from __future__ import annotations

import logging

from agent.llm import StreamEvent, ToolCall, ToolSpec, Turn, Usage
from agent.loop import EMPTY_NOTE, AgentLoop


def _spec(name, terminal=False):
    return ToolSpec(name=name, description="t", parameters={"type": "object", "properties": {}},
                    side_effect="write", terminal=terminal)


class _Ad:
    """按顺序吐出给定的几次调用。每次可以带草稿（模型自己的 reasoning）。"""

    name = "fake"

    def __init__(self, *turns, draft="嗯，她在纠结买不买……"):
        self.turns = list(turns)
        self.draft = draft
        self.calls = 0
        self.dynamic = []
        self.seen_history = []

    def stream(self, messages, tools, **kw):
        self.dynamic.append(kw.get("dynamic_system"))
        self.seen_history.append([m.role for m in messages])
        t = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        if self.draft:
            yield StreamEvent("thinking", text=self.draft)
        if t.text:
            yield StreamEvent("text", text=t.text)
        yield StreamEvent("done", turn=t)


def _end(text):
    return Turn(stop_reason="end_turn", text=text, usage=Usage(input_tokens=30000, output_tokens=356))


def _tool(text, *names):
    return Turn(stop_reason="tool_use", text=text, usage=Usage(input_tokens=30000),
                tool_calls=[ToolCall(id=f"c{i}", name=n, arguments={}) for i, n in enumerate(names)])


def _loop(ad):
    loop = AgentLoop(adapter=ad)
    loop.register(_spec("send_meme", True), lambda a: "ok")
    loop.register(_spec("web_search"), lambda a: "结果")
    return loop


def _run(ad):
    evs = list(_loop(ad).run_stream("我买了个 行尸走肉", history=[]))
    return "".join(e.text for e in evs if e.type == "text"), evs[-1].result


def test_红线_一个字都没说_再问一次_第二次说出来了():
    ad = _Ad(_end(""), _end("行尸走肉？哪一版。"))
    text, r = _run(ad)
    assert ad.calls == 2
    assert text == "行尸走肉？哪一版。" and r.outcome == "answered" and r.text == "行尸走肉？哪一版。"
    assert EMPTY_NOTE not in (ad.dynamic[0] or "")
    assert EMPTY_NOTE in (ad.dynamic[1] or ""), "重问那次要告诉他：刚才没说出口"
    # 空的那次不进历史：第二次看到的历史和第一次一样
    assert ad.seen_history[1] == ad.seen_history[0]
    assert [m.role for m in r.messages] == ["user", "assistant"]


def test_查完东西才空_也重问():
    """10-18 第一句就是这样：先 web_search（没写字），回来又一个字没写。"""
    ad = _Ad(_tool("", "web_search"), _end(""), _end("出了，等打折再说。"))
    text, r = _run(ad)
    assert ad.calls == 3 and text == "出了，等打折再说。"


def test_重问还是空_就认了_不绕圈():
    ad = _Ad(_end(""), _end(""), _end("不该走到这里"))
    text, r = _run(ad)
    assert ad.calls == 2 and text == "" and r.outcome == "answered"


def test_前面已经说过话_最后空着是正常收尾_不重问():
    ad = _Ad(_tool("我去查查。", "web_search"), _end(""))
    text, r = _run(ad)
    assert ad.calls == 2 and text == "我去查查。"


def test_已经发出去一张表情_最后空着不重问():
    ad = _Ad(_tool("", "send_meme"), _end(""))
    _, r = _run(ad)
    assert ad.calls == 2


def test_空回复留痕_写明心里话和草稿各多少字(caplog):
    ad = _Ad(_end(""), _end("在。"))
    with caplog.at_level(logging.WARNING, logger="agent.loop"):
        _run(ad)
    line = next(r.getMessage() for r in caplog.records if "空回复" in r.getMessage())
    assert "第 1 次调用" in line and "草稿 11 字" in line and "心里话 0 字" in line and "再问一次" in line


def test_整段写进心里话_也算没说出口_日志写明心里话有字(caplog):
    """忘了收口的心里话整块当心里话（10-15 规则），正文就是空的 —— 日志要分得出是这种空。"""
    ad = _Ad(_end("<心里>她在纠结买不买，"), _end("等打折。"), draft="")
    with caplog.at_level(logging.WARNING, logger="agent.loop"):
        text, _ = _run(ad)
    line = next(r.getMessage() for r in caplog.records if "空回复" in r.getMessage())
    assert "心里话 0 字" not in line and "草稿 0 字" in line
    assert text == "等打折。"
