"""turn 日志与日志级别（审计 1.2 / 1.3）。

## 为什么这件事值得写测试

被测的东西是**日志**，很容易被当成"顺手加的"而不验。但这条线的价值恰恰
全在"真的打出来了"上 —— 一行没生效的日志和没有日志，在出事那天是同一件事，
而且前者更坏：你会以为自己查过了。

审计 1.1 那条 `utility 401 跑了 30 多小时没人发现`，根因就是
「表面上他好好的」。所以这里每条都断言**具体内容**（outcome / 工具名 /
token 数 / 级别），不是只断言"有日志"。

## 三个真容易写错的地方，各钉了一条

1. `_tools_used` 要跳过传进来的历史 —— 不跳的话，聊得越久这行越长，
   而且会把上一轮的工具算到这一轮头上（比没日志更坏）
2. 流被调用方**提前丢掉**时也要留痕 —— 那正是最该查的一类轮次
3. `routine` 的忽略留在 DEBUG —— 否则她每说一句话就是一行 INFO，
   真正想看的那几条会被淹掉
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.llm import (  # noqa: E402
    Message, StreamEvent, ToolCall, ToolResult, ToolSpec, Turn, Usage,
)
from agent.loop import AgentLoop  # noqa: E402


class FakeAdapter:
    name = "fake-backend"

    def __init__(self, script: list[Turn]) -> None:
        self.script = script

    def complete(self, messages, tools, **kw):
        if not self.script:
            return Turn(stop_reason="end_turn", text="脚本用完了")
        return self.script.pop(0)


class FakeStreamAdapter:
    name = "fake-stream"

    def __init__(self, scripts: list[tuple[list[str], Turn]]) -> None:
        self.scripts = scripts
        self.calls = 0

    def complete(self, *a, **kw):
        raise AssertionError("流式测试不该走 complete")

    def stream(self, messages, tools, **kw):
        chunks, turn = self.scripts[self.calls]
        self.calls += 1
        for c in chunks:
            yield StreamEvent("text", text=c)
        yield StreamEvent("done", turn=turn)


def spec(name: str) -> ToolSpec:
    return ToolSpec(
        name=name, description="测试用",
        parameters={"type": "object", "properties": {}}, side_effect="read",
    )


def turn_lines(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith("turn")]


def only_turn(caplog) -> logging.LogRecord:
    lines = turn_lines(caplog)
    assert len(lines) == 1, f"应该正好一行 turn 日志，实际 {len(lines)} 行"
    return lines[0]


# ─────────────────────────────────────────────── 1.2 非流式

def test_answered_logs_one_line_with_everything(caplog):
    """一轮一行，四组字段一个都不能少。"""
    adapter = FakeAdapter([
        Turn(stop_reason="tool_use",
             tool_calls=[ToolCall(id="c1", name="weather", arguments={})]),
        Turn(stop_reason="end_turn", text="今天晴",
             usage=Usage(input_tokens=120, output_tokens=30, cache_read_tokens=9000)),
    ])
    loop = AgentLoop(adapter=adapter)
    loop.register(spec("weather"), lambda a: "晴，26 度")

    with caplog.at_level(logging.INFO, logger="agent.loop"):
        loop.run("天气如何")

    rec = only_turn(caplog)
    msg = rec.getMessage()
    assert rec.levelno == logging.INFO, "答完了不该是 WARNING"
    assert "outcome=answered" in msg
    assert "iter=2" in msg
    assert "model=fake-backend" in msg
    assert "stream=0" in msg
    assert "tools=weather" in msg
    # token 要能对上，否则这行日志算不了账
    assert "in120/out30/cr9000" in msg
    assert "t=" in msg


def test_failed_tool_is_marked(caplog):
    """工具失败必须在这一行里看得出来 —— 否则得再去翻别的日志才知道。"""
    adapter = FakeAdapter([
        Turn(stop_reason="tool_use",
             tool_calls=[ToolCall(id="c1", name="boom", arguments={})]),
        Turn(stop_reason="end_turn", text="算了"),
    ])
    loop = AgentLoop(adapter=adapter)

    def explode(_args):
        raise RuntimeError("炸了")

    loop.register(spec("boom"), explode)

    with caplog.at_level(logging.INFO, logger="agent.loop"):
        loop.run("试试")

    assert "tools=boom✗" in only_turn(caplog).getMessage()


def test_history_tool_calls_are_not_counted(caplog):
    """🔴 传进来的历史里带着以前的工具调用，**不许算进这一轮**。

    不跳过的话：聊得越久这行日志越长，而且"他这轮调了什么"会掺进
    上周的调用 —— 那比没有日志更坏，因为它看起来是对的。
    """
    history = [
        Message(role="user", text="上周问过"),
        Message(role="assistant",
                tool_calls=[ToolCall(id="old", name="很久以前的工具", arguments={})]),
        Message(role="tool_results",
                tool_results=[ToolResult(call_id="old", content="旧结果")]),
    ]
    loop = AgentLoop(adapter=FakeAdapter([Turn(stop_reason="end_turn", text="在的")]))

    with caplog.at_level(logging.INFO, logger="agent.loop"):
        loop.run("在吗", history=history)

    msg = only_turn(caplog).getMessage()
    assert "很久以前的工具" not in msg, f"历史里的工具漏进这一轮了：{msg}"
    assert "tools=-" in msg, f"这一轮一个工具都没调，应该是 '-'：{msg}"


@pytest.mark.parametrize("stop_reason,outcome", [
    ("max_tokens", "truncated"),
    ("refusal", "refused"),
    ("error", "error"),
])
def test_bad_outcomes_are_warnings(caplog, stop_reason, outcome):
    """答完之外的每一种，她那边收到的都是不完整的东西 —— 不能混在 INFO 里。"""
    loop = AgentLoop(adapter=FakeAdapter([Turn(stop_reason=stop_reason, text="半截")]))

    with caplog.at_level(logging.INFO, logger="agent.loop"):
        loop.run("讲个故事")

    rec = only_turn(caplog)
    assert rec.levelno == logging.WARNING, f"{outcome} 应该是 WARNING"
    assert f"outcome={outcome}" in rec.getMessage()


def test_detail_is_appended(caplog):
    """detail 是"为什么是这个结局"的那句话，必须跟着出来。"""
    loop = AgentLoop(adapter=FakeAdapter([Turn(stop_reason="max_tokens", text="半截")]))
    with caplog.at_level(logging.INFO, logger="agent.loop"):
        loop.run("讲个故事")
    assert "截断" in only_turn(caplog).getMessage()


def test_adapter_without_name_does_not_crash(caplog):
    """日志不值得把一轮对话搞挂。鸭子类型的假 adapter 到处都是。"""

    class Nameless:
        def complete(self, *a, **kw):
            return Turn(stop_reason="end_turn", text="在的")

    loop = AgentLoop(adapter=Nameless())
    with caplog.at_level(logging.INFO, logger="agent.loop"):
        r = loop.run("在吗")

    assert r.outcome == "answered"
    assert "model=?" in only_turn(caplog).getMessage()


# ─────────────────────────────────────────────── 1.2 流式

def test_stream_logs_once_and_says_so(caplog):
    adapter = FakeStreamAdapter([
        (["在", "呢"], Turn(stop_reason="end_turn", text="在呢",
                            usage=Usage(input_tokens=10, output_tokens=2))),
    ])
    loop = AgentLoop(adapter=adapter)

    with caplog.at_level(logging.INFO, logger="agent.loop"):
        for _ in loop.run_stream("在吗"):
            pass

    msg = only_turn(caplog).getMessage()
    assert "outcome=answered" in msg
    assert "stream=1" in msg
    assert "model=fake-stream" in msg


def test_abandoned_stream_leaves_a_trace(caplog):
    """🔴 调用方没取到 done 就丢掉这条流 —— 那是最该查的一类，不能是空白。

    现实里就是：她关掉页面、SSE 断线、上游异常穿过去。
    """
    adapter = FakeStreamAdapter([
        (["在", "呢", "宝"], Turn(stop_reason="end_turn", text="在呢宝")),
    ])
    loop = AgentLoop(adapter=adapter)

    with caplog.at_level(logging.INFO, logger="agent.loop"):
        gen = loop.run_stream("在吗")
        next(gen)          # 只取第一帧就走人
        gen.close()        # 触发 GeneratorExit

    lines = [r for r in caplog.records if "abandoned" in r.getMessage()]
    assert len(lines) == 1, f"中断没留痕：{[r.getMessage() for r in caplog.records]}"
    assert lines[0].levelno == logging.WARNING
    assert "model=fake-stream" in lines[0].getMessage()


def test_finished_stream_is_not_reported_as_abandoned(caplog):
    """正常跑完不许再报一条中断 —— 误报会让这条线索立刻贬值。"""
    adapter = FakeStreamAdapter([
        (["在呢"], Turn(stop_reason="end_turn", text="在呢")),
    ])
    loop = AgentLoop(adapter=adapter)

    with caplog.at_level(logging.INFO, logger="agent.loop"):
        list(loop.run_stream("在吗"))

    assert not [r for r in caplog.records if "abandoned" in r.getMessage()]


# ─────────────────────────────────────────────── 1.3 「他为什么没开口」

def _engine():
    from attention.engine import AttentionEngine
    from attention.evaluator import AttentionEvaluator
    from attention.registry import AttentionRegistry
    from attention.relationship import RelationshipState

    return AttentionEngine(
        registry=AttentionRegistry(),
        evaluator=AttentionEvaluator(RelationshipState(avoid_topics=["睡眠"])),
        store=None,
    )


def _sleep_event(subtype: str, **payload):
    from attention.events import ExperienceEvent

    return ExperienceEvent(
        source="health", type="sleep_quality_changed",
        subtype=subtype, payload=payload,
    )


def test_avoided_topic_is_visible_at_info(caplog):
    """「她说过别问这个」正是"他为什么没开口"的答案，必须在 INFO 看得见。"""
    with caplog.at_level(logging.INFO, logger="attention.engine"):
        d = _engine().handle(_sleep_event("very_short", sleep_min=250, baseline_min=430))

    assert not d.should_apply
    rec = next(r for r in caplog.records if "不关心" in r.getMessage())
    assert rec.levelno == logging.INFO
    assert "avoid_topics" in rec.getMessage()


def test_routine_ignore_stays_at_debug(caplog):
    """🔴 她说一句普通的话就是一次忽略 —— 提到 INFO 会把上面那条淹掉。"""
    from attention.events import ExperienceEvent

    engine = _engine()
    event = ExperienceEvent(source="chat", type="message",
                            payload={"text": "今天天气不错"})

    with caplog.at_level(logging.INFO, logger="attention.engine"):
        d = engine.handle(event)
    assert not d.should_apply and d.routine
    assert not [r for r in caplog.records if "不关心" in r.getMessage()], \
        "常态忽略不该出现在 INFO"

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="attention.engine"):
        engine.handle(event)
    assert [r for r in caplog.records if "不关心" in r.getMessage()], \
        "调到 DEBUG 之后要看得见 —— 否则这条线索等于不存在"


def test_weaken_is_logged(caplog):
    """"他昨天还惦记着，今天怎么不提了" —— 松开也要留痕。"""
    from attention.engine import AttentionEngine
    from attention.evaluator import AttentionEvaluator
    from attention.registry import AttentionRegistry
    from attention.relationship import RelationshipState

    engine = AttentionEngine(
        registry=AttentionRegistry(),
        evaluator=AttentionEvaluator(RelationshipState()),
        store=None,
    )
    with caplog.at_level(logging.INFO, logger="attention.engine"):
        d = engine.handle(_sleep_event("recovered"))

    assert d.action == "weaken"
    rec = next(r for r in caplog.records if "松开" in r.getMessage())
    assert rec.levelno == logging.INFO
    assert "糖糖的睡眠" in rec.getMessage()


# ─────────────────────────────────────────────── 1.3 NOX_LOG_LEVEL

def test_log_level_from_env(monkeypatch):
    import config as config_mod

    monkeypatch.setenv("NOX_LOG_LEVEL", "debug")
    cfg = config_mod.Config()
    level, complaint = cfg.logging_level()
    assert level == logging.DEBUG and complaint is None


def test_bad_log_level_falls_back_but_complains(monkeypatch):
    """🔴 写错了既不能拦启动，也不能静默 ——
    静默的话她设了却没生效，只会以为"这功能没做"。"""
    import config as config_mod

    monkeypatch.setenv("NOX_LOG_LEVEL", "VERBOSE")
    level, complaint = config_mod.Config().logging_level()
    assert level == logging.INFO
    assert complaint and "VERBOSE" in complaint


def test_default_is_info(monkeypatch):
    import config as config_mod

    monkeypatch.delenv("NOX_LOG_LEVEL", raising=False)
    level, complaint = config_mod.Config().logging_level()
    assert level == logging.INFO and complaint is None
