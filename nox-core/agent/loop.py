"""Agent Loop —— 核心推理循环。

模型无关：只跟 LLMAdapter 说话，不认识任何一家的参数名。

四个关键设计，每个都对应一个具体的坑：
  1. 硬上限 + 区分结局 —— 跑满循环和答完是两回事，混在一起就永远不知道
     他是答完了还是在原地打转
  2. refusal / max_tokens 单独处理 —— max_tokens 是截断不是完成，当成完成
     处理就会把半截答案当最终回复；refusal 时 content 可能是空的
  3. 所有 tool_result 装进同一条消息 —— 拆开不报错，但会让模型逐渐放弃
     并行调用工具
  4. 工具失败原样回传 —— 见 guard.py 开头那段
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from agent import guard
from tools import context as tool_context
from agent.llm import (
    Depth,
    LLMAdapter,
    Message,
    MoodTagFilter,
    SegmentSplitter,
    StreamEvent,
    ToolCall,
    ToolSpec,
    Turn,
    Usage,
)

logger = logging.getLogger(__name__)

# 工具处理函数：拿到已解析的参数 dict，返回字符串结果。
# 抛异常是允许的 —— loop 会捕获并按"工具失败"回传给模型。
ToolHandler = Callable[[dict[str, Any]], str]


@dataclass
class Tool:
    spec: ToolSpec
    handler: ToolHandler


@dataclass
class LoopResult:
    """一次 run 的结局。

    outcome 的取值决定调用方怎么处理：
      answered      正常答完
      refused       模型拒绝回答
      truncated     输出被 max_tokens 截断（**不是**完成）
      tool_stuck    同一工具连续失败，主动收手
      exhausted     跑满循环上限还没结束
      error         调用链本身出错
    """

    outcome: str
    text: str | None
    iterations: int
    usage: Usage
    messages: list[Message]
    detail: str | None = None
    # 工具产生的附带产物（目前只有「要发到聊天里的图片」）。
    # 见 tools/context.py —— 有些工具的意义不在返回文本，在于产生副作用，
    # 而 Core 够不着 bridge 那条 SSE 连接，只能这样把意图传出去。
    attachments: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.outcome == "answered"


@dataclass
class AgentLoop:
    adapter: LLMAdapter
    tools: dict[str, Tool] = field(default_factory=dict)
    max_iterations: int = 12
    # 聊天不需要深度思考。糖糖问的多是"记不记得""帮我开个灯"这类，
    # 答案在记忆和工具里，不在推理里 —— 让模型多想只是白等。
    # 真需要深想的场景（多步任务规划）由 Planner 层单独指定 high。
    #
    # 注意：这个值只在 Anthropic 原生 adapter 上生效（映射到 output_config.effort）。
    # OpenAI 兼容侧没有对应概念，见 adapters.py 里的说明。
    depth: Depth = "low"
    # 同一个工具连续失败几次就收手
    failure_limit: int = 2

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self.tools[spec.name] = Tool(spec=spec, handler=handler)

    def run(
        self,
        user_text: str,
        *,
        system: str | None = None,
        dynamic_system: str | None = None,
        history: list[Message] | None = None,
        images: list[str] | None = None,
        adapter: LLMAdapter | None = None,
    ) -> LoopResult:
        # 上下文当局部变量持有，不用 contextvar 包整轮 ——
        # 那样在流式路径下会炸（见 tools/context.py 开头）
        ctx = tool_context.ToolContext()
        result = self._run_inner(
            user_text, ctx, system=system, dynamic_system=dynamic_system,
            history=history, images=images, adapter=adapter,
        )
        result.attachments = ctx.attachments
        return result

    def _run_inner(
        self,
        user_text: str,
        ctx: tool_context.ToolContext,
        *,
        system: str | None = None,
        dynamic_system: str | None = None,
        history: list[Message] | None = None,
        images: list[str] | None = None,
        adapter: LLMAdapter | None = None,
    ) -> LoopResult:
        # 这一轮用哪个后端。传了就用传的（糖糖在前端换了模型），
        # 没传用默认的 —— loop 本身不认识"模型"这个概念，只认 adapter
        llm = adapter or self.adapter
        messages: list[Message] = list(history or [])
        messages.append(Message(role="user", text=user_text, images=images or []))

        specs = [t.spec for t in self.tools.values()]
        tracker = guard.FailureTracker(limit=self.failure_limit)
        total = Usage()
        iterations = 0

        for iterations in range(1, self.max_iterations + 1):
            turn = llm.complete(
                messages,
                specs,
                system=system,
                dynamic_system=dynamic_system,
                depth=self.depth,
            )
            _accumulate(total, turn.usage)

            if turn.stop_reason == "error":
                return LoopResult("error", None, iterations, total, messages, turn.error)

            if turn.stop_reason == "refusal":
                # 拒答时 text 通常为 None —— 不要去读 content，直接走这条路
                return LoopResult("refused", turn.text, iterations, total, messages, turn.error)

            if turn.stop_reason == "max_tokens":
                # 截断。返回已有内容但标明它不完整 —— 当成 answered 处理，
                # 调用方就会把半截答案当成最终回复发给糖糖。
                messages.append(_assistant_message(turn))
                return LoopResult(
                    "truncated",
                    turn.text,
                    iterations,
                    total,
                    messages,
                    "输出被 max_tokens 截断，回答不完整",
                )

            if turn.stop_reason == "end_turn" or not turn.tool_calls:
                messages.append(_assistant_message(turn))
                return LoopResult("answered", turn.text, iterations, total, messages)

            # --- 要调工具 ---
            messages.append(_assistant_message(turn))

            outcomes = [self._execute(call, tracker, ctx) for call in turn.tool_calls]
            # 一轮里的全部结果放进同一条消息（见文件开头第 3 条）
            messages.append(
                Message(role="tool_results", tool_results=[o.result for o in outcomes])
            )

            stuck = tracker.exhausted()
            if stuck:
                return LoopResult(
                    "tool_stuck",
                    turn.text,
                    iterations,
                    total,
                    messages,
                    f"这些工具连续失败 {self.failure_limit} 次，已停止重试：{', '.join(stuck)}",
                )
        else:
            # for 正常跑完 = 一次都没 return = 跑满上限还没结束。
            # 这个 else 不能省：它是"打转"和"答完"的唯一区分点。
            return LoopResult(
                "exhausted",
                None,
                iterations,
                total,
                messages,
                f"达到循环上限 {self.max_iterations} 轮仍未结束",
            )

    def run_stream(
        self,
        user_text: str,
        *,
        system: str | None = None,
        dynamic_system: str | None = None,
        history: list[Message] | None = None,
        images: list[str] | None = None,
        split: bool = True,
        adapter: LLMAdapter | None = None,
    ) -> Iterator[StreamEvent]:
        """流式版的 run。

        文本增量实时吐出，工具调用照旧在内部跑完。最后一个事件是 done，
        里面带完整的 LoopResult（挂在 turn 上传不方便，所以用属性传）。

        注意：**要调工具的那一轮，文本也会先流出来**。模型常常先说
        "让我看看"再调工具，那句话该让糖糖立刻看见，不该等工具跑完。
        """
        # 局部变量，不是 contextvar —— 生成器帧天然按调用隔离，并发不会串，
        # 而 contextvar 跨 yield 在这里必炸（见 tools/context.py 开头）
        ctx = tool_context.ToolContext()
        for ev in self._stream_inner(
            user_text, ctx, system=system, dynamic_system=dynamic_system,
            history=history, images=images, split=split, adapter=adapter,
        ):
            # done 事件带上工具产生的附带产物（比如「要发的图片」）
            if ev.type == "done":
                result = getattr(ev, "result", None)
                if result is not None:
                    result.attachments = ctx.attachments
            yield ev

    def _stream_inner(
        self,
        user_text: str,
        ctx: tool_context.ToolContext,
        *,
        system: str | None = None,
        dynamic_system: str | None = None,
        history: list[Message] | None = None,
        images: list[str] | None = None,
        split: bool = True,
        adapter: LLMAdapter | None = None,
    ) -> Iterator[StreamEvent]:
        llm = adapter or self.adapter
        messages: list[Message] = list(history or [])
        messages.append(Message(role="user", text=user_text, images=images or []))

        specs = [t.spec for t in self.tools.values()]
        tracker = guard.FailureTracker(limit=self.failure_limit)
        total = Usage()
        # 两级过滤，顺序不能反：先剥 [mood:] 标记，再切 ||| 分段。
        # 反过来的话，万一情绪标记出现在段末，会被当成正文推出去。
        #
        # ⚠️ MoodTagFilter 只认 [mood: 前缀 —— ElevenLabs 的情绪标签
        # （[whining] [softly] 等）必须原样保留送到 TTS，不能被误伤。
        mood_filter = MoodTagFilter()
        # 语音模式关掉分段：||| 会被 TTS 当成正文念出来
        splitter = SegmentSplitter() if split else None

        def emit(raw: str) -> Iterator[StreamEvent]:
            safe = mood_filter.feed(raw)
            if not safe:
                return
            if splitter is None:
                yield StreamEvent("text", text=safe)
                return
            for kind, content in splitter.feed(safe):
                yield StreamEvent(kind, text=content)  # type: ignore[arg-type]

        def drain() -> Iterator[StreamEvent]:
            tail = mood_filter.flush()
            if tail:
                if splitter is None:
                    yield StreamEvent("text", text=tail)
                else:
                    for kind, content in splitter.feed(tail):
                        yield StreamEvent(kind, text=content)  # type: ignore[arg-type]
            if splitter is not None:
                rest = splitter.flush()
                if rest:
                    yield StreamEvent("text", text=rest)

        for iterations in range(1, self.max_iterations + 1):
            turn: Turn | None = None
            for ev in llm.stream(
                messages, specs,
                system=system, dynamic_system=dynamic_system, depth=self.depth,
            ):
                if ev.type == "text":
                    yield from emit(ev.text)
                else:
                    turn = ev.turn

            if turn is None:
                turn = Turn(stop_reason="error", error="流意外结束，没有收到 done 事件")
            _accumulate(total, turn.usage)

            if turn.stop_reason in ("error", "refusal", "max_tokens"):
                yield from drain()
                outcome = {
                    "error": "error", "refusal": "refused", "max_tokens": "truncated",
                }[turn.stop_reason]
                if turn.stop_reason == "max_tokens":
                    messages.append(_assistant_message(turn))
                yield _done(outcome, turn.text, iterations, total, messages, turn.error)
                return

            if turn.stop_reason == "end_turn" or not turn.tool_calls:
                yield from drain()
                messages.append(_assistant_message(turn))
                yield _done("answered", turn.text, iterations, total, messages)
                return

            messages.append(_assistant_message(turn))
            # 🔴 一件一件跑，每件前后各发一帧 —— **不要写回列表推导**。
            # 推导式会把这一批工具整个跑完才回到调用方，中间没有任何输出：
            # 语音通话那边就是十几秒的死寂，她分不清他在干活还是卡死了。
            # （非流式那条 `run()` 没有这个问题 —— 那边本来就是等全部做完才回。）
            outcomes = []
            for c in turn.tool_calls:
                yield StreamEvent("tool_start", tool=c.name)
                outcome = self._execute(c, tracker, ctx)
                outcomes.append(outcome)
                yield StreamEvent("tool_end", tool=c.name, ok=not outcome.failed)
            messages.append(
                Message(role="tool_results", tool_results=[o.result for o in outcomes])
            )

            stuck = tracker.exhausted()
            if stuck:
                yield _done(
                    "tool_stuck", turn.text, iterations, total, messages,
                    f"这些工具连续失败 {self.failure_limit} 次，已停止重试：{', '.join(stuck)}",
                )
                return
        else:
            yield _done(
                "exhausted", None, self.max_iterations, total, messages,
                f"达到循环上限 {self.max_iterations} 轮仍未结束",
            )

    def _execute(
        self,
        call: ToolCall,
        tracker: guard.FailureTracker,
        ctx: tool_context.ToolContext | None = None,
    ) -> guard.ToolOutcome:
        if "_parse_error" in call.arguments:
            outcome = guard.bad_arguments(call, str(call.arguments["_parse_error"]))
            tracker.record(outcome, call.name)
            return outcome

        tool = self.tools.get(call.name)
        if tool is None:
            outcome = guard.unknown_tool(call, list(self.tools))
            tracker.record(outcome, call.name)
            return outcome

        try:
            # 上下文只绑这一小段。set 和 reset 之间没有 yield，
            # 所以流式路径下也成立（见 tools/context.py 开头）
            if ctx is None:
                raw = tool.handler(call.arguments)
            else:
                with tool_context.bind(ctx):
                    raw = tool.handler(call.arguments)
            outcome = guard.ok(call, raw)
        except Exception as exc:  # noqa: BLE001 —— 工具的任何异常都要回给模型，不能吞
            logger.warning("工具 %s 执行失败: %s", call.name, exc, exc_info=True)
            outcome = guard.failure(call, exc)

        tracker.record(outcome, call.name)
        return outcome


def _assistant_message(turn: Turn) -> Message:
    return Message(role="assistant", text=turn.text, tool_calls=turn.tool_calls)


def _done(
    outcome: str,
    text: str | None,
    iterations: int,
    usage: Usage,
    messages: list[Message],
    detail: str | None = None,
) -> StreamEvent:
    """收尾事件。LoopResult 挂在 event 上，调用方拿它做统计和落库。"""
    ev = StreamEvent("done")
    ev.result = LoopResult(  # type: ignore[attr-defined]
        outcome=outcome,
        text=text,
        iterations=iterations,
        usage=usage,
        messages=messages,
        detail=detail,
    )
    return ev


def _accumulate(total: Usage, part: Usage) -> None:
    total.input_tokens += part.input_tokens
    total.output_tokens += part.output_tokens
    total.cache_read_tokens += part.cache_read_tokens
    total.cache_write_tokens += part.cache_write_tokens
