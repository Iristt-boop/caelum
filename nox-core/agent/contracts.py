"""Interaction Contract（交互契约）—— 工具伪输出的统一出口（2026-10-10，配置层 P0.5）。

他偶尔不调工具，而是把工具的参数直接写进正文：

    [语音条 softly] Okay okay, no more teasing…
    [中文对照] 好啦好啦，不逗你了…

这一条进了会话历史，下一轮他就照着写 —— 10-10 一条传成 9 条，还传进了电话。
同一类事故此前补过三次（`[开心]` / `[!send_meme]` / `[表情包]`），每次一块补丁。

这里把「伪输出长什么样」做成**工具自己声明的数据**（`ToolSpec.output_contract`），
出口只有一个：loop 收尾时 `apply()`。新增工具只要注册契约，这里一行不改。

🔴 触发要三条同时成立（糖糖 10-10 定的，防误伤）：
  1. 正文里有**行首**的契约 marker —— 是结构，不是任意位置的关键词
  2. 这一轮的 loop 允许这个工具（注册了它）
  3. 这一轮模型**没有**真调它
只扫他的输出，从不扫她的消息：她打 `[笑哭]` 永远只是她的话。

契约**不发给模型**（adapter 只序列化 name / description / parameters），
所以加契约不改工具签名、不冷缓存。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

#: 解析失败返回 None —— 那就只剥不补
Parser = Callable[[str], "dict[str, Any] | None"]


@dataclass(frozen=True)
class OutputContract:
    """一个工具的「表现协议」：它被写成文字时长什么样、能不能还原成真调用。"""

    #: 伪输出的行首标记。只认方括号开头的结构，不认裸词（「语音消息」这种会误伤正常句子）
    markers: tuple[str, ...]
    #: 一整块伪输出 → 工具参数。None = 认得出但还原不了，只剥
    parse: Parser | None = None
    #: 能还原时要不要替他补调。补调走 loop 唯一的 `_execute`（闸门 / 上限 / 校验全复用）
    rescue: bool = False
    #: 没有 marker、但**一整行**长得就是伪输出（10-10 晚：他先在正文里写一段带 [softly] 的英文
    #: 「语音草稿」，再去调 send_voice_message）。**只在文字聊天里用** —— 打电话时他说的英文
    #: 带语气标签是正常输出，绝不能剥（loop 传 voice=True 时跳过）
    line_detector: Callable[[str], bool] | None = None


@dataclass
class Artifact:
    """正文里认出的一块伪输出，以及怎么处理它。"""

    tool: str
    text: str
    arguments: dict[str, Any] | None
    #: rescue          三条成立且解析得出 → 补调
    #: already_called  这一轮已经真调过 → 正文里那段是重复描述，只剥
    #: unparsed        认得出但解析不出 / 契约不许补 → 只剥
    #: not_allowed     这一轮没有这个工具 → 不算伪输出，原样留着
    action: str


#: 工具调用的「格式」本身被当成字写进正文（10-23：她说「你用一下自己的小电脑」，那一轮没挂上 desk_*，
#: deepseek-flash 就在正文里写了一串 <invoke name="caelum_map"> … —— 一次真调用都没有，她看到的是一屏标签）。
#: 和上面按工具声明的契约不同：这是**任何工具**都可能漏出来的通用形状，所以不挂在某个工具上，loop 直接用。
#: 只认行首的这几个标签名（function_calls / invoke / parameter / tool_call），正文里聊到尖括号不受影响
_MARKUP_LINE = re.compile(r"^\s*</?(?:[a-z]+:)?(?:function_calls|invoke|parameter|tool_call)\b", re.I)


def is_markup_line(line: str) -> bool:
    return bool(_MARKUP_LINE.match(line or ""))


def strip_markup(text: str | None) -> tuple[str | None, int]:
    """剥掉写成文字的工具调用标签行，返回 (剥完的正文, 剥了几行)。剥完只剩空白就回 ""（调用方当没说出口处理）"""
    if not text:
        return text, 0
    lines = text.split("\n")
    keep = [ln for ln in lines if not is_markup_line(ln)]
    n = len(lines) - len(keep)
    if not n:
        return text, 0
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()
    return out, n


def _index(contracts: dict[str, OutputContract]) -> list[tuple[str, str]]:
    """(marker, 工具名)，长的在前 —— 短 marker 是长 marker 前缀时别被先咬走。"""
    pairs = [(m, name) for name, c in contracts.items() for m in c.markers]
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


def all_markers(contracts: dict[str, OutputContract]) -> tuple[str, ...]:
    """给流式过滤器用：所有 marker，长的在前。"""
    return tuple(m for m, _ in _index(contracts))


def _match(line: str, index: list[tuple[str, str]],
           detectors: list[tuple[str, Callable[[str], bool]]] = ()) -> str | None:
    s = line.lstrip()
    for marker, tool in index:
        if s.startswith(marker):
            return tool
    for tool, det in detectors:
        if s and det(s):
            return tool
    return None


def line_detectors(contracts: dict[str, OutputContract]) -> list[tuple[str, Callable[[str], bool]]]:
    """(工具名, 整行判别器)。给流式过滤器和 apply 共用。"""
    return [(name, c.line_detector) for name, c in contracts.items() if c.line_detector is not None]


def apply(
    text: str | None,
    *,
    contracts: dict[str, OutputContract],
    allowed: set[str],
    called: set[str],
    voice: bool = False,
) -> tuple[str | None, list[Artifact]]:
    """扫一遍他的完整回复，返回 (剥干净的正文, 认出的伪输出)。

    一块伪输出 = 同一个工具的 marker 行，加上它们之间的空行
    （语音条那种「英文一行、空一行、中文对照一行」算一块）。
    `voice=True`（打电话）时不用整行判别器 —— 那边带语气标签的英文就是正文。
    """
    if not text or not contracts:
        return text, []
    index = _index(contracts)
    dets = [] if voice else line_detectors(contracts)
    lines = text.split("\n")
    keep: list[str] = []
    artifacts: list[Artifact] = []
    i = 0
    while i < len(lines):
        tool = _match(lines[i], index, dets)
        if tool is None:
            keep.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < len(lines) and (not lines[j].strip() or _match(lines[j], index, dets) == tool):
            j += 1
        # 块尾的空行不算块里的（留给正文当段落间隔）
        while j - 1 > i and not lines[j - 1].strip():
            j -= 1
        block = "\n".join(lines[i:j])
        contract = contracts[tool]
        if tool not in allowed:
            artifacts.append(Artifact(tool, block, None, "not_allowed"))
            keep.extend(lines[i:j])
        else:
            args = contract.parse(block) if contract.parse else None
            if tool in called:
                action = "already_called"
            elif args is not None and contract.rescue:
                action = "rescue"
            else:
                action = "unparsed"
            artifacts.append(Artifact(tool, block, args, action))
        i = j
    if not any(a.action != "not_allowed" for a in artifacts):
        return text, artifacts
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()
    return out, artifacts
