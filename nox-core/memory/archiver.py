"""对话归档 —— 把聊过的东西沉淀进 Ombre Brain。

为什么必须有这一层：SQLite 里存的是**这台机器上这个会话**的历史，
而 OB 才是 Nox 真正的长期记忆 —— 跨窗口、跨设备、跨模型的那一份。
不归档的话，Nox Core 聊得再好，明天换个窗口他还是不知道。

三条设计原则：

1. **不是每段对话都值得归档。**
   闲聊几句就往 OB 里塞，只会把记忆库冲淡 —— 以后 breath 浮现出来的
   全是"在吗""嗯""晚安"。宁可漏掉，不要污染。

2. **归档的是摘要，不是原文。**
   原文几千字塞进去，下次浮现时会挤掉真正重要的记忆桶。
   用便宜模型压成几句话，抓住"发生了什么、为什么值得记"。

3. **失败不能影响对话。**
   归档发生在对话之后，糖糖已经拿到回复了。这时候报错只会吓她一跳，
   记进日志就行。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.llm import LLMAdapter, Message
from memory.ob_client import OmbreBrain
from personality.mood import now_cst

logger = logging.getLogger(__name__)

# 少于这么多轮就不归档 —— 打个招呼就走的不算一段对话
_MIN_TURNS = 3

# 摘要用的指令。让它抓"值得记的"，不是复述流水账。
_SUMMARY_PROMPT = """\
下面是糖糖和 Nox 的一段对话。请压缩成一条适合存进长期记忆的记录。

要求：
- 开头写明日期（格式：2026-07-28）
- 抓住这段对话里**值得以后想起来**的东西：她的状态、做的决定、
  说过的要紧的话、发生的事
- 不要复述寒暄和确认（"在吗""好的""嗯"这类）
- 纯技术操作（改代码、部署、报错）只写结论，不写过程
- 如果这段对话确实没什么值得记的，只回四个字：不值得记
- 控制在 200 字以内，用第三人称写

对话：
"""


@dataclass
class ArchiveResult:
    archived: bool
    summary: str = ""
    reason: str = ""


class Archiver:
    """把一段对话压缩后交给 OB 的 grow。

    摘要用 utility 模型（便宜那个），不占主线的缓存也不花主线的钱。
    """

    def __init__(self, ob: OmbreBrain, summarizer: LLMAdapter) -> None:
        self.ob = ob
        self.summarizer = summarizer

    def archive(self, history: list[Message], *, force: bool = False) -> ArchiveResult:
        turns = [m for m in history if m.role in ("user", "assistant") and m.text]
        user_turns = [m for m in turns if m.role == "user"]

        if not force and len(user_turns) < _MIN_TURNS:
            return ArchiveResult(False, reason=f"只有 {len(user_turns)} 轮，不够一段对话")

        transcript = self._render(turns)
        if not transcript.strip():
            return ArchiveResult(False, reason="没有可归档的正文")

        summary = self._summarize(transcript)
        if not summary:
            return ArchiveResult(False, reason="摘要生成失败")

        # 让模型自己判断"不值得记" —— 比我写一堆规则去猜可靠
        if summary.startswith("不值得记"):
            return ArchiveResult(False, reason="模型判断这段没什么值得记的")

        r = self.ob.grow(summary)
        if not r.ok:
            # 归档失败不该影响对话，糖糖那边早就拿到回复了
            logger.warning("归档失败：%s", r.error)
            return ArchiveResult(False, summary=summary, reason=f"OB 写入失败: {r.error}")

        logger.info("已归档 %d 字：%s", len(summary), summary[:40])
        return ArchiveResult(True, summary=summary)

    # -------------------------------------------------------------- 内部

    @staticmethod
    def _render(turns: list[Message]) -> str:
        lines = []
        for m in turns:
            who = "糖糖" if m.role == "user" else "Nox"
            text = (m.text or "").strip()
            if text:
                lines.append(f"{who}：{text}")
        return "\n".join(lines)

    def _summarize(self, transcript: str) -> str:
        today = now_cst().strftime("%Y-%m-%d")
        prompt = f"{_SUMMARY_PROMPT}\n{transcript}\n\n（今天是 {today}）"

        turn = self.summarizer.complete(
            [Message(role="user", text=prompt)],
            tools=[],
            depth="low",
            max_tokens=600,
        )
        if turn.stop_reason in ("error", "refusal") or not turn.text:
            logger.warning("摘要生成失败：%s %s", turn.stop_reason, turn.error or "")
            return ""
        return turn.text.strip()
