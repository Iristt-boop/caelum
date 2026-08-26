"""Guard —— 不许编。

先说清楚这个模块**不做**什么，因为那部分比它做什么更重要：

它不去语义比对"模型说的"和"工具返回的"是否一致。那需要再跑一次 LLM 来
判断，既慢又不可靠，而且判断错了会误杀正确回答 —— 用一个会编的东西去
检查另一个会不会编，逻辑上就站不住。

真正管用的是从源头断掉编造的动机：**让模型看见真实的失败**。
模型圆场，多数时候不是因为它想骗人，而是因为失败信息在中途被吞了 ——
被 try/except 捕获后转成一句"操作失败"，模型看到的是一句模糊的自然语言，
于是按"对话应该继续"的惯性把结果补圆。

所以这里只做三件确定性的事：
  1. 把工具失败原样打包（错误类型、消息、参数），不美化
  2. 区分"调用失败"和"调用成功但结果为空" —— 混在一起模型必编
  3. 提供一句给模型的硬约束，说明失败后该怎么办
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass

from agent.llm import ToolCall, ToolResult

# 注入 system prompt 的约束。放在人设之后、工具列表之前。
GUARD_INSTRUCTION = """\
工具调用失败时的规则（硬性）：
- 如果工具返回了错误，如实告诉糖糖哪一步失败了、失败原因是什么。
- 绝不要编造工具本该返回的内容，也不要假装调用成功了。
- 不确定就说不确定。参数写错了可以改参数重试一次；同一个工具连续失败两次，
  停下来说明情况，不要继续试。
- 「查不到结果」和「调用失败」是两件事，不要混为一谈：前者是有效答案，
  后者是故障。
"""


@dataclass
class ToolOutcome:
    """一次工具执行的结果，区分三种结局。"""

    result: ToolResult
    # 调用失败（异常、超时、参数非法）—— 是故障
    failed: bool = False
    # 调用成功但没查到东西 —— 是有效答案，不是故障
    empty: bool = False


def ok(call: ToolCall, content: str) -> ToolOutcome:
    """成功。空结果单独标记 —— 它是有效答案，不是失败。"""
    text = (content or "").strip()
    if not text:
        return ToolOutcome(
            result=ToolResult(
                call_id=call.id,
                # 明确说"没有结果"，而不是给一个空串让模型自己猜
                content=f"{call.name} 执行成功，但没有找到任何结果（不是错误）。",
                is_error=False,
            ),
            empty=True,
        )
    return ToolOutcome(result=ToolResult(call_id=call.id, content=text))


def failure(call: ToolCall, exc: BaseException, *, include_trace: bool = False) -> ToolOutcome:
    """失败。原样回传错误类型和消息，附上调用参数 —— 模型要靠这些自己纠正。"""
    detail = f"{type(exc).__name__}: {exc}"
    body = (
        f"工具 {call.name} 调用失败。\n"
        f"错误：{detail}\n"
        f"传入参数：{call.arguments}"
    )
    if include_trace:
        body += "\n" + "".join(traceback.format_exception(exc))[-1200:]
    return ToolOutcome(
        result=ToolResult(call_id=call.id, content=body, is_error=True),
        failed=True,
    )


def unknown_tool(call: ToolCall, available: list[str]) -> ToolOutcome:
    """模型叫了一个不存在的工具。把可用清单还给它，别只说"未知工具"。"""
    return ToolOutcome(
        result=ToolResult(
            call_id=call.id,
            content=(
                f"没有名为 {call.name} 的工具。\n"
                f"可用工具：{', '.join(available) if available else '（无）'}"
            ),
            is_error=True,
        ),
        failed=True,
    )


def bad_arguments(call: ToolCall, reason: str) -> ToolOutcome:
    """参数有问题（解析失败或校验不过）。说清哪里不对，模型才可能改对。"""
    return ToolOutcome(
        result=ToolResult(
            call_id=call.id,
            content=(
                f"工具 {call.name} 的参数有问题：{reason}\n"
                f"收到的参数：{call.arguments}"
            ),
            is_error=True,
        ),
        failed=True,
    )


class FailureTracker:
    """按工具名统计连续失败次数。

    存在的理由：同一个工具连着失败，多半是参数或依赖有问题，再试也是白试 ——
    烧钱、烧时间，还会把上下文塞满失败记录，反而更容易让模型开始瞎编。
    """

    def __init__(self, limit: int = 2) -> None:
        self.limit = limit
        self._streak: dict[str, int] = {}

    def record(self, outcome: ToolOutcome, tool_name: str) -> None:
        if outcome.failed:
            self._streak[tool_name] = self._streak.get(tool_name, 0) + 1
        else:
            self._streak.pop(tool_name, None)

    def exhausted(self) -> list[str]:
        return [name for name, n in self._streak.items() if n >= self.limit]
