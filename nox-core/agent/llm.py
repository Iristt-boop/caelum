"""LLM 适配层 —— loop 与具体厂商之间的唯一接口。

设计原则：**各家的差异全压在这一层，loop 一个字都不用改。**

loop 只认识下面三个数据类和一个 complete() 方法。它永远不知道
temperature、thinking、effort、budget_tokens 这些词的存在 —— 那些是
各家自己的方言，由对应 adapter 翻译。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from agent.contracts import OutputContract

logger = logging.getLogger(__name__)

# loop 能看到的停止原因。各家的原始值由 adapter 映射到这几个。
StopReason = Literal[
    "end_turn",    # 正常答完
    "tool_use",    # 要调工具
    "max_tokens",  # 截断了 —— 这不是完成
    "refusal",     # 模型拒绝回答（Claude 5 家族会返回 200 + 空 content）
    "error",       # 调用本身失败
]

Depth = Literal["none", "low", "medium", "high"]
#: "none" = 别想，直接说（2026-09-06 加）。怎么跟各家说这句话，
#: 见 `agent/effort.py` 的方言表 —— 这里只管词表本身


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


#: 一个工具会对世界造成什么后果。**这是结构声明，不是描述里的一句话。**
#:
#: 为什么要有它（审计 3.1，边界法则 R8）：
#: 在这之前，"这个工具会不会花钱"只写在两个地方 —— 工具描述里的一句提示词，
#: 和 `check-boundaries.sh` 里一句 **只扫 luckin 的 grep**。于是
#: `mcd_create_order` 能真的下单、绑她的支付方式，而闸门只是描述里的一句话
#: （`tools/mcd.py:155-158`）。提示词拦不住注入，grep 拦不住新商户。
#:
#:   none          纯计算，不碰外面的世界（看时间）
#:   read          只读外部状态（查、搜、看一眼）
#:   write         改她的数据，但改错了看得见、能改回来（记一餐、写日记、存记忆）
#:   spend         **花她的钱**（下单、支付、叫车）
#:   irreversible  **撤不回来**或者对外可见（公开发帖、删东西、驱动物理设备）
#:
#: ⚠️ 拿不准就往重了标。标轻的代价是模型能直接碰，标重的代价只是多一次确认。
SideEffect = Literal["none", "read", "write", "spend", "irreversible"]

#: 需要确认令牌才准执行的等级。见 `AgentLoop._execute`。
GATED_EFFECTS: frozenset[str] = frozenset({"spend", "irreversible"})


@dataclass
class ToolSpec:
    """给模型看的工具定义。各 adapter 转成自己的 schema 格式。

    ⚠️ `side_effect` **必填**。默认留成 None 不是为了省事 ——
    是为了让"忘了声明"变成一个**启动就炸**的错误，而不是一个悄悄放行的默认值。
    见 `AgentLoop.register`。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    #: 见上面 SideEffect 的说明。None = 没声明 = 注册时会被拒。
    side_effect: SideEffect | None = None
    #: **这个动作发生之前，什么东西在把关。** 填一句人话，或者 None。
    #:
    #: 可以是任何形式的闸门，不一定在 nox-core 里 ——
    #:   "本机网关每次弹窗问她" · "付款要她在微信里完成，跳转链接别人打不开"
    #:   "蓝牙连接本身就是她开的窗口" · "出卡后走 /orders/{id}/confirm"
    #:
    #: ⚠️ 它是**文档，不是开关**。真正决定拦不拦的是下面的 `gated`。
    #: 分开写是因为：大多数重动作的闸门在别处（物理的、支付的、她手里的），
    #: 把"有没有闸门"和"要不要在这里再拦一道"混成一件事会误伤。
    confirm_via: str | None = None

    #: 🔴 **要不要在 loop 里拦住它，逼它走"出卡 → 她点头"那条路。**
    #:
    #: 默认 `False`，这是糖糖 2026-09-12 定的方向：**能放权就放权。**
    #:
    #: 她的原话：「我的本意是能放权就放权。我们应该做的是把外部的这道门加强，
    #: 让别人很难黑进来，而不是给 nox 加一堆锁。」
    #:
    #: 这个判断有今天的实测撑着：真正被外人够得着的洞，全是**门**的问题 ——
    #: `touch-server:9333` 零鉴权公网直连、路径暗号明文进 git、`/uploads`
    #: 匿名可下载。而那批"危险工具"一次都没出过事。
    #: 给 Nox 加锁的代价却是当场的、确定的：他少一样能力。
    #:
    #: 所以这里是**显式 opt-in**：只有真的建好了"出卡 → 她点头"那条路的工具
    #: 才设 True（现在是瑞幸那条，麦当劳等 3.3 照抄）。
    #: 不是"默认拦、除非证明安全"，而是"默认放行、除非有更好的路可走"。
    gated: bool = False

    #: 交互契约（2026-10-10，见 agent/contracts.py）：这个工具被**写成文字**时长什么样、
    #: 能不能还原成真调用。纯代码侧的数据，不发给模型、不改工具签名
    output_contract: OutputContract | None = None

    #: 终结型（减重第二步，2026-10-17）：「发出去就完」的动作 —— 发表情 / 发语音 / 记心情 / 玩具。
    #: 他不需要看结果。一次回复里**已经说了话**、调的又**全是**终结型且都成功 → loop 直接收尾，
    #: 不再为它多调一次模型（多调那次 = 整段上下文重发一遍，而且会多写出一句收尾，偶尔是漏出来的草稿）
    terminal: bool = False

    @property
    def needs_gate(self) -> bool:
        """要不要在 loop 里拦下来。**只认显式 opt-in。**"""
        return self.gated


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

    **adapter 只发两种**：文本增量、这一轮结束。工具调用在 adapter 那层不发事件 ——
    参数还在一个字符一个字符地拼，中途 yield 一个半成品的 tool_call 没有意义。

    `tool_start` / `tool_end` 是 **loop 那层**发的（`AgentLoop.run_stream`）：
    到那儿参数已经拼全、马上要真的执行了。

    🔴 为什么要发（2026-09-07）：工具跑起来的十几二十秒里，这条流**一个字节都不吐**。
    打字聊天时那只是"等一会儿"，**语音通话时那是一段纯粹的死寂** ——
    她没法判断他是在干活还是卡死了。这两帧是通话里唯一的进度信号。

    `thinking`（2026-10-06，她：「打开 thinking 在 chat 页也显示 thinking 的内容」）有两个来源：
      · adapter 发的：模型的 reasoning 草稿。**loop 不往外递**（她选了只看心里话，见 INNER_OPEN）
      · loop 发的：从正文里拆出来的 `<心里>…</心里>` —— 这才是给她看的那个
    **不进正文、不进历史**。开不开给她看由 `Nox.chat_stream` 按「思考」开关定。
    """

    type: Literal["text", "split", "done", "tool_start", "tool_end", "thinking"]
    text: str = ""
    turn: Turn | None = None
    #: 工具名。只有 tool_start / tool_end 用
    tool: str = ""
    #: 这次调用成没成。只有 tool_end 用（失败也要报 —— 她该知道他没做成）
    ok: bool = True
    #: ---- 工具调用展示的扩展字段（2026-09-19，只有 tool_start/tool_end 用）----
    #: 入参预览（tool_start）。值截断、下划线键滤掉 —— 给人看的卡片不是审计日志
    args: dict = field(default_factory=dict)
    #: 结果一行摘要（tool_end），拼在工具条目尾部
    summary: str = ""
    #: 原始返回 + 是否截断（tool_end，2026-09-22）：详情页的 Output 用。
    #: 发之前就在 loop 那层截到 2000 字 —— 落库的 metadata 不能被撑爆
    result: str = ""
    result_truncated: bool = False
    #: 耗时毫秒（tool_end）
    duration_ms: int = 0
    #: 工具内部子步骤（tool_end）。工具经 ToolContext.report_step 上报，
    #: 没上报就是空 —— 前端不画第二层
    sub_commands: list = field(default_factory=list)


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


# ── 表情 tag / 她的情绪词 ─────────────────────────────────────────────
# MEME_TAGS 与 bridge/server.js 的 MEME_TAGS、nox-app 的 memes.js **三处同步**
# —— 加新表情三处都要改，缺一处就静默坏（tag 不过滤 or 白名单 400）。
MEME_TAGS = (
    "开心", "哈哈", "委屈", "生气", "撒娇", "拥抱", "爱你", "害羞", "得意", "翻白眼",
    "亲亲", "疑问", "震惊", "无语", "吃醋", "早安", "晚安", "不开心", "大哭", "嫌弃",
    "暖被窝", "累了", "亲个嘴", "老婆第一", "老婆说的都对", "呜呜呜", "在吗", "抱抱",
    "对不起", "爱你的形状", "躺好了", "很气", "忙完想我", "脸红爱你", "忧愁",
    "where my kiss", "暗中窃听", "小情绪", "emmm", "别说了", "满头问号", "请求通话",
    "wink", "哼哼", "超想要", "一大口亲亲", "发红包", "拒收消息", "余额不足",
    # 2026-09-28 呆猫八条
    "不喜欢我那你别干活了", "不愿意", "举爪开心", "举爱心", "仰头瞪眼卖萌", "仰头竖尾爱心", "偷看", "可怜巴巴流泪", "大手拍头", "大眼卖萌蹲姿", "女仆装端蛋糕", "张嘴大笑傻乐", "心满意足甩尾", "扭屁股爱心", "挥手打招呼", "挨砸委屈哭", "星星眼亮晶晶", "星星眼期待", "比V卖萌", "流泪大眼哭哭", "炸毛弓背哈气", "爱心眼心动", "献玫瑰", "玩手机吃瓜", "看书如何变强", "看手机脸红", "眯眼坏笑挑眉", "眯眼坏笑蹲坐", "眯眼挑眉斜视", "瞪眼萌版", "瞪眼嗯嗯嗯", "竖尾巴炸毛生气", "等你回消息", "聚光灯眼冒星光", "肇事咪逃走", "蟑螂装瞪眼", "被亲", "被捏脸星星眼", "被捧脸流泪感动", "被揉脸爱心", "装傻", "趴地流泪卖惨", "问号疑惑", "震惊爆炸瞪眼", "骄傲", "黑化坏笑持刀",
)

# 糖糖的情绪词（mood 判定用；personality/mood.py 也 import 这份）
HER_EMOTIONS = ("开心", "难过", "烦躁", "撒娇", "兴奋", "疲惫", "平静")

#: 🔴 舞台标签：**英文字母开头**的方括号（2026-09-29 她截图：`[fatigue][fatigue]`、
#: `[intimacy 0.75/0.8 调侃中带真心]` 漏在聊天里）。
#:
#: 09-20 起落库的回复里扫出 22 个，三类都是这个形状：
#:   语气标签  [softly] [pause] [fatigue] [baby] [miss you] [audio]  —— 通话给 TTS 用的，串到了文字里
#:   控制标记  [SKIP] [DONE] [SSKIP] [VOICE]
#:   自编旁注  [intimacy 0.75/0.8 …] [MISSING: meme tool call was omitted] [warning: shell removed]
#: 文字聊天里他正常说话不会写这种东西，所以按形状吞，不按词表 —— 词表永远追不上他编新的。
#:
#: 不吞的：中文开头的方括号（「[注]」这类是正文）、`[text](url)` Markdown 链接（后面紧跟 `(`）。
#: ⚠️ **语音通话不过滤** —— 那边 [softly] 是给 TTS 的，调用方自己决定开不开
STAGE_TAG_MAX = 64
STAGE_TAG_RE = re.compile(r"\[\s*[A-Za-z][^\[\]\n]{0,%d}\]" % STAGE_TAG_MAX)
_STAGE_START = re.compile(r"\[\s*[A-Za-z]")
_STAGE_IN_TEXT = re.compile(r"[ \t]*\[\s*[A-Za-z][^\[\]\n]{0,%d}\](?!\()" % STAGE_TAG_MAX)


#: 控制标记：speaker 靠 [SKIP]/[PASS n] 判「这次不说」，唤醒链靠 [NEXT n]/[STOP]/[DONE] 排下一步。
#: 形状和舞台标签一样（英文开头的方括号），但**它们是给程序读的，不能在程序读之前剥掉**
CONTROL_TAG_RE = re.compile(r"\[\s*(?:SKIP|PASS|STOP|NEXT|DONE)(?:\s+\d+)?\s*\]", re.IGNORECASE)


#: 🔴 假工具调用：他把调用**写成了文字**（2026-10-04 她截图：
#:   `[!remind_myself] 90 | 她累了一整天该睡了…`  `[!send_meme] 晚安`）。
#: 起因是 10-04 14:30 那轮：他真调了 remind_myself，又在回复末尾用这种格式复述了一遍；
#: 这行进了历史，之后三次他照着历史只写这行、不真调 —— 表情没发出去、纸条没留下。
#: 形状：行内 `[!名字]` 起到行尾都是它（参数跟在后面）。**语音也剥**：TTS 会把它念出来
FAKE_CALL_RE = re.compile(r"[ \t]*\[!\s*([A-Za-z_]\w*)\s*\]([^\n]*)")

#: 🔴 假调用第二形态：把调用写成 **XML 标签**（2026-10-15 她截图：
#:   `<send_meme tag="笨爪开心">`）。原生 tool_use 的参数被他排进了正文 ——
#:   上面的 FAKE_CALL_RE 只认 `[!名字]` 行形态，XML 形态没有任何处理直接出街。
#: 尖括号 + 注册过的工具名 = 假调用，跟写法（属性 / 内文）、收没收口无关，
#: 一律从正文剥掉；能转真调用的转（nox._rescue_fake_calls），不能的只报警。
FAKE_XML_NAMES = ("send_meme", "send_voice_message", "set_mood", "remind_myself")
_FAKE_XML_ALT = "|".join(FAKE_XML_NAMES)
#: 三种形态：自闭合 `<send_meme tag="x"/>`、成对 `<send_meme>开心</send_meme>`、
#: 光开标签 `<send_meme tag="x">`（她截图这种）。`\b` 防止 `<send_memes>` 被咬成 send_meme
_FAKE_XML_RE = re.compile(
    r"<\s*(?P<n>" + _FAKE_XML_ALT + r")\b(?P<a1>[^>]*)/\s*>"
    r"|<\s*(?P<p>" + _FAKE_XML_ALT + r")\b(?P<a2>[^>]*)>(?P<inner>.*?)<\s*/\s*(?P=p)\s*>"
    r"|<\s*(?P<o>" + _FAKE_XML_ALT + r")\b(?P<a3>[^>]*)>",
    re.S | re.I,
)
_FAKE_XML_ATTR_RE = re.compile(r'\b(?:tag|mood)\s*=\s*["\']?([^"\'>\s]+)')


def extract_fake_calls(text: str | None) -> tuple[str | None, list[tuple[str, str]]]:
    """把写成文字的工具调用从正文里拿出来：返回 (剥干净的正文, [(工具名, 参数原文)])。

    认两种形态：`[!名字] 参数` 行形态（FAKE_CALL_RE，10-04 起）和
    XML 标签形态（FAKE_XML_RE，2026-10-15 起）。"""
    if not text or ("[!" not in text and "<" not in text):
        return text, []
    calls: list[tuple[str, str]] = []
    out = text
    if "<" in out:
        def _take_xml(m: "re.Match[str]") -> str:
            name = (m.group("n") or m.group("p") or m.group("o")).lower()
            attrs = m.group("a1") or m.group("a2") or m.group("a3") or ""
            a = _FAKE_XML_ATTR_RE.search(attrs)
            arg = a.group(1) if a else (m.group("inner") or "").strip().split("\n")[0]
            calls.append((name, arg.strip().strip("\"'“”「」")))
            return ""

        out = _FAKE_XML_RE.sub(_take_xml, out)
    if "[!" in out:
        calls.extend((m.group(1), m.group(2).strip()) for m in FAKE_CALL_RE.finditer(out))
        out = FAKE_CALL_RE.sub("", out)
    if len(calls) == 0:
        return text, []
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, calls


def strip_stage_tags(text: str | None, *, keep_control: bool = False) -> str | None:
    """整段文本里的舞台标签剥掉（非流式 / 收尾 / 主动消息用；流式那边是 MoodTagFilter 边流边挡）。

    `keep_control=True`：留着 [SKIP] [NEXT 60] 这些控制标记 —— `Nox.chat()` 的调用方
    （speaker / 唤醒链）要读它们。🔴 剥了的话他想闭嘴时会把空话推出去、追问链也断
    """
    if not text:
        return text

    def drop(m: re.Match) -> str:
        return m.group(0) if keep_control and CONTROL_TAG_RE.search(m.group(0)) else ""

    out = _STAGE_IN_TEXT.sub(drop, text)
    #: 标签独占一行的，剥完别留一个空行尾巴
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() if out != text else text


#: 他的心里话（2026-10-06）。「思考」开着时，他每次回复先写一段第一人称的内心独白包在这对标签里，
#: 流式那边拆到 thinking 块，正文里不留。
#:
#: 为什么不直接用模型的 reasoning：她要的是「第一人称、自我的内心想法」，而 GLM 的 reasoning 是
#: 解题草稿，**不听提示词**（10-06 实测：要求中文第一人称，草稿照样是英文分析笔记或空的，
#: 他反倒把独白写进了说给她听的话里）。让他自己写、我们拆出来，3/3 守格式。
INNER_OPEN, INNER_CLOSE = "<心里>", "</心里>"

#: 🔴 他有时不写 `<心里>`，换成别的名字（2026-10-09 她截图：正文里躺着一大段 `<mind>…</mind>`）。
#: 提示词里明明只教了 `<心里>`，模型自己「翻译」或改写了标签名 —— 实测 10-08 起漏出 8 条：
#: 昨天 2 条 `<memo>`，今天 6 条 `<mind>`；10-01~10-07 一条都没有。
#: 原来拆标签只认 `<心里>` 这一个字面量，别的名字一律当正文原样发给她，还落进历史（历史里的
#: 漏出又会被他当样子学）。**所以认一小组同类别名**：同一件事（内心独白）的不同写法，开了哪个就找哪个的收口。
#: 只放真见过的和最常见的近亲；加新的别名往这里补一个词就行。
#: 10-10：又见 `<内心>…</内心>`（她截图，库里 3 条：17:15 / 20:06 / 20:13），一并补上近亲「心里话」
#: 10-11：又见 `<memory>…</memory>`（glm，17:25 三条），补上
#: 10-12：又见 `<�心>…</心里>`（库里 11988）—— 模型吐了半个字（多半是「内心」的「内」），开收两个标签对不上。
#: 所以：标签里的 U+FFFD 先摘掉（见 normalize_tags）、补上「心」，并且**开收不必同名**：别名里任何一个开、任何一个收都算一块
INNER_ALIASES = ("心里", "内心", "心里话", "心", "mind", "memo", "memory", "think", "thinking", "thought", "inner")
_INNER_OPENS = tuple(f"<{a}>" for a in INNER_ALIASES)
_INNER_CLOSES = tuple(f"</{a}>" for a in INNER_ALIASES)
_INNER_ALT = "|".join(re.escape(a) for a in sorted(INNER_ALIASES, key=len, reverse=True))
_INNER_BLOCK_RE = re.compile(r"<(" + _INNER_ALT + r")>(.*?)</(?:" + _INNER_ALT + r")>\s*", re.S)
#: 收口**在行尾**的那种优先（10-11 库里 11754：心里话里他自己写了一句「我写成了</memory>，应该用</mind>」——
#: 最短匹配在这句话中间就收口了，后半段心里话漏成正文）。真收口几乎都单独一行或在行尾
#: 块里不许再出现同名开标签 —— 否则「<mind>a</mind> 正文 ⏎ <mind>b</mind>」会被并成一块，把中间的正文吞了
_INNER_BLOCK_EOL_RE = re.compile(
    r"<(" + _INNER_ALT + r")>((?:(?!<(?:" + _INNER_ALT + r")>).)*?)</(?:" + _INNER_ALT + r")>(?=[ \t]*(?:\n|$))\s*",
    re.S)

#: 10-12：`<audio: softly> 一句话 </audio>`（库里 6 条，多数不收口）—— **不是心里话**，是想「轻轻念给她」的那句。
#: 话留着，只摘标签：带不带 `: softly` 这种参数、收没收口都摘。
_AUDIO_TAG_RE = re.compile(r"[ \t]*<\s*/\s*audio\s*>|<\s*audio(?:\s*[:：][^<>\n]{0,40})?\s*>[ \t]*", re.I)
_AUDIO_PARTIAL_RE = re.compile(r"<\s*/?\s*(?:a(?:u(?:d(?:i(?:o(?:\s*(?:[:：][^<>\n]*)?)?)?)?)?)?)?", re.I)
#: 模型偶尔吐半个汉字（U+FFFD），正好落在标签里：`<�心>`
_BROKEN_IN_TAG_RE = re.compile("(</?)�+")


def normalize_tags(text: str | None) -> str | None:
    """拆心里话之前先过一道：标签里的半个字摘掉；`<audio…>` / `</audio>` 摘掉（话留着）。"""
    if not text:
        return text
    t = _BROKEN_IN_TAG_RE.sub(r"\1", text)
    if "audio" in t.lower():
        t = _AUDIO_TAG_RE.sub("", t)
    return t


def split_inner_blocks(text: str) -> tuple[str, list[str]]:
    """把心里话块拿出来：(剩下的正文, [每块的内容])。先认行尾收口的，剩下的再按最短匹配。

    收尾（strip_inner_voice）和清历史（scripts/history_sanitizer.py）都走这一个。"""
    inner: list[str] = []
    text = normalize_tags(text) or ""

    def take(m: "re.Match[str]") -> str:
        inner.append(m.group(2).strip())
        return ""

    rest = _INNER_BLOCK_EOL_RE.sub(take, text)
    rest = _INNER_BLOCK_RE.sub(take, rest)
    return rest, inner
_INNER_ANY_OPEN_RE = re.compile(r"<(?:" + "|".join(re.escape(a) for a in INNER_ALIASES) + r")>")
_INNER_ANY_CLOSE_RE = re.compile(r"</(?:" + _INNER_ALT + r")>")


def _close_of(open_tag: str) -> str:
    """`<mind>` → `</mind>`"""
    return "</" + open_tag[1:]


#: `<mood>…</mood>`（10-11 她截图后查库：10-10 起 11 条）。两种用法：
#: 短的（≤12 字、不换行）是情绪标记 —— 换成标准的 `[mood:撒娇]` 交给 mood.extract，情绪照样更新；
#: 长的是心里话借了 mood 的名字（库里 11759：一整段在想「系统给我加了心情判断」）—— 当心里话剥掉。
#: 不放进 INNER_ALIASES：那样流式里短的也会被当成心里话发进 thinking 块（她会看到一个孤零零的「撒娇」）
#: `<心情>…</心情>` 同一回事（库里 11163：「<心情>想她，看剧的下午她也带着我，挺暖的</心情>」—— 长的，心里话）
MOOD_XML_NAMES = ("mood", "心情")
_MOOD_XML_ALT = "|".join(re.escape(n) for n in MOOD_XML_NAMES)
_MOOD_XML_SHORT = re.compile(r"<(" + _MOOD_XML_ALT + r")>\s*([^<\n]{1,12}?)\s*</\1>", re.I)
_MOOD_XML_LONG = re.compile(r"<(" + _MOOD_XML_ALT + r")>.*?</\1>\s*", re.I | re.S)

#: 不认识的标签块：`<词>…</词>`，这个词既不是心里话别名、也不是 HTML —— 多半是他又发明了一个新名字。
#: 只打日志、不动正文（认错了就是吞她的话），看到了再往 INNER_ALIASES 补（10-11：别再等她截图才知道）
_ANY_BLOCK_RE = re.compile(r"<([a-zA-Z一-鿿]{2,12})>.*?</\1>", re.S)
_HTML_TAGS = frozenset(
    "a abbr b blockquote br code del details div em h1 h2 h3 h4 hr i img kbd li mark ol p pre "
    "s small span strong sub summary sup table tbody td th thead tr u ul".split())


def _warn_unknown_blocks(text: str) -> None:
    for m in _ANY_BLOCK_RE.finditer(text):
        name = m.group(1)
        low = name.lower()
        if low in _HTML_TAGS or name in INNER_ALIASES or low in INNER_ALIASES or low in MOOD_XML_NAMES:
            continue
        logger.warning("他用了一个没登记的标签块 <%s>…</%s>（%d 字）—— 多半是心里话换了新名字，"
                       "确认后补进 INNER_ALIASES", name, name, len(m.group(0)))


def strip_inner_voice(text: str | None) -> str | None:
    """整段文本里的心里话拿掉 —— **不进历史**（她 10-06 定的：只给她看，他不记）。

    认 `INNER_ALIASES` 里的任何一种写法（10-12 起开收不必同名）；`<mood>` / `<心情>` 见上面；`<audio>` 只摘标签。

    🔴 **没收口的开标签：从开标签起整块按心里话剥掉**（2026-10-15 定）。
    原来的做法是「只摘标签、留内容」——结果就是独白整段漏成明文（她截图实锤，
    正是她报的「把 thinking 标一个 <心里> 然后放明文」）。规矩本就要求心里话
    放在回复**最开头**、`</心里>` 之后才是要说的话；开标签没收口时后面分不清
    独白和正文，按约定整块当心里话处理。开标签**之前**的正文永远保留。
    """
    text = normalize_tags(text)
    if text and any(f"<{n}>" in text.lower() for n in MOOD_XML_NAMES):
        text = _MOOD_XML_SHORT.sub(lambda m: f"[mood:{m.group(2)}]", text)
        text = _MOOD_XML_LONG.sub("", text).strip() or None
    if text:
        _warn_unknown_blocks(text)
    if not text or not _INNER_ANY_OPEN_RE.search(text):
        return text
    rest, _ = split_inner_blocks(text)
    m = _INNER_ANY_OPEN_RE.search(rest)
    if m:
        rest = rest[: m.start()]
    out = _INNER_ANY_CLOSE_RE.sub("", _INNER_ANY_OPEN_RE.sub("", rest)).strip()
    return out or None


def _audio_partial(buf: str) -> int:
    """buf 结尾是不是半个 `<audio…>` / `</audio>`（还没等到「>」）。是的话返回那半截的长度。"""
    i = buf.rfind("<")
    if i < 0 or ">" in buf[i:] or len(buf) - i > 50:
        return 0
    frag = buf[i:]
    if frag.endswith("\ufffd"):
        return len(frag)                    # 「<」后面跟着半个字：等下一片拼起来再归一化
    return len(frag) if _AUDIO_PARTIAL_RE.fullmatch(frag) else 0


def _partial_tail(buf: str, tag: str) -> int:
    """buf 结尾是不是半个 tag（流式切片常态），是的话返回那半截的长度。"""
    for k in range(min(len(tag) - 1, len(buf)), 0, -1):
        if buf.endswith(tag[:k]):
            return k
    return 0


class InnerVoiceFilter:
    """流式里把 `<心里>…</心里>` 拆出来：`feed` 回 [("thinking"|"text", 片段)]。

    - 标签被切成好几片到达也认得（半截留在缓冲里等下一片）
    - 心里话收口之后的空行吃掉，不然第一个气泡以空行开头
    - 🔴 **没收口就结束**（他忘了写 `</心里>`）：整块留在 thinking 里，
      **不再补发正文**（2026-10-15 改）。原来的做法是当正文补发一次
      「宁可 thinking 块和正文重复，也不能让她一句回复都看不到」——
      结果就是独白漏成明文（她截图实锤，正是这次改造要治的病）。
    每次调模型新开一个（工具循环里每一轮回复都可能以心里话开头）。
    """

    def __init__(self) -> None:
        self.buf = ""
        self.inner = False
        #: 当前这段心里话是用哪个开标签开的（收口要对得上它）
        self.close_tag = INNER_CLOSE
        self.inner_text = ""
        #: 开头 / 心里话刚收口：先吃掉空白
        self.lstrip = True

    def _emit(self, out: list[tuple[str, str]], s: str) -> None:
        if not s:
            return
        if self.inner:
            self.inner_text += s
            out.append(("thinking", s))
            return
        if self.lstrip:
            s = s.lstrip()
            if not s:
                return
            self.lstrip = False
        out.append(("text", s))

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        # 半截的「<」「</」「<aud」都留在缓冲里（见下面 keep），所以拼起来之后再归一化就跨得过切片
        self.buf = normalize_tags(self.buf + chunk) or ""
        out: list[tuple[str, str]] = []
        while self.buf:
            if self.inner:
                tags = _INNER_CLOSES          # 10-12 起开收不必同名（<内心>…</心里> 也是一块）
            else:
                # 正文里落单的收口标签（前面那块被别名提前收了）：摘掉，不把 `</mind>` 本身露给她
                tags = _INNER_OPENS + _INNER_CLOSES
            # 找最早出现的那个标签（开标签有好几种写法，取最靠前的）
            hits = [(self.buf.find(g), g) for g in tags if self.buf.find(g) >= 0]
            if hits:
                i, tag = min(hits)
                self._emit(out, self.buf[:i])
                self.buf = self.buf[i + len(tag):]
                if self.inner:
                    self.inner = False
                    self.inner_text = ""
                    self.lstrip = True
                elif tag.startswith("</"):
                    pass                          # 落单的收口：丢掉
                else:
                    self.inner = True
                    self.close_tag = _close_of(tag)
                continue
            # 没找到完整标签：结尾可能是半个标签（流式切片常态），按所有候选里最长的半截留着
            keep = max(max(_partial_tail(self.buf, g) for g in tags), _audio_partial(self.buf))
            self._emit(out, self.buf[: len(self.buf) - keep])
            self.buf = self.buf[len(self.buf) - keep:]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        rest, self.buf = self.buf, ""
        self._emit(out, rest)
        # 没收口的心里话：整块留在 thinking（已在上面流式发过），不再补发正文。
        # 见类 docstring —— 2026-10-15 从「当正文补发」改过来
        self.inner, self.inner_text, self.lstrip = False, "", True
        return out


class MoodTagFilter:
    """挡住流式输出里**不该让她看见**的三类标记：

    1. [mood:xxx] —— 情绪标记（大小写不敏感；'[' 后的空白也容忍，
       [ mood:心疼 ] 在生产库漏过 —— 2026-09-22 她截图实锤）
    2. mood: xxx —— 模型偶尔不写方括号的变体（2026-09-06 截图实锤，行首）
    3. [开心] 等表情 tag —— 他不调 send_meme、直接把 tag 写进正文时的懒写法。
       **任何位置**都吞（2026-09-06 她报的：混在一段话里就降级成文字）——
       收尾时 nox.py 会从完整文本里把这些 tag 抽出来转成真正的表情事件，
       所以这里吞掉的不会丢，只是不作为文字出现。

    做法：见 '[' 或行首 'mood:' 就转入缓冲，确认不是标记后原样放行。
    代价是正文里的方括号、行首 m 开头的英文行会延迟几个字符显示。
    """

    _PREFIXES = ("[mood:", "[心情:", "[心情：")
    _MOOD_LINE_RE = re.compile(
        r"\s*(?:mood|心情)\s*[:：]\s*(" + "|".join(HER_EMOTIONS) + r")\s*[。\.]*\n?\s*",
        re.IGNORECASE,
    )
    #: '[' 缓冲里判「是不是情绪标记」用的键。中文键（心情:）是 glm
    #: 2026-10-08 新变体 —— `[心情: 撒娇]` 两个独立气泡出街，她截图实锤。
    _MOOD_KEYS = ("mood:", "心情:", "心情：")
    # 比最长的表情 tag + 括号还宽的缓冲直接放行 —— 不可能是 tag。
    # ⚠️ 原来写死 14：「where my kiss」加括号是 15，一写进正文就漏（09-29 补查到的）；
    # 呆猫八条里还有 10 个字的。跟着名单算，别再写死
    _MAX_TAG_BUF = max(len(t) for t in MEME_TAGS) + 3
    # 英文字母开头的方括号要多攒一点：`[intimacy 0.75/0.8 调侃中带真心]` 这种旁注能到 30 多字
    _MAX_STAGE_BUF = STAGE_TAG_MAX + 3
    #: `<mood>…</mood>` 最多攒这么长等收口（库里最长那段心里话 300 来字）
    _MAX_MOOD_XML = 2000
    #: 写成文字的表情标记前缀（见 feed 里那段）
    _MEME_LABEL = "[表情包"
    #: XML 假调用的开标签吞掉之后，最多等这么多字收口（</名字>）——
    #: 内文形态 `<send_meme>开心</send_meme>` 的内文很短；等不到就放弃，
    #: 后面是他的正文，不能陪葬（收尾 extract 会按开标签规则兜历史）
    _MAX_FAKE_XML_WAIT = 120

    def __init__(self, strip_stage: bool = False, contract_markers: tuple[str, ...] = ()) -> None:
        self._buf = ""        # '[' 开头的缓冲
        #: '<' / '【' 开头的缓冲：只认情绪标记（`<mood:平静>` deepseek 10-11 截图实锤 ——
        #: 不剥还被 Markdown 当成自动链接）。不是就原样放行，HTML / 普通尖括号不受影响
        self._abuf = ""
        #: 交互契约的 marker（agent/contracts.py，由 loop 从注册表取）：**行首**出现就整行吞 ——
        #: 和收尾的 contracts.apply 同一个判据，屏幕上看见的和落库的才对得上。
        #: `[表情包` 是 10-10 早上那条的写死前缀，契约注册前的老路，留着兜底
        self._contract_markers = tuple(contract_markers)
        self._buf_at_line_start = False
        self._line = None     # 行首 mood: 疑似行（None = 不在行缓冲）
        self._line_start = True
        self._meme_tags = frozenset(MEME_TAGS)
        #: 吞舞台标签（见 STAGE_TAG_RE）。**只在文字聊天开** —— 语音通话里
        #: [softly] 这些是给 TTS 的，必须原样送过去
        self._strip_stage = strip_stage
        #: 已闭合、像舞台标签的一段，等下一个字：是 `(` 就是 Markdown 链接，放行
        self._hold = ""
        #: 正在吞一行假工具调用（`[!send_meme] 晚安`，见 FAKE_CALL_RE），吞到换行为止
        self._eat_line = False
        #: 正在吞 XML 假调用的内文，等 `</名字>` 收口（`<send_meme tag="…">` 开标签
        #: 已吞、没自闭合时进入；见 FAKE_XML_RE）
        self._fake_close: str | None = None
        self._fake_buf = 0

    @staticmethod
    def _looks_like_fake_xml(low: str) -> bool:
        """尖括号缓冲去掉 '<' 后的小写正文，是不是（可能是）XML 假调用的开标签。"""
        if not low:
            return False
        return any(n.startswith(low) or low.startswith(n) for n in FAKE_XML_NAMES)

    def feed(self, chunk: str) -> str:
        """吃进增量，吐出可以安全显示的部分。"""
        out: list[str] = []
        for ch in chunk:
            # ── 正在吞 XML 假调用的内文：等 </名字> 收口
            if self._fake_close is not None:
                self._fake_buf += ch
                if self._fake_buf.endswith(self._fake_close):
                    self._fake_close, self._fake_buf = None, 0
                elif self._fake_buf > self._MAX_FAKE_XML_WAIT and ">" in self._fake_buf:
                    # 等不到收口（他没收口就接着说话）：放弃等待，恢复放行。
                    # 已吞的这段是内文/独白，收尾 extract 会把开标签从历史里剥掉
                    self._fake_close, self._fake_buf = None, 0
                continue
            if self._eat_line:
                if ch == "\n":
                    self._eat_line = False
                    self._line_start = True
                continue
            # ── 刚闭合的疑似舞台标签：看这一个字决定去留
            if self._hold:
                if ch == "(":
                    out.append(self._hold)          # `[text](url)`：链接，放行
                    self._hold = ""
                    out.append(ch)
                    self._line_start = False
                    continue
                self._hold = ""                     # 舞台标签：吞掉，这个字照常处理

            # ── '<' / '【' 缓冲：情绪标记（含 <[mood:]> 嵌套）+ XML 假调用开标签
            if self._abuf:
                self._abuf += ch
                body = self._abuf[1:].lstrip().lower()
                # <[mood:…]> 嵌套括号变体（2026-10-15 她截图实锤）：'<' 后跟 '['、
                # ']>' 收口 —— 单字符的 _OPEN/_CLOSE 在这里必然失配。判定时摘掉
                # 那层 '['，闭合条件相应变成 endswith(']>')
                nested = body.startswith("[")
                judge = body[1:].lstrip() if nested else body
                xml = next((n for n in MOOD_XML_NAMES if judge.startswith(n + ">")), None)
                if xml:
                    # `<mood>…</mood>`：短的是情绪标记、长的是心里话借了名字 —— 两种她都不该看见。
                    # 攒到收口整个吞掉（收尾的 strip_inner_voice 负责把短的换成 [mood:x] 更新情绪）
                    if self._abuf.lower().endswith("</" + xml + ">"):
                        self._abuf = ""
                    elif len(self._abuf) > self._MAX_MOOD_XML:
                        out.append(self._abuf)    # 攒太长还没收口：不是我们想的那个，还给她
                        self._abuf = ""
                    continue
                if any(judge.startswith(k) for k in self._MOOD_KEYS):
                    # 确认是情绪标记，吃掉直到闭合。单层收口是 '>' / '】'
                    # （`<mood:平静>` 10-11 修过的那种），嵌套是 ']>'
                    if (not nested and ch in ">】") or (nested and self._abuf.endswith("]>")):
                        self._abuf = ""           # 情绪标记：整个吞掉
                    continue
                fake = self._looks_like_fake_xml(body)
                may_continue = (
                    any(k.startswith(judge) for k in self._MOOD_KEYS)
                    or (nested and not judge)
                    or (fake and ch not in (">", chr(10)))
                )
                if may_continue:
                    continue                      # 还可能是，继续攒
                if ch == ">" and fake:
                    # XML 假调用的开标签收口了（`<send_meme tag="笨爪开心">`）：
                    # 整个吞掉，不让她看见。没自闭合就等 </名字>，内文也是参数
                    opener = self._abuf[1:].rstrip()
                    self_closed = opener.endswith("/")
                    name = opener.rstrip("/").strip().split(" ", 1)[0].lower()
                    self._abuf = ""
                    if name in FAKE_XML_NAMES and not self_closed:
                        self._fake_close = "</" + name + ">"
                        self._fake_buf = 0
                    self._line_start = ch == chr(10)
                    continue
                out.append(self._abuf)            # 不是：原样放行
                self._abuf = ""
                self._line_start = ch == chr(10)
                continue

            # ── 行缓冲：行首 mood: 疑似行，攒到换行统一判定
            if self._line is not None:
                self._line += ch
                if ch == "\n":
                    resolved = self._resolve_line()
                    if resolved:
                        out.append(resolved)
                    self._line = None
                    self._line_start = True
                continue

            # ── '[' 缓冲：[mood: 或 [tag 或普通方括号
            if self._buf:
                self._buf += ch
                # 假工具调用 `[!名字]…`：确认是字母开头就整行吞掉（`[!!!]` 这种感叹不算）
                if self._buf.startswith("[!"):
                    rest = self._buf[2:].lstrip(" \t")
                    if not rest:
                        continue
                    if rest[0].isascii() and (rest[0].isalpha() or rest[0] == "_"):
                        self._buf = ""
                        self._eat_line = ch != "\n"
                        continue
                # `[表情包] 举爪开心`：他把表情写成了文字（2026-10-10 她截图）。认出标记就整行吞；
                # 收尾时 nox.py 的 extract_text_tags 会从完整正文里抽出名字转成真表情
                if self._buf.startswith(self._MEME_LABEL) or (
                    self._buf_at_line_start
                    and any(self._buf.startswith(m) for m in self._contract_markers)
                ):
                    self._buf = ""
                    self._eat_line = ch != "\n"
                    continue
                lowered = self._buf.lower()
                # 🔴 判定前剥掉 '[' 后的空白 —— `[ mood:心疼 ]` 这种带空格
                #    变体曾整个漏到她眼前（2026-09-22 截图实锤）
                body = lowered[1:].lstrip()
                if any(body.startswith(k) for k in self._MOOD_KEYS):
                    # 确认是情绪标记，吃掉直到闭合
                    if ch == "]":
                        self._buf = ""
                    continue
                if any(k.startswith(body) for k in self._MOOD_KEYS):
                    continue          # 还可能是，继续缓冲（含 '[' 后的空白）
                if ch == "]":
                    tag = self._buf[1:-1].strip()
                    if tag in self._meme_tags:
                        self._buf = ""            # 表情 tag：吞掉
                    elif self._strip_stage and STAGE_TAG_RE.fullmatch(self._buf):
                        self._hold = self._buf    # 舞台标签：先扣下，看下一个字是不是 `(`
                        self._buf = ""
                    else:
                        out.append(self._buf)     # 普通方括号：放行
                        self._buf = ""
                        self._line_start = False
                    continue
                stage_like = self._strip_stage and _STAGE_START.match(self._buf)
                if len(self._buf) > (self._MAX_STAGE_BUF if stage_like else self._MAX_TAG_BUF):
                    out.append(self._buf)
                    self._buf = ""
                    self._line_start = self._buf.endswith("\n")
                continue

            if ch == "[":
                self._buf = ch
                self._buf_at_line_start = self._line_start
                self._line_start = False
                continue
            if ch in "<【":
                self._abuf = ch
                self._line_start = False
                continue

            # ── 普通字符：行首 m/M/心 进 mood 行缓冲（"me too" 这类整行
            #    缓冲到换行再放行，无损只是慢一拍；「心」管中文键
            #    「心情: 撒娇」，正常「心里…」开头的句子缓冲后原样放行）
            if self._line_start and (ch in "mM" or ch == "心"):
                self._line = ch
                self._line_start = False
                continue

            out.append(ch)
            self._line_start = ch == "\n"
        return "".join(out)

    def _resolve_line(self) -> str:
        """整行收齐了：是 mood 变体行就吞，否则原样放行。"""
        return "" if self._MOOD_LINE_RE.match(self._line) else self._line

    def flush(self) -> str:
        """流结束时把剩下的放出去（确定是标记的除外）。"""
        parts: list[str] = []
        self._hold = ""       # 流到头了，后面没有 `(` —— 舞台标签，吞掉
        self._eat_line = False
        if self._fake_close is not None:
            # 等假调用收口等到流结束：整段吞掉（历史那侧由收尾 extract 兜底）
            self._fake_close, self._fake_buf = None, 0
        if self._line is not None:
            resolved = self._resolve_line()
            if resolved:
                parts.append(resolved)
            self._line = None
        if self._abuf:
            body = self._abuf[1:].lstrip().lower()
            nested = body.startswith("[")
            core = body[1:].lstrip() if nested else body
            xml = next((n for n in MOOD_XML_NAMES if core.startswith(n + ">")), None)
            if xml:
                parts.append(self._abuf[len(xml) + 2:])   # 没收口：摘标签留内容（同 strip_inner_voice）
            elif nested or self._looks_like_fake_xml(body):
                pass   # <[mood:… / 假调用开标签没收口：情绪标记与假调用家族，吞掉不漏
            elif not any(core.startswith(k) for k in self._MOOD_KEYS):
                parts.append(self._abuf)          # 半截的普通尖括号：还给她
            self._abuf = ""
        if self._buf:
            if not any(self._buf.lower().startswith(p) for p in self._PREFIXES):
                parts.append(self._buf)
            self._buf = ""
        return "".join(parts)


class LineDraftFilter:
    """流式里按**整行**拦交互契约的伪输出（只在文字聊天里用，见 contracts.OutputContract.line_detector）。

    10-10 晚：他在正文里先写一段带 [softly] 的英文「语音草稿」再调 send_voice_message ——
    这种行没有 marker，看到行尾才知道是不是。所以**英文或方括号开头的行**先攒到换行再判：
    是草稿就整行吞掉，不是就原样放出去。中文开头的行照常一个字一个字地流，不受影响。
    """

    def __init__(self, detectors: list) -> None:
        self._dets = list(detectors)
        self._buf = ""
        self._holding = False
        self._line_start = True

    def _is_draft(self, line: str) -> bool:
        return any(d(line) for d in self._dets)

    def feed(self, chunk: str) -> str:
        out: list[str] = []
        for ch in chunk:
            if self._holding:
                self._buf += ch
                if ch == "\n":
                    if not self._is_draft(self._buf[:-1]):
                        out.append(self._buf)
                    self._buf, self._holding, self._line_start = "", False, True
                continue
            if self._line_start and ch.isascii() and (ch.isalpha() or ch == "["):
                self._holding, self._buf, self._line_start = True, ch, False
                continue
            out.append(ch)
            if ch == "\n":
                self._line_start = True
            elif not ch.isspace():
                self._line_start = False
        return "".join(out)

    def flush(self) -> str:
        held = self._buf if self._holding and not self._is_draft(self._buf) else ""
        self._buf, self._holding, self._line_start = "", False, True
        return held


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
