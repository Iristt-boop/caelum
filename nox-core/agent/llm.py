"""LLM 适配层 —— loop 与具体厂商之间的唯一接口。

设计原则：**各家的差异全压在这一层，loop 一个字都不用改。**

loop 只认识下面三个数据类和一个 complete() 方法。它永远不知道
temperature、thinking、effort、budget_tokens 这些词的存在 —— 那些是
各家自己的方言，由对应 adapter 翻译。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# loop 能看到的停止原因。各家的原始值由 adapter 映射到这几个。
StopReason = Literal[
    "end_turn",    # 正常答完
    "tool_use",    # 要调工具
    "max_tokens",  # 截断了 —— 这不是完成
    "refusal",     # 模型拒绝回答（Claude 5 家族会返回 200 + 空 content）
    "error",       # 调用本身失败
]

Depth = Literal["low", "medium", "high"]


@dataclass
class ToolCall:
    """模型要调用的一个工具。"""

    id: str
    name: str
    # 必须是已解析的 dict。各家对工具参数的 JSON 转义方式不同（Unicode、
    # 正斜杠都可能被转义），解析责任在 adapter —— loop 拿到的永远是干净的 dict。
    # 绝不能对序列化后的字符串做匹配。
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """一个工具的执行结果。"""

    call_id: str
    content: str
    # 独立的布尔字段，不是塞在 content 里的一句"失败了"。
    # 整个"不许编"就靠这个字段立住：模型之所以圆场，往往是因为它只看到
    # 一句模糊的自然语言，而不是一个明确的错误信号。
    is_error: bool = False


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class Turn:
    """模型一轮的返回。"""

    stop_reason: StopReason
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    # stop_reason == "error" 时说明原因；其余情况为 None
    error: str | None = None


@dataclass
class ToolSpec:
    """给模型看的工具定义。各 adapter 转成自己的 schema 格式。"""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class Message:
    """中性消息 —— loop 维护的对话历史用这个格式，不是任何一家的原生格式。

    这一步不能省：两家的工具结果结构不同，不只是字段名不同。
      Anthropic     —— tool_result 塞在 user 消息的 content 数组里
      OpenAI 兼容   —— tool 结果是独立的 role="tool" 消息
    loop 若直接维护某一家的格式，就跟那一家绑死了。
    """

    role: Literal["user", "assistant", "tool_results"]
    text: str | None = None
    # role == "assistant" 且模型要调工具时填
    tool_calls: list[ToolCall] = field(default_factory=list)
    # role == "tool_results" 时填。一轮里的所有结果放同一条消息 ——
    # 拆成多条不会报错，但会慢慢训练模型不再并行调用工具。
    tool_results: list[ToolResult] = field(default_factory=list)
    # 图片，base64 或 data URI。两家的格式差得比文本大：
    #   Anthropic     {"type":"image","source":{"type":"base64","media_type":...,"data":...}}
    #   OpenAI 兼容   {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}
    # 所以这里只存裸数据，由各 adapter 自己包装。
    images: list[str] = field(default_factory=list)


@dataclass
class StreamEvent:
    """流式事件。

    只有两种：文本增量、这一轮结束。工具调用不单独发事件 ——
    调用方拿到 done 事件里的 Turn 就知道要不要调工具了，
    中途 yield 一个半成品的 tool_call 没有意义（参数还没拼完）。
    """

    type: Literal["text", "split", "done"]
    text: str = ""
    turn: Turn | None = None


class LLMAdapter(Protocol):
    """loop 唯一依赖的接口。加一个新厂商 = 写一个新 adapter，loop 不动。"""

    name: str

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        system: str | None = None,
        dynamic_system: str | None = None,
        depth: Depth | None = None,
        max_tokens: int | None = None,
    ) -> Turn:
        """system 是静态前缀（打缓存断点），dynamic_system 每轮可变。

        分开传是为了缓存：前者按字节匹配前缀、一变全废，后者跟在断点之后
        每轮新发。情绪状态、当下场景这类东西必须走 dynamic_system。
        """
        ...

    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        system: str | None = None,
        dynamic_system: str | None = None,
        depth: Depth | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[StreamEvent]:
        """同 complete，但边生成边吐。最后一个事件必定是 done。"""
        ...


class MoodTagFilter:
    """挡住流式输出里的 [mood:xxx] 标记。

    非流式时可以等文本收全了统一剥掉，流式下不行 —— 标记会一个字一个字
    出现在糖糖屏幕上。

    做法：见到 '[' 就转入缓冲，不再往外吐；等能确定它不是 mood 标记了，
    把缓冲原样放出去。代价是正文里出现方括号时会延迟几个字符才显示，
    这比让她看见 [mood:平静] 强得多。

    ⚠️ 大小写不敏感。模型偶尔手滑写 [Mood:开心] —— 生产库里漏过 3 次
    全是大写变体（2026-09-06 查证）。比较时 lower，放行时吐原始文本。
    """

    _PREFIX = "[mood:"

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> str:
        """吃进增量，吐出可以安全显示的部分。"""
        out: list[str] = []
        for ch in chunk:
            if self._buf:
                self._buf += ch
                lowered = self._buf.lower()
                if lowered.startswith(self._PREFIX):
                    # 确认是标记，吃掉直到闭合
                    if ch == "]":
                        self._buf = ""
                    continue
                if self._PREFIX.startswith(lowered):
                    continue          # 还可能是，继续缓冲
                out.append(self._buf)  # 确认不是，放行（原始大小写）
                self._buf = ""
            elif ch == "[":
                self._buf = ch
            else:
                out.append(ch)
        return "".join(out)

    def flush(self) -> str:
        """流结束时把剩下的放出去（除非它正好是个没闭合的标记）。"""
        left = self._buf
        self._buf = ""
        return "" if left.lower().startswith(self._PREFIX) else left


class SegmentSplitter:
    """把回复切成多个气泡。

    切点由**模型自己标**（用 ||| 分隔），不是机械按标点切 —— 他知道
    哪句该单独成一条，而规则猜不出来。"今天好累" 和 "但是看到你就好了"
    该分开发，"我去看看，稍等" 不该在逗号处断开。

    标记会被 chunk 切成两半（流式下这是常态），所以结尾的 | 或 ||
    要先扣着，等下一片到了再判断。
    """

    MARK = "|||"

    def __init__(self) -> None:
        self._carry = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """返回 [(事件类型, 内容)]，类型是 text 或 split。"""
        self._carry += chunk
        out: list[tuple[str, str]] = []

        while True:
            idx = self._carry.find(self.MARK)
            if idx == -1:
                break
            head = self._carry[:idx]
            if head:
                out.append(("text", head))
            out.append(("split", ""))
            self._carry = self._carry[idx + len(self.MARK) :]

        # 结尾若是半个标记（| 或 ||），扣下来等下一片
        keep = 0
        for n in (2, 1):
            if self._carry.endswith("|" * n):
                keep = n
                break
        if keep:
            safe, self._carry = self._carry[:-keep], self._carry[-keep:]
        else:
            safe, self._carry = self._carry, ""

        if safe:
            out.append(("text", safe))
        return out

    def flush(self) -> str:
        """收尾。扣着的半个标记如果最后没等到后续，原样放出去。"""
        left = self._carry
        self._carry = ""
        return left


def split_data_uri(raw: str) -> tuple[str, str]:
    """把图片拆成 (media_type, base64 裸数据)。

    前端传过来的可能是 `data:image/jpeg;base64,xxx`，也可能是光秃秃的
    base64。Anthropic 要求分开给 media_type 和 data，OpenAI 要求拼成
    完整的 data URI —— 所以这里统一拆开，谁要用谁自己拼。

    认不出类型时按 jpeg 处理：手机拍的照片绝大多数是 jpeg，
    而猜错 media_type 只会让模型看不清，不会报错。
    """
    if raw.startswith("data:"):
        head, _, data = raw.partition(",")
        media = head[5:].split(";")[0] or "image/jpeg"
        return media, data
    return "image/jpeg", raw


def parse_arguments(raw: Any, *, tool_name: str) -> dict[str, Any]:
    """把工具参数统一解析成 dict。

    有的后端给 dict，有的给 JSON 字符串。解析失败不抛异常 —— 交回一个
    带 _parse_error 的 dict，让 loop 走"工具失败"这条正常路径回给模型，
    模型看到真实错误才可能自己纠正参数。抛异常会把这条路径变成崩溃。
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return {"_parse_error": f"{tool_name} 的参数不是合法 JSON: {exc}"}
        if isinstance(parsed, dict):
            return parsed
        return {"_parse_error": f"{tool_name} 的参数解析后不是对象，而是 {type(parsed).__name__}"}
    return {"_parse_error": f"{tool_name} 的参数类型异常: {type(raw).__name__}"}
