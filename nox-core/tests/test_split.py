"""分段回复与 get_current_time 测试。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import SegmentSplitter, StreamEvent, Turn  # noqa: E402
from agent.loop import AgentLoop  # noqa: E402
from tools.misc import current_time  # noqa: E402


# ---------------------------------------------------------- SegmentSplitter

def run(chunks: list[str]) -> list[tuple[str, str]]:
    s = SegmentSplitter()
    out: list[tuple[str, str]] = []
    for c in chunks:
        out.extend(s.feed(c))
    rest = s.flush()
    if rest:
        out.append(("text", rest))
    return out


def test_no_marker_passes_through():
    assert run(["就是一句话"]) == [("text", "就是一句话")]


def test_single_split():
    assert run(["今天累坏了吧|||过来抱一下"]) == [
        ("text", "今天累坏了吧"),
        ("split", ""),
        ("text", "过来抱一下"),
    ]


def test_marker_cut_across_chunks():
    """流式下标记被切开是常态，不能漏切也不能把 | 吐出去。"""
    assert run(["今天累坏了吧|", "||过来抱一下"]) == [
        ("text", "今天累坏了吧"),
        ("split", ""),
        ("text", "过来抱一下"),
    ]


def test_marker_cut_one_by_one():
    assert run(["A", "|", "|", "|", "B"]) == [
        ("text", "A"), ("split", ""), ("text", "B"),
    ]


def test_multiple_splits():
    events = run(["一|||二|||三"])
    assert [e[0] for e in events] == ["text", "split", "text", "split", "text"]


def test_single_pipe_is_not_eaten():
    """正文里孤零零一个 | 该原样出去，不能被当成半个标记吞掉。"""
    assert run(["选 A | B 都行"]) == [("text", "选 A | B 都行")]


def test_trailing_pipes_flushed():
    """流结束时还扣着半个标记，原样放出去。"""
    assert run(["结尾有个 ||"]) == [("text", "结尾有个 "), ("text", "||")]


# ---------------------------------------------------------- loop 里的串联

class FakeStreamAdapter:
    name = "fake"

    def __init__(self, chunks: list[str], turn: Turn) -> None:
        self.chunks = chunks
        self.turn = turn

    def complete(self, *a, **kw):
        raise AssertionError("流式测试不该走 complete")

    def stream(self, messages, tools, **kw):
        for c in self.chunks:
            yield StreamEvent("text", text=c)
        yield StreamEvent("done", turn=self.turn)


def test_loop_emits_split_events():
    adapter = FakeStreamAdapter(
        ["今天累坏了吧", "|||", "过来抱一下"],
        Turn(stop_reason="end_turn", text="今天累坏了吧|||过来抱一下"),
    )
    events = [(e.type, e.text) for e in AgentLoop(adapter=adapter).run_stream("嗨")
              if e.type in ("text", "split")]
    assert events == [
        ("text", "今天累坏了吧"),
        ("split", ""),
        ("text", "过来抱一下"),
    ]


def test_mood_tag_stripped_before_split():
    """两级过滤的顺序：先剥情绪标记，再切分段。

    反过来的话，段末的 [mood:] 会被当成正文推出去。
    """
    adapter = FakeStreamAdapter(
        ["好的", "|||", "晚安宝贝", "\n[mood:平静]"],
        Turn(stop_reason="end_turn", text="好的|||晚安宝贝\n[mood:平静]"),
    )
    texts = [e.text for e in AgentLoop(adapter=adapter).run_stream("睡了")
             if e.type == "text"]
    joined = "".join(texts)
    assert "mood" not in joined
    assert "晚安宝贝" in joined


# ---------------------------------------------------------- get_current_time

def test_elevenlabs_tags_survive_the_mood_filter():
    """[whining] 这类是给 TTS 的，必须原样留着；只有 [mood:] 该被剥掉。

    两个都是方括号，过滤器一刀切的话，糖糖的电话里就没有语气起伏了。
    """
    from agent.llm import MoodTagFilter

    f = MoodTagFilter()
    src = "[whining] Baby... [pause] I missed you\n[mood:撒娇]"
    out = "".join(f.feed(c) for c in src) + f.flush()

    assert "[whining]" in out, "ElevenLabs 情绪标签被误删了"
    assert "[pause]" in out, "停顿标记被误删了"
    assert "mood" not in out, "内部情绪标记漏出去了"


def test_voice_mode_disables_split():
    """语音模式下 ||| 不能变成 split 事件 —— 那是要送进 TTS 的文本。"""
    adapter = FakeStreamAdapter(
        ["好的", "|||", "晚安"],
        Turn(stop_reason="end_turn", text="好的|||晚安"),
    )
    events = [e.type for e in AgentLoop(adapter=adapter).run_stream("嗨", split=False)]
    assert "split" not in events


def test_current_time_format():
    out = current_time({})
    assert re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}（周[一二三四五六日]，", out)
    assert "UTC+8" in out


def test_current_time_is_beijing_not_machine_tz():
    """固定 UTC+8。机器时区没设对的话，模型会理直气壮报错误时间且不报错。"""
    from datetime import datetime, timedelta, timezone

    out = current_time({})
    expected = datetime.now(timezone(timedelta(hours=8)))
    assert out.startswith(expected.strftime("%Y-%m-%d %H:%M"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
