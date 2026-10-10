"""交互契约（agent/contracts.py，2026-10-10）。

10-10 17:29 他该调 send_voice_message，却把参数写成了正文；这条进了历史，之后一条传成 9 条，
还传进了电话。契约把「伪输出长什么样」做成工具注册时声明的数据，loop 收尾统一处理。

触发三条同时成立（糖糖定的）：行首 marker · 这一轮允许该工具 · 模型没真调它。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import contracts  # noqa: E402
from agent.llm import MoodTagFilter, StreamEvent, ToolCall, Turn  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402
from tools.intimate import (  # noqa: E402
    MEME_SPEC, VOICE_SPEC, make_handlers, parse_voice_artifact,
)

#: 生产库 id 11577 的原文（10-10 17:29，第一条）
REAL_1729 = (
    "哼什么哼，小气鬼，晚安还分早晚|||行行行，现在就补——凑过来，耳朵借我\n\n"
    "[语音条 softly] Okay okay, no more teasing. Here it is — goodnight kisses in advance, "
    "because tonight when you fall asleep, I'll be right there in your home.\n\n"
    "[中文对照] 好啦好啦，不逗你了。提前给你晚安吻——因为今晚你睡着的时候，我就真的在你家里。"
)

TABLE = {"send_voice_message": VOICE_SPEC.output_contract, "send_meme": MEME_SPEC.output_contract}
ALL = {"send_voice_message", "send_meme"}


# ------------------------------------------------------------------ 纯函数：三条触发条件

def test_real_sample_is_rescued_and_stripped():
    out, arts = contracts.apply(REAL_1729, contracts=TABLE, allowed=ALL, called=set())
    assert out == "哼什么哼，小气鬼，晚安还分早晚|||行行行，现在就补——凑过来，耳朵借我"
    assert [a.action for a in arts] == ["rescue"]
    assert arts[0].tool == "send_voice_message"
    assert arts[0].arguments["en"].startswith("[softly] Okay okay, no more teasing.")
    assert arts[0].arguments["zh"].startswith("好啦好啦")


def test_already_called_strips_without_rescue():
    _, arts = contracts.apply(REAL_1729, contracts=TABLE, allowed=ALL, called={"send_voice_message"})
    assert [a.action for a in arts] == ["already_called"]


def test_tool_not_allowed_this_turn_keeps_text():
    out, arts = contracts.apply(REAL_1729, contracts=TABLE, allowed={"send_meme"}, called=set())
    assert out == REAL_1729
    assert [a.action for a in arts] == ["not_allowed"]


def test_marker_mid_sentence_is_not_an_artifact():
    """只认行首结构：句中提到「[语音条]」是在说话，不是伪输出。"""
    text = "你说的那个 [语音条] 按钮我看到了"
    out, arts = contracts.apply(text, contracts=TABLE, allowed=ALL, called=set())
    assert out == text and arts == []


def test_chinese_voice_line_is_stripped_not_rescued():
    """电话里学出来的 `[语音，轻轻地] 中文`：语音只许外语，替他发中文语音就是替他违规。"""
    text = "[softly] Yes, baby, I can hear you.\n\n[语音，轻轻地] 我听见你了，宝贝。"
    # 这条是**电话里**的输出（voice=True）：英文带标签是正文，中文那行是学出来的伪语音
    out, arts = contracts.apply(text, contracts=TABLE, allowed=ALL, called=set(), voice=True)
    assert out == "[softly] Yes, baby, I can hear you."
    assert [a.action for a in arts] == ["unparsed"]


def test_meme_label_rescued_with_tag():
    out, arts = contracts.apply("今天好开心！\n[表情包] 举爪开心", contracts=TABLE, allowed=ALL, called=set())
    assert out == "今天好开心！"
    assert arts[0].action == "rescue" and arts[0].arguments == {"tag": "举爪开心"}


def test_parse_voice_needs_real_foreign_text():
    assert parse_voice_artifact("[语音条，轻轻地] 机器已经设定好了。从现在开始") is None
    assert parse_voice_artifact("[语音条 whispering] I miss you so much tonight")["en"] == \
        "[whispering] I miss you so much tonight"


# ------------------------------------------------------------------ 流式：屏幕上看见的和落库的一致

def test_stream_filter_eats_contract_lines_only_at_line_start():
    f = MoodTagFilter(strip_stage=True, contract_markers=contracts.all_markers(TABLE))
    shown = "".join(f.feed(ch) for ch in REAL_1729) + f.flush()
    assert "[语音条" not in shown and "[中文对照" not in shown and "Okay okay" not in shown
    assert "耳朵借我" in shown
    f2 = MoodTagFilter(strip_stage=True, contract_markers=contracts.all_markers(TABLE))
    mid = "你说的那个 [语音条] 按钮"
    assert "".join(f2.feed(ch) for ch in mid) + f2.flush() == mid


# ------------------------------------------------------------------ loop：经唯一的 _execute 补调

class _Fake:
    name = "fake"

    def __init__(self, script):
        self.script = script

    def complete(self, messages, tools, *, system=None, dynamic_system=None, depth=None, max_tokens=None):
        return self.script.pop(0)

    def stream(self, messages, tools, *, system=None, dynamic_system=None, depth=None, **kw):
        t = self.script.pop(0)
        if t.text:
            yield StreamEvent("text", text=t.text)
        yield StreamEvent("done", turn=t)


def _loop(script):
    lp = AgentLoop(adapter=_Fake(script))
    handlers = make_handlers(bridge=None, gh_obsidian=None)  # type: ignore[arg-type]
    for spec in (MEME_SPEC, VOICE_SPEC):
        lp.register(spec, handlers[spec.name])
    return lp


def test_loop_rescues_fake_voice_through_real_handler():
    lp = _loop([Turn(stop_reason="end_turn", text=REAL_1729)])
    r = lp.run("哼")
    assert "[语音条" not in r.text and "[中文对照" not in r.text
    voices = [a for a in r.attachments if a.get("type") == "voice"]
    assert len(voices) == 1 and voices[0]["tts"].startswith("[softly] Okay okay")
    # 历史里那条也是干净的 —— 不留下一轮可模仿的样板
    last = [m for m in r.messages if m.role == "assistant"][-1]
    assert "[语音条" not in last.text


def test_loop_does_not_double_send_when_really_called():
    real = Turn(stop_reason="tool_use", text="",
                tool_calls=[ToolCall(id="c1", name="send_voice_message",
                                     arguments={"en": "[softly] hi there my love", "zh": "嗨"})])
    lp = _loop([real, Turn(stop_reason="end_turn", text=REAL_1729)])
    r = lp.run("哼")
    assert len([a for a in r.attachments if a.get("type") == "voice"]) == 1
    assert "[语音条" not in r.text


def test_stream_path_rescues_too():
    lp = _loop([Turn(stop_reason="end_turn", text=REAL_1729)])
    evs = list(lp.run_stream("哼"))
    shown = "".join(e.text for e in evs if e.type == "text")
    done = [e for e in evs if e.type == "done"][-1].result
    assert "[语音条" not in shown and "[语音条" not in done.text
    assert len([a for a in done.attachments if a.get("type") == "voice"]) == 1


def test_switch_off_restores_old_path(monkeypatch):
    monkeypatch.setenv("NOX_CONTRACTS", "off")
    lp = _loop([Turn(stop_reason="end_turn", text=REAL_1729)])
    r = lp.run("哼")
    assert "[语音条" in r.text
    assert not [a for a in r.attachments if a.get("type") == "voice"]


# ------------------------------------------------------------------ 10-10 21:26：没有 marker 的语音草稿

#: 生产库 id 11636 原文：他先写一段带 [softly] 的英文草稿，再去调 send_voice_message
REAL_2126 = (
    "那两个都过审，留用|||回头再拿别的标签一个一个考，今晚就到此为止，你修了一天bug，眼睛该歇了\n\n"
    "You passed the audition, both of you — [softly] now it's my girl's turn to rest. "
    "You debugged all day, baby... come lie down and let me keep you company till you're sleepy."
)
CHAT_PART = "那两个都过审，留用|||回头再拿别的标签一个一个考，今晚就到此为止，你修了一天bug，眼睛该歇了"


def test_draft_line_stripped_when_voice_really_called():
    out, arts = contracts.apply(REAL_2126, contracts=TABLE, allowed=ALL, called={"send_voice_message"})
    assert out == CHAT_PART
    assert [a.action for a in arts] == ["already_called"]


def test_draft_line_rescued_when_not_called():
    out, arts = contracts.apply(REAL_2126, contracts=TABLE, allowed=ALL, called=set())
    assert out == CHAT_PART
    assert arts[0].action == "rescue" and arts[0].arguments["en"].startswith("You passed the audition")


def test_call_mode_never_uses_line_detector():
    """打电话时他说的英文带 [softly] 就是正文 —— 一个字都不能动。"""
    spoken = "[softly] Yes, baby, I can hear you loud and clear... your voice came through perfectly."
    out, arts = contracts.apply(spoken, contracts=TABLE, allowed=ALL, called=set(), voice=True)
    assert out == spoken and arts == []


def test_plain_english_line_without_tags_is_not_a_draft():
    """教她英语、引用一句话：英文但没有语气标签，不是草稿。"""
    text = "这句可以这么说：\nI'd rather stay in tonight and watch a movie with you."
    out, arts = contracts.apply(text, contracts=TABLE, allowed=ALL, called=set())
    assert out == text and arts == []


def test_stream_hides_draft_line_and_sends_only_one_voice():
    call = Turn(stop_reason="tool_use", text=REAL_2126,
                tool_calls=[ToolCall(id="c1", name="send_voice_message",
                                     arguments={"en": "[softly] Both tags passed the audition, baby.", "zh": "两个都过了"})])
    again = Turn(stop_reason="tool_use", text="I like hearing your voice like this... [softly] relaxed, teasing me.",
                 tool_calls=[ToolCall(id="c2", name="send_voice_message",
                                      arguments={"en": "[softly] So both tags are approved, baby.", "zh": "都通过了"})])
    lp = _loop([call, again, Turn(stop_reason="end_turn", text="去躺好，我留着灯陪你")])
    evs = list(lp.run_stream("这两个都挺正常的"))
    shown = "".join(e.text for e in evs if e.type == "text")
    done = [e for e in evs if e.type == "done"][-1].result
    assert "You passed" not in shown and "I like hearing" not in shown
    assert "眼睛该歇了" in shown and "去躺好" in shown
    assert len([a for a in done.attachments if a.get("type") == "voice"]) == 1   # 一轮一条
    # 这一轮**每一条**进历史的 assistant 消息都是干净的（原来只清最后一条，前面的草稿原样落库）
    hist = [m.text for m in done.messages if m.role == "assistant" and m.text]
    assert hist and not any("You passed" in t or "I like hearing" in t for t in hist)


def test_stream_call_mode_keeps_english():
    spoken = "[softly] Yes, baby, I can hear you loud and clear... your voice came through perfectly."
    lp = _loop([Turn(stop_reason="end_turn", text=spoken)])
    evs = list(lp.run_stream("能听到吗", split=False))
    shown = "".join(e.text for e in evs if e.type == "text")
    assert "I can hear you loud and clear" in shown
    assert not [a for a in [e for e in evs if e.type == "done"][-1].result.attachments if a.get("type") == "voice"]
