"""Agent Loop —— 核心推理循环。

模型无关：只跟 LLMAdapter 说话，不认识任何一家的参数名。

五个关键设计，每个都对应一个具体的坑：
  1. 硬上限 + 区分结局 —— 跑满循环和答完是两回事，混在一起就永远不知道
     他是答完了还是在原地打转
  2. refusal / max_tokens 单独处理 —— max_tokens 是截断不是完成，当成完成
     处理就会把半截答案当最终回复；refusal 时 content 可能是空的
  3. 所有 tool_result 装进同一条消息 —— 拆开不报错，但会让模型逐渐放弃
     并行调用工具
  4. 工具失败原样回传 —— 见 guard.py 开头那段
  5. **墙钟 deadline**（2026-09-12 加，审计 1.1）—— `max_iterations` 数的是
     轮数，数不了时间。一个每轮都慢、每轮都不报错的上游能把 12 轮拖成几十
     分钟，而糖糖那边只看到"一直在转"。deadline 让"慢"变成一个**有下场的
     结局**（`timeout`），而不是一段无声的等待。
  6. **每轮一行 turn 日志**（2026-09-13 加，审计 1.2）—— 见文件末尾 `log_turn`。

## 日志（审计 1.2）

在这之前整个 loop 只有 `_execute` 里的两行 warning：工具抛异常、闸门拦下。
也就是说 —— **只有出事了才有日志，正常跑完一个字都不留。**
于是"他今天怪怪的"这类问题没有任何可查的东西：他调了什么、跑了几轮、
花了多久、用的哪个后端，全部只存在于那一瞬间。

现在每轮结束落一行，**出口只有两个**（`run` / `run_stream`），
所以不管从哪个 `return` 出去都跑得到。这不是巧合，是故意只在这两处记 ——
`_run_inner` 有 8 个返回点，在每个点上手写日志，迟早会有人加第 9 个时忘掉。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from agent import guard
from tools import context as tool_context
from agent.llm import (
    GATED_EFFECTS,
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
      timeout       超过这一轮的墙钟预算，主动收尾
      error         调用链本身出错

    ⚠️ `timeout` 和 `exhausted` 是两回事，别合并：
    `exhausted` 是**他在打转**（轮数用光），`timeout` 是**上游在拖**（时间用光）。
    一个该去看提示词，一个该去看 provider —— 合成一个就等于把这两条线索都丢了。
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
    #: 这一轮里**写过**的状态 → 管着它的 Context Provider 名字。
    #: 由工具经 `ToolContext.wrote()` 登记；调用方（`nox.py`）在轮次结束时
    #: 拿它去 `registry.invalidate()`，否则下一轮递给他的还是写之前那份快照。
    #: 见 `tools/context.py` 里 `wrote()` 的完整说明。
    dirty_providers: list[str] = field(default_factory=list)

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
    #: 一整轮的墙钟预算（秒）。None / ≤0 = 不限（默认，保持老调用方的行为不变）。
    #: 由 `nox.py` 从 `config.chat_deadline_s` 传进来。
    deadline_s: float | None = None

    def _deadline(self) -> float | None:
        """算出这一轮的截止时刻。用 monotonic —— 系统时钟被改也不受影响。"""
        if not self.deadline_s or self.deadline_s <= 0:
            return None
        return time.monotonic() + self.deadline_s

    def _overdue(self, deadline: float | None) -> bool:
        return deadline is not None and time.monotonic() >= deadline

    @staticmethod
    def _confirmed(ctx: tool_context.ToolContext | None, call: ToolCall) -> bool:
        """糖糖这一轮点过头没有。

        ⚠️ 只认 `ctx.confirmed` 这个集合，**不认参数里带的任何字段**。
        模型能写出 `{"confirmed": true}`，所以"确认"绝不能是模型自己填的东西。
        """
        return ctx is not None and call.name in ctx.confirmed

    def _timeout_detail(self, iterations: int) -> str:
        return (
            f"这一轮超过 {self.deadline_s:.0f} 秒的预算，在第 {iterations} 轮收尾。"
            "通常是上游变慢，不是他在打转"
        )

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        """登记一个工具。**没声明副作用的一律拒收。**

        🔴 这里抛异常而不是给个默认值，是整条闸门的地基（审计 3.1）：
        如果未声明默认成 `read`，那么"忘了标"和"确实只读"在代码里长得一模一样，
        闸门就变成了一个**只能挡住老实人**的东西 —— 而注入和幻觉都不老实。

        代价是新加工具时会在启动阶段炸。那正是我们要的：
        **炸在启动，好过某天悄悄下了一单。**
        """
        if spec.side_effect is None:
            raise ValueError(
                f"工具 {spec.name!r} 没有声明 side_effect。"
                f"必须从 {', '.join(sorted(GATED_EFFECTS | {'none', 'read', 'write'}))} 里选一个。"
                "拿不准就往重了标 —— 标轻了模型能直接碰，标重了只是多一次确认。"
            )
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
        session_id: str | None = None,
    ) -> LoopResult:
        # 上下文当局部变量持有，不用 contextvar 包整轮 ——
        # 那样在流式路径下会炸（见 tools/context.py 开头）
        ctx = tool_context.ToolContext(session_id=session_id, user_text=user_text)
        # 日志用的两个基准，必须在进 _run_inner 之前取（见 _tools_used）
        started = time.monotonic()
        base = len(history or [])
        result = self._run_inner(
            user_text, ctx, system=system, dynamic_system=dynamic_system,
            history=history, images=images, adapter=adapter,
        )
        result.attachments = ctx.attachments
        result.dirty_providers = sorted(ctx.dirty)
        log_turn(
            result,
            model=adapter_name(adapter or self.adapter),
            elapsed_s=time.monotonic() - started,
            history_len=base,
            stream=False,
        )
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
        deadline = self._deadline()

        for iterations in range(1, self.max_iterations + 1):
            # ⚠️ 检查点在**开下一轮之前**，不是在调用中间 ——
            # 正在进行的 SDK 调用（它自己带重试）这里插不进去。
            # 所以这个 deadline 保证的是"不会再往下滚"，不是"180 秒必回"。
            if self._overdue(deadline):
                return LoopResult(
                    "timeout", None, iterations - 1, total, messages,
                    self._timeout_detail(iterations - 1),
                )

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
        session_id: str | None = None,
    ) -> Iterator[StreamEvent]:
        """流式版的 run。

        文本增量实时吐出，工具调用照旧在内部跑完。最后一个事件是 done，
        里面带完整的 LoopResult（挂在 turn 上传不方便，所以用属性传）。

        注意：**要调工具的那一轮，文本也会先流出来**。模型常常先说
        "让我看看"再调工具，那句话该让糖糖立刻看见，不该等工具跑完。
        """
        # 局部变量，不是 contextvar —— 生成器帧天然按调用隔离，并发不会串，
        # 而 contextvar 跨 yield 在这里必炸（见 tools/context.py 开头）
        ctx = tool_context.ToolContext(session_id=session_id, user_text=user_text)
        started = time.monotonic()
        base = len(history or [])
        model = adapter_name(adapter or self.adapter)
        finished = False
        try:
            for ev in self._stream_inner(
                user_text, ctx, system=system, dynamic_system=dynamic_system,
                history=history, images=images, split=split, adapter=adapter,
            ):
                # done 事件带上工具产生的附带产物（比如「要发的图片」），
                # 以及「这一轮写过哪些状态」—— 后者由调用方拿去清 Provider 缓存
                if ev.type == "done":
                    result = getattr(ev, "result", None)
                    if result is not None:
                        result.attachments = ctx.attachments
                        result.dirty_providers = sorted(ctx.dirty)
                        finished = True
                        log_turn(
                            result, model=model,
                            elapsed_s=time.monotonic() - started,
                            history_len=base, stream=True,
                        )
                yield ev
        finally:
            # 🔴 **走不到 done 的那条路也要留痕。**
            #
            # 生成器被调用方提前丢掉（她关掉页面、SSE 断开、上游抛异常穿过去）
            # 时，上面那个 `if ev.type == "done"` 一次都不会执行 ——
            # 于是最该查的那类轮次反而是日志里唯一的空白。
            # 这正是「连接抖一下就断」那个坑（api/server.py 的 ws_ping 注释）
            # 当初查了两天的原因：**断掉的会话不留任何记录。**
            #
            # ⚠️ 这里在 GeneratorExit 期间运行，所以只许打日志，不许 yield。
            if not finished:
                logger.warning(
                    "turn 中断 | outcome=abandoned stream=1 model=%s t=%.1fs "
                    "（调用方没取到 done 就丢掉了这条流：断线 / 客户端关闭 / 上游异常）",
                    model, time.monotonic() - started,
                )

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

        deadline = self._deadline()

        for iterations in range(1, self.max_iterations + 1):
            # 同非流式那条：只拦"开下一轮"，拦不住正在进行的那一次调用。
            # 流式这边额外要紧的是 —— 超时前已经吐出去的文本**不能吞掉**，
            # 所以先 drain 再报 timeout，否则她屏幕上会留半句没有下文的话。
            if self._overdue(deadline):
                yield from drain()
                yield _done(
                    "timeout", None, iterations - 1, total, messages,
                    self._timeout_detail(iterations - 1),
                )
                return

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

        # 🔴 花钱的、撤不回来的，模型不许直接碰（审计 3.2，边界法则 R8）。
        #
        # 这一层拦的不是"模型会不会想干坏事"，而是**它没有能力分辨自己是不是
        # 被骗了**：搜索结果里的一句注入、一次幻觉，跟糖糖真的说"帮我点一杯"
        # 在模型眼里长得一样。所以判断权不能留在模型那一侧。
        #
        # 正确的路是瑞幸那条：工具只负责**出卡**（preview），真正下单走
        # `/api/nox/orders/{id}/confirm` —— 她点头才发生（`api/server.py:1609`）。
        #
        # ⚠️ 这里只认「有没有令牌」，不认工具描述里写了什么。
        # 描述是提示词，提示词是可以被绕过的；令牌是结构。
        if tool.spec.needs_gate and not self._confirmed(ctx, call):
            logger.warning(
                "拦下未确认的 %s 工具: %s", tool.spec.side_effect, call.name,
            )
            outcome = guard.failure(call, PermissionError(
                f"{call.name} 会{'花钱' if tool.spec.side_effect == 'spend' else '产生撤不回来的后果'}，"
                "不能直接调用。先把方案出成一张卡给糖糖看，她点头之后走确认端点。"
            ))
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


def adapter_name(adapter: LLMAdapter) -> str:
    """这一轮是谁答的，形如 `openai_compat:glm-5.3`。

    🔴 **两段都要**，这是上线当天实测出来的（2026-09-13）：
    第一版只打 `adapter.name`，线上看到的是 `model=openai_compat` ——
    那是**传输层**，不是模型。而糖糖在前端随时换模型，
    "他今天怪怪的"里有一大半就是"这轮是哪个模型答的"。

    反过来只打模型也不行：`anthropic` 原生和 `openai_compat` 走的是两条
    代码路径（`depth` 只在前者生效，见 `adapters.py`），那个区别咬过人。

    ⚠️ **拿不到也不许炸** —— 日志不值得把一轮对话搞挂。协议里写了
    `name: str`，但协议是给类型检查看的，运行时谁都能塞个鸭子类型进来。
    """
    name = str(getattr(adapter, "name", None) or "?")
    model = getattr(getattr(adapter, "cfg", None), "model", None)
    return f"{name}:{model}" if model else name


#: turn 日志里最多列几个工具调用。超了截断并标出还有多少 ——
#: 一轮里调 40 次工具本身就是个信号，但不该让一行日志变成一屏
_TOOLS_IN_LOG = 20


def _tools_used(messages: list[Message], history_len: int) -> list[str]:
    """这一轮**他自己**调了哪些工具，按调用顺序，失败的打 ✗。

    ⚠️ `messages` 的前 `history_len` 条是**传进来的历史**，里面带着
    以前几轮的 `tool_calls`。不跳过它们的话，聊得越久这行日志越长，
    而且会把上一轮的工具算到这一轮头上 —— 那种错比没日志更坏。

    故意**不去重**：同一个工具连着出现三次，正是"他在打转"的样子，
    压成一个就把唯一的线索压没了。
    """
    failed: set[str] = set()
    for msg in messages[history_len:]:
        for res in msg.tool_results:
            if res.is_error:
                failed.add(res.call_id)

    names: list[str] = []
    for msg in messages[history_len:]:
        for call in msg.tool_calls:
            names.append(f"{call.name}✗" if call.id in failed else call.name)
    return names


def log_turn(
    result: LoopResult,
    *,
    model: str,
    elapsed_s: float,
    history_len: int,
    stream: bool,
    path: str = "full",
) -> None:
    """一轮一行。**这是"他今天怪怪的"唯一能查的东西**（审计 1.2）。

    要能回答四个问题，所以四组字段缺一不可：

        他跑完了吗   outcome / iter
        他慢在哪     t（墙钟）、对照 iter 就知道是轮数多还是单轮慢
        他干了什么   tools（按顺序，失败打 ✗）
        花了多少     tok

    `answered` 走 INFO，其余全部 WARNING —— 因为其余每一种都是
    "她那边收到的东西不完整"：截断、拒答、超时、打转、工具卡住。
    这些不该跟正常轮次混在同一个级别里等人去筛。

    `path=light` 是 Router 那条便宜路（`router/router.py`）——
    它不走 loop，所以 `iter=1 tools=-` 是常态，不是异常。
    **`stream` 和 `path` 是两个轴，别合并**：一个说传输，一个说路由。
    """
    tools = _tools_used(result.messages, history_len)
    shown = ",".join(tools[:_TOOLS_IN_LOG])
    if len(tools) > _TOOLS_IN_LOG:
        shown = f"{shown},…+{len(tools) - _TOOLS_IN_LOG}"

    u = result.usage
    line = (
        "turn | outcome=%s path=%s iter=%d t=%.1fs model=%s stream=%d "
        "tools=%s tok=in%d/out%d/cr%d/cw%d"
    )
    args = (
        result.outcome, path, result.iterations, elapsed_s, model, int(stream),
        shown or "-",
        u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens,
    )
    if result.detail:
        line += " | %s"
        args += (result.detail,)

    logger.log(logging.INFO if result.ok else logging.WARNING, line, *args)


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
