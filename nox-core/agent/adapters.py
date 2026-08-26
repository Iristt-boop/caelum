"""两个 LLM adapter。各家的方言全在这里翻译，loop 看不见。

新增一家 = 在这里加一个类 + 在 make_adapter 里加一行，loop.py 不动。
"""

from __future__ import annotations

from typing import Any

from config import LLMConfig
from collections.abc import Iterator

from agent.llm import (
    Depth,
    LLMAdapter,
    Message,
    StopReason,
    StreamEvent,
    ToolCall,
    ToolSpec,
    Turn,
    Usage,
    parse_arguments,
    split_data_uri,
)

# ---------------------------------------------------------------- Anthropic

# depth 是中性词。Claude 5 家族用 output_config.effort 表达深度，
# 并且**拒绝 temperature/top_p/top_k** —— 传了直接 400，不是忽略。
_ANTHROPIC_EFFORT: dict[Depth, str] = {"low": "low", "medium": "medium", "high": "high"}

_ANTHROPIC_STOP: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
    "stop_sequence": "end_turn",
    # pause_turn 只在用 Anthropic 托管的服务端工具时出现（我们没用）。
    # 映射成 end_turn 而不是继续循环 —— 真遇上了宁可提前收尾，也不要空转。
    "pause_turn": "end_turn",
}


class AnthropicAdapter:
    """原生 Anthropic API。

    Claude 5 家族的几个硬约束（都是 400 级别的，不是风格问题）：
      - temperature / top_p / top_k 一律不能传
      - 旧的 thinking budget_tokens 已移除，深度用 output_config.effort
      - 拒答时返回 200 + stop_reason="refusal"，content 可能为空 ——
        必须先判断 stop_reason 再读 content，否则会崩在空数组上
    """

    name = "anthropic"

    def __init__(self, cfg: LLMConfig) -> None:
        import anthropic  # 延迟导入：没配这家就不需要装这个包

        self.cfg = cfg
        # SDK 自带指数退避重试，且会读 retry-after 头 —— 比自己写一层可靠。
        # 它只重试该重试的（连接失败 / 429 / 5xx），400、401 这类不会白重试。
        self._client = anthropic.Anthropic(
            api_key=cfg.api_key,
            max_retries=cfg.max_retries,
            timeout=cfg.timeout,
        )

    def _build_kwargs(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        system: str | None,
        dynamic_system: str | None,
        depth: Depth | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        """complete 和 stream 共用的请求构造。

        抽出来是因为两条路径的参数必须完全一致 —— 分成两份写，
        以后改缓存策略或思考配置时必定漏掉一处。
        """
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "max_tokens": max_tokens or self.cfg.max_tokens,
            "messages": [self._to_native(m) for m in messages],
            # 自适应思考。注意 max_tokens 是"思考 + 回复"的总额度，
            # 不是只给回复的 —— 给太紧会在回答中途截断。
            "thinking": {"type": "adaptive"},
        }
        if system:
            # 缓存断点打在 system 末尾。请求的前缀顺序是 tools → system →
            # messages，所以这一个断点会把工具定义和 system 一起缓存住。
            #
            # 用 1h 而不是默认的 5m：糖糖的聊天是断续的，说几句去忙别的，
            # 隔半小时再回来。5m 那档会每次都过期重写（1.25x），比不缓存
            # 还贵；1h 写入贵一倍但之后一小时内都是 0.1x。
            #
            # 前提是这段文字**一个字都不能变** —— 缓存按字节匹配前缀。
            # 所以核心准则取一次就冻住，绝不每轮重取。
            blocks: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ]
            # 动态块跟在缓存断点**之后** —— 它每轮都变（情绪、场景），
            # 放在断点前会让整段缓存作废。放在后面则前缀照常命中，
            # 只有这几十个字符按新内容计费。
            if dynamic_system:
                blocks.append({"type": "text", "text": dynamic_system})
            kwargs["system"] = blocks
        elif dynamic_system:
            kwargs["system"] = dynamic_system
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        if depth:
            kwargs["output_config"] = {"effort": _ANTHROPIC_EFFORT[depth]}
        return kwargs

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
        kwargs = self._build_kwargs(
            messages, tools, system=system, dynamic_system=dynamic_system,
            depth=depth, max_tokens=max_tokens,
        )
        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — 任何失败都要变成 Turn，不能炸穿 loop
            return Turn(stop_reason="error", error=f"{type(exc).__name__}: {exc}")
        return self._to_turn(resp)

    def _to_turn(self, resp: Any) -> Turn:
        stop = _ANTHROPIC_STOP.get(resp.stop_reason or "", "end_turn")

        # 拒答：content 可能是空数组，此时不要去读 content[0]
        if stop == "refusal":
            detail = getattr(resp, "stop_details", None)
            reason = getattr(detail, "category", None) if detail else None
            return Turn(
                stop_reason="refusal",
                error=f"模型拒绝回答（类别：{reason or '未标注'}）",
                usage=self._usage(resp),
            )

        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=parse_arguments(block.input, tool_name=block.name),
                    )
                )
            # thinking 块不进对话文本 —— 那是模型的草稿纸，不是给糖糖看的

        return Turn(
            stop_reason=stop,
            text="\n".join(texts).strip() or None,
            tool_calls=calls,
            usage=self._usage(resp),
        )

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
        kwargs = self._build_kwargs(
            messages, tools, system=system, dynamic_system=dynamic_system,
            depth=depth, max_tokens=max_tokens,
        )
        try:
            with self._client.messages.stream(**kwargs) as s:
                for text in s.text_stream:
                    yield StreamEvent("text", text=text)
                final = s.get_final_message()
        except Exception as exc:  # noqa: BLE001
            yield StreamEvent(
                "done",
                turn=Turn(stop_reason="error", error=f"{type(exc).__name__}: {exc}"),
            )
            return

        yield StreamEvent("done", turn=self._to_turn(final))

    @staticmethod
    def _usage(resp: Any) -> Usage:
        u = getattr(resp, "usage", None)
        if not u:
            return Usage()
        return Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )

    @staticmethod
    def _to_native(m: Message) -> dict[str, Any]:
        if m.role == "tool_results":
            # 所有结果装进同一条 user 消息。拆开不报错，但会让模型逐渐放弃并行调用。
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call_id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in m.tool_results
                ],
            }
        if m.role == "assistant" and m.tool_calls:
            content: list[dict[str, Any]] = []
            if m.text:
                content.append({"type": "text", "text": m.text})
            content.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            )
            return {"role": "assistant", "content": content}
        if m.images:
            # 图片放在文本**前面** —— Anthropic 官方建议如此，
            # 模型先看图再读问题，理解得更准
            blocks: list[dict[str, Any]] = []
            for img in m.images:
                media, data = split_data_uri(img)
                blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media, "data": data},
                })
            if m.text:
                blocks.append({"type": "text", "text": m.text})
            return {"role": m.role, "content": blocks}
        return {"role": m.role, "content": m.text or ""}


