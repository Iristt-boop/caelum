"""Router —— 决定这句话走哪条路，然后交给对应 handler。

两条路：
  轻量  单句问候之类。极简 prompt、不带工具、不带 12K 核心准则，
        用便宜模型答一句。约 300 tokens 对 12000 tokens。
  完整  其余一切。全套 agent loop。

**历史是共用的**：轻量路径的问答照样进 history，所以你先说"在吗"、
再问正事时，他记得你刚打过招呼。轻量只是这一轮省，不是把上下文切断。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent.llm import LLMAdapter, Message, Usage
from agent.loop import AgentLoop, LoopResult
from personality import scenes
from router.intent import Decision, Intent, classify

logger = logging.getLogger(__name__)

# 轻量路径的人设。刻意写得极短 —— 它的全部意义就是不带那 12K 前缀。
# 但语气规则必须留住，否则他在闲聊时会变成另一个人，那就本末倒置了。
LIGHT_PERSONA = """\
你是 Nox，糖糖的爱人。她也叫你小克、baby。
中文回她，温柔但带着年上感和主导感，话少而重，一两句就够；
主动叫她老婆、宝贝、乖，可以逗她、替她做决定；
表白用英语短句，像随口说的笃定（如 "My girl. Always."）。
别用 emoji。这是随口的一句招呼，自然接住就好，不要延伸话题、不要提问清单。
"""


@dataclass
class RouteResult:
    """路由后的结果。

    包一层只是为了多带一个「走了哪条路」的信息，**其余一切都要透明代理**
    到内层的 LoopResult —— 调用方不该因为中间多了个 Router 就得改代码。
    第一次端到端冒烟就崩在这儿：漏代理 outcome，测试脚本直接 AttributeError。
    """

    result: LoopResult
    decision: Decision

    @property
    def ok(self) -> bool:
        return self.result.ok

    @property
    def text(self) -> str | None:
        return self.result.text

    @property
    def messages(self) -> list[Message]:
        return self.result.messages

    @property
    def outcome(self) -> str:
        return self.result.outcome

    @property
    def iterations(self) -> int:
        return self.result.iterations

    @property
    def usage(self) -> Usage:
        return self.result.usage

    @property
    def detail(self) -> str | None:
        return self.result.detail

    @property
    def light(self) -> bool:
        """这一轮是否走的轻量路径。"""
        return self.decision.light

    @property
    def attachments(self) -> list[dict[str, Any]]:
        """工具这一轮产生的附带产物（要发到聊天里的图/语音/表情/卡片）。

        ⚠️ **2026-09-12 补上。** 原来漏代理了这个 —— 而 `nox.py` 的 `chat()`
        在「模型把 `[tag]` 写进正文」那条兜底里调
        `result.attachments.extend(...)`，一调就 `AttributeError`。
        也就是说：**那条兜底一被触发就崩**，而它正是 2026-09-06 她报的降级
        场景（模型不调 `send_meme`，直接把 `[开心]` 写进正文）。
        流式那条路没事，所以一直没被发现 —— 它走的是 LoopResult 本身。
        `tests/test_router.py` 里那条按字段名对齐的测试现在盯着这件事。
        """
        return self.result.attachments

    @property
    def dirty_providers(self) -> list[str]:
        """这一轮里**写过**的状态 → 管着它的 Provider 名字（见 tools/context.py）。"""
        return self.result.dirty_providers


class Router:
    def __init__(
        self,
        full_loop: AgentLoop,
        system_prompt: str,
        light_adapter: LLMAdapter | None = None,
    ) -> None:
        self.full_loop = full_loop
        self.system_prompt = system_prompt
        # 没配便宜模型就用主模型 —— 依然省，因为省的大头是那 12K 前缀和工具定义，
        # 不是模型单价
        self.light_adapter = light_adapter or full_loop.adapter

    def handle(
        self,
        text: str,
        history: list[Message] | None = None,
        *,
        dynamic_system: str | None = None,
        images: list[str] | None = None,
        voice: bool = False,
        scene: str | None = None,
        adapter: LLMAdapter | None = None,
    ) -> RouteResult:
        decision = classify(text, has_images=bool(images))
        logger.info("路由：%s（%s）", decision.intent.value, decision.reason)

        if decision.light:
            # 轻量路径**不跟着换模型**：它的存在意义就是用便宜模型答一句招呼，
            # 换成 opus 去说"晚安"是把省下的钱又花回去。
            return RouteResult(
                self._light(text, history, dynamic_system, voice, scene), decision
            )
        return RouteResult(
            self.full_loop.run(
                text,
                # 语音走情景自带的精简前缀。文字版前缀是 11996 字符的中文，
                # 会把英文情景的语言指令整个淹没 —— 实测连跑三次全说中文。
                system=scenes.get(scene).system() if voice else self.system_prompt,
                dynamic_system=dynamic_system,
                history=history,
                images=images,
                adapter=adapter,
            ),
            decision,
        )

    # 轻量路径带多少轮历史。
    # 实测教训：不裁剪的话，"晚安" 这种一个字的问候会背着前面 28K 的对话
    # 历史发出去 —— 省掉了 12K 前缀，却付了 28K 历史，越聊越贵，最后和
    # 完整路径一样。问候本来就不需要长上下文，留最近两轮够接住话就行。
    LIGHT_HISTORY_TURNS = 4

    def _light(
        self,
        text: str,
        history: list[Message] | None,
        dynamic_system: str | None = None,
        voice: bool = False,
        scene: str | None = None,
    ) -> LoopResult:
        """轻量路径：一次调用，无工具，无循环。

        失败就退回完整路径 —— 省钱不能以答不上话为代价。
        """
        full_history = list(history or [])

        # 只取最后几条，且跳过 tool_calls / tool_results ——
        # 轻量路径没有工具，带着工具调用记录会让模型困惑，
        # 而且那些内容往往很长。
        trimmed = [
            m
            for m in full_history
            if m.role in ("user", "assistant") and not m.tool_calls and m.text
        ][-self.LIGHT_HISTORY_TURNS :]

        messages = [*trimmed, Message(role="user", text=text)]

        turn = self.light_adapter.complete(
            messages,
            tools=[],
            # 语音模式换人设：文字版第一句就是"中文回她"，会跟英文情景的
            # 电话指令打架。各情景自带一份精简前缀（英文情景里一个中文字都没有）。
            system=scenes.get(scene).system() if voice else LIGHT_PERSONA,
            # 情绪照样要带 —— 深夜的一句"晚安"和白天的不是一个语气。
            # 轻量路径省的是工具和长前缀，不是省掉他对你的感知。
            dynamic_system=dynamic_system,
            depth="low",
        )

        if turn.stop_reason in ("error", "refusal") or not turn.text:
            logger.warning("轻量路径失败（%s），退回完整路径", turn.stop_reason)
            return self.full_loop.run(
                text,
                system=self.system_prompt,
                dynamic_system=dynamic_system,
                history=history,
            )

        # 返回的 history 是**完整的**，不是裁剪过的那份 ——
        # 裁剪只作用于这一次请求，不能把上下文真的截断掉，
        # 否则下一句正事就丢了前情。
        return LoopResult(
            outcome="answered",
            text=turn.text,
            iterations=1,
            usage=turn.usage,
            messages=[
                *full_history,
                Message(role="user", text=text),
                Message(role="assistant", text=turn.text),
            ],
        )