# ------------------------------------------------------------ OpenAI 兼容

# ⚠️ 不要把 depth 映射成 temperature —— 那是两个概念：
#   depth (effort)  思考多深 —— 影响推理过程的长度与成本
#   temperature     输出多随机 —— 影响措辞，与思考深浅无关
# 曾经写成 {"low": 0.0, ...}，结果一把 depth 调低，Nox 就变得死板，
# 同一句话每次答得一模一样。省了思考，也省掉了活人味。
#
# 这一侧没有 effort 概念，所以 depth 在这里**不生效**（只在 Anthropic 原生
# adapter 上有意义）。温度固定在适合聊天的值，让措辞自然。
_CHAT_TEMPERATURE = 0.8

_OPENAI_STOP: dict[str, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class OpenAICompatAdapter:
    """DeepSeek / OpenRouter 等 OpenAI 兼容接口。"""

    name = "openai_compat"

    def __init__(self, cfg: LLMConfig) -> None:
        from openai import OpenAI  # 延迟导入

        self.cfg = cfg
        self._client = OpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url or None,
            max_retries=cfg.max_retries,
            timeout=cfg.timeout,
        )
        # OpenRouter 支持把 cache_control 透传给 Anthropic/Gemini 后端，
        # 但只有 5 分钟档 —— 没有 Anthropic 原生的 1h TTL。
        # 对断续聊天来说 5 分钟经常等于没缓存，这是走 OpenRouter 的代价。
        self._supports_cache = "openrouter" in (cfg.base_url or "").lower()

    def _system_message(self, system: str, dynamic: str | None = None) -> dict[str, Any]:
        if not self._supports_cache:
            text = f"{system}\n\n{dynamic}" if dynamic else system
            return {"role": "system", "content": text}
        # 数组形式才能带 cache_control；OpenRouter 会翻译成后端的原生格式。
        # 动态块放在打了断点的静态块**之后**，这样前缀照常命中缓存。
        blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if dynamic:
            blocks.append({"type": "text", "text": dynamic})
        return {"role": "system", "content": blocks}

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
        native: list[dict[str, Any]] = []
        if system:
            native.append(self._system_message(system, dynamic_system))
        elif dynamic_system:
            native.append({"role": "system", "content": dynamic_system})
        for m in messages:
            native.extend(self._to_native(m))

        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": native,
            "max_tokens": max_tokens or self.cfg.max_tokens,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                        # DeepSeek V4 在 40+ 工具时容易把 tool call 当文本吐出来，
                        # strict=true 让它严肃对待工具调用，减少"我没有这个工具"式的幻觉
                        "strict": True,
                    },
                }
                for t in tools
            ]
        # depth 在这一侧无对应概念，忽略（见上方注释）。
        # 温度固定，保证措辞自然 —— 这是聊天，不是抽取结构化数据。
        kwargs["temperature"] = _CHAT_TEMPERATURE

        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            return Turn(stop_reason="error", error=f"{type(exc).__name__}: {exc}")

        choice = resp.choices[0]
        stop = _OPENAI_STOP.get(choice.finish_reason or "", "end_turn")

        calls: list[ToolCall] = []
        for tc in choice.message.tool_calls or []:
            # 这一侧的 arguments 是 JSON **字符串**，必须解析。
            calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=parse_arguments(tc.function.arguments, tool_name=tc.function.name),
                )
            )

        usage = self._usage(resp)

        return Turn(
            stop_reason=stop,
            text=(choice.message.content or "").strip() or None,
            tool_calls=calls,
            usage=usage,
        )

    @staticmethod
    def _usage(resp: Any) -> Usage:
        """解析 usage。

        缓存数据藏在 prompt_tokens_details 里，顶层的 prompt_tokens 是**总数**
        （已含缓存命中部分）。只读顶层会让缓存看起来完全没生效 —— 实测
        同一请求第二次 cost 从 $0.0542 掉到 $0.0044，钱早省了，只是没统计上。

        字段名各家不同，这里把见过的都试一遍：
          cached_tokens             OpenRouter / OpenAI（在 prompt_tokens_details 里）
          cache_read_tokens         部分兼容层
          cache_write_tokens        OpenRouter 写入缓存时
          prompt_cache_hit_tokens   **DeepSeek，在 usage 顶层，不在 details 里**

        ⚠️ DeepSeek 没有"写入缓存"的概念，只有命中/未命中，
        所以它的 cache_write 恒为 0 —— 那是正常的，不是没统计上。
        """
        u = getattr(resp, "usage", None)
        if not u:
            return Usage()

        detail = getattr(u, "prompt_tokens_details", None)

        def pick(*names: str) -> int:
            for src in (detail, u):
                if src is None:
                    continue
                for n in names:
                    v = getattr(src, n, None)
                    if v is None and isinstance(src, dict):
                        v = src.get(n)
                    if v:
                        return int(v)
            return 0

        return Usage(
            input_tokens=getattr(u, "prompt_tokens", 0) or 0,
            output_tokens=getattr(u, "completion_tokens", 0) or 0,
            cache_read_tokens=pick(
                "cached_tokens", "cache_read_tokens", "cache_read_input_tokens",
                "prompt_cache_hit_tokens",   # DeepSeek
            ),
            cache_write_tokens=pick("cache_write_tokens", "cache_creation_input_tokens"),
        )

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
        native: list[dict[str, Any]] = []
        if system:
            native.append(self._system_message(system, dynamic_system))
        elif dynamic_system:
            native.append({"role": "system", "content": dynamic_system})
        for m in messages:
            native.extend(self._to_native(m))

        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": native,
            "max_tokens": max_tokens or self.cfg.max_tokens,
            "temperature": _CHAT_TEMPERATURE,
            "stream": True,
            # 要 usage 必须显式开，否则流式下拿不到 token 数
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                        # DeepSeek V4 在 40+ 工具时容易把 tool call 当文本吐出来，
                        # strict=true 让它严肃对待工具调用，减少"我没有这个工具"式的幻觉
                        "strict": True,
                    },
                }
                for t in tools
            ]

        texts: list[str] = []
        # 工具调用的参数是**分片到达**的，要按 index 拼起来才是完整 JSON
        partial: dict[int, dict[str, Any]] = {}
        finish = ""
        usage = Usage()

        try:
            for chunk in self._client.chat.completions.create(**kwargs):
                if getattr(chunk, "usage", None):
                    # 复用非流式那套解析 —— 缓存命中藏在 prompt_tokens_details 里，
                    # 字段名各家不同，那边已经把见过的都试过一遍了
                    usage = self._usage(chunk)
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish = choice.finish_reason

                delta = choice.delta
                if getattr(delta, "content", None):
                    texts.append(delta.content)
                    yield StreamEvent("text", text=delta.content)

                for tc in getattr(delta, "tool_calls", None) or []:
                    slot = partial.setdefault(
                        tc.index, {"id": "", "name": "", "args": ""}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments
        except Exception as exc:  # noqa: BLE001
            yield StreamEvent(
                "done",
                turn=Turn(stop_reason="error", error=f"{type(exc).__name__}: {exc}"),
            )
            return

        calls = [
            ToolCall(
                id=s["id"],
                name=s["name"],
                arguments=parse_arguments(s["args"], tool_name=s["name"]),
            )
            for _, s in sorted(partial.items())
            if s["name"]
        ]

        yield StreamEvent(
            "done",
            turn=Turn(
                stop_reason=_OPENAI_STOP.get(finish, "end_turn"),
                text="".join(texts).strip() or None,
                tool_calls=calls,
                usage=usage,
            ),
        )

    def _to_native(self, m: Message) -> list[dict[str, Any]]:
        if m.role == "tool_results":
            # 与 Anthropic 相反：这一侧每个结果是独立的 role="tool" 消息。
            # 没有 is_error 字段，所以把失败标记写进内容里 —— 否则模型看不出
            # 这是失败还是正常返回，就会开始圆场。
            out: list[dict[str, Any]] = []
            for r in m.tool_results:
                body = f"[ERROR] {r.content}" if r.is_error else r.content
                out.append({"role": "tool", "tool_call_id": r.call_id, "content": body})
            return out
        if m.role == "assistant" and m.tool_calls:
            return [
                {
                    "role": "assistant",
                    "content": m.text or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": _dump(c.arguments)},
                        }
                        for c in m.tool_calls
                    ],
                }
            ]
        if m.images:
            if not supports_vision(self.cfg.model):
                # 这个模型看不了图，把图降级成一句文字。
                #
                # ⚠️ 关键在于**历史里的图也会走到这里**：发过一次图之后，那条消息
                # 留在会话历史里，之后每一轮都会被重新发出去、每一轮都被拒成 400 ——
                # 整个会话就此报废，连纯文字都发不出去。
                # （2026-08-02 实测：糖糖发完 logo，下一句纯文字照样 400。）
                # 降级成文字之后，老会话也能自己救回来。
                #
                # 说清楚「没看到」而不是假装看到 —— 失败必须可见，否则他会开始编。
                note = (
                    f"[她发来 {len(m.images)} 张图片。你现在用的模型看不了图，"
                    f"你没有看到图的内容。如实告诉她，并告诉她可以在输入框旁边的"
                    f"模型下拉里切到 Sonnet 5 / Opus，那些能看图]"
                )
                text = f"{m.text}\n{note}" if m.text else note
                return [{"role": m.role, "content": text}]
            # 这一侧要拼成完整的 data URI，跟 Anthropic 的拆分格式正好相反
            parts: list[dict[str, Any]] = []
            for img in m.images:
                media, data = split_data_uri(img)
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media};base64,{data}"},
                })
            if m.text:
                parts.append({"type": "text", "text": m.text})
            return [{"role": m.role, "content": parts}]
        return [{"role": m.role, "content": m.text or ""}]


def _dump(obj: dict[str, Any]) -> str:
    import json

    # sort_keys 让同样的参数序列化成同样的字节 —— 对提示词缓存命中有影响
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


# DeepSeek 的 API 连 image_url 这个类型都不认识，实测原话：
#   invalid_request_error | unknown variant `image_url`, expected `text`
# v4-flash 和 v4-pro 都一样（2026-08-02 直接打 api.deepseek.com 验的）。
# Claude / GPT 系都支持，所以这里列「已知不支持」的黑名单，其余默认支持。
_NO_VISION = ("deepseek",)


def supports_vision(model: str) -> bool:
    m = (model or "").lower()
    return not any(h in m for h in _NO_VISION)


# ------------------------------------------------------------------ 工厂


def make_adapter(cfg: LLMConfig) -> LLMAdapter:
    if cfg.provider == "anthropic":
        return AnthropicAdapter(cfg)
    if cfg.provider == "openai_compat":
        return OpenAICompatAdapter(cfg)
    raise ValueError(f"不认识的 provider: {cfg.provider}")
