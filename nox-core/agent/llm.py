"""LLM 适配层 —— loop 与具体厂商之间的唯一接口。

设计原则：**各家的差异全压在这一层，loop 一个字都不用改。**

loop 只认识下面三个数据类和一个 complete() 方法。它永远不知道
temperature、thinking、effort、budget_tokens 这些词的存在 —— 那些是
各家自己的方言，由对应 adapter 翻译。
"""

from __future__ import annotations

import json
import re
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
    """

    type: Literal["text", "split", "done", "tool_start", "tool_end"]
    text: str = ""
    turn: Turn | None = None
    #: 工具名。只有 tool_start / tool_end 用
    tool: str = ""
    #: 这次调用成没成。只有 tool_end 用（失败也要报 —— 她该知道他没做成）
    ok: bool = True


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
)

# 糖糖的情绪词（mood 判定用；personality/mood.py 也 import 这份）
HER_EMOTIONS = ("开心", "难过", "烦躁", "撒娇", "兴奋", "疲惫", "平静")


class MoodTagFilter:
    """挡住流式输出里**不该让她看见**的三类标记：

    1. [mood:xxx] —— 情绪标记（大小写不敏感，[Mood: 在生产库漏过 3 次）
    2. mood: xxx —— 模型偶尔不写方括号的变体（2026-09-06 截图实锤，行首）
    3. [开心] 等表情 tag —— 他不调 send_meme、直接把 tag 写进正文时的懒写法。
       **任何位置**都吞（2026-09-06 她报的：混在一段话里就降级成文字）——
       收尾时 nox.py 会从完整文本里把这些 tag 抽出来转成真正的表情事件，
       所以这里吞掉的不会丢，只是不作为文字出现。

    做法：见 '[' 或行首 'mood:' 就转入缓冲，确认不是标记后原样放行。
    代价是正文里的方括号、行首 m 开头的英文行会延迟几个字符显示。
    """

    _PREFIX = "[mood:"
    _MOOD_LINE_RE = re.compile(
        r"\s*mood\s*[:：]\s*(" + "|".join(HER_EMOTIONS) + r")\s*[。\.]*\n?\s*",
        re.IGNORECASE,
    )
    # 比最长的 tag（5 字）+ 括号还宽的缓冲直接放行 —— 不可能是 tag
    _MAX_TAG_BUF = 14

    def __init__(self) -> None:
        self._buf = ""        # '[' 开头的缓冲
        self._line = None     # 行首 mood: 疑似行（None = 不在行缓冲）
        self._line_start = True
        self._meme_tags = frozenset(MEME_TAGS)

    def feed(self, chunk: str) -> str:
        """吃进增量，吐出可以安全显示的部分。"""
        out: list[str] = []
        for ch in chunk:
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
                lowered = self._buf.lower()
                if lowered.startswith(self._PREFIX):
                    # 确认是情绪标记，吃掉直到闭合
                    if ch == "]":
                        self._buf = ""
                    continue
                if self._PREFIX.startswith(lowered):
                    continue          # 还可能是，继续缓冲
                if ch == "]":
                    tag = self._buf[1:-1].strip()
                    if tag in self._meme_tags:
                        self._buf = ""            # 表情 tag：吞掉
                    else:
                        out.append(self._buf)     # 普通方括号：放行
                        self._buf = ""
                        self._line_start = False
                    continue
                if len(self._buf) > self._MAX_TAG_BUF:
                    out.append(self._buf)
                    self._buf = ""
                    self._line_start = self._buf.endswith("\n")
                continue

            if ch == "[":
                self._buf = ch
                self._line_start = False
                continue

            # ── 普通字符：行首 m/M 进 mood 行缓冲（"me too" 这类整行
            #    缓冲到换行再放行，无损只是慢一拍）
            if self._line_start and ch in "mM":
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
        if self._line is not None:
            resolved = self._resolve_line()
            if resolved:
                parts.append(resolved)
            self._line = None
        if self._buf:
            if not self._buf.lower().startswith(self._PREFIX):
                parts.append(self._buf)
            self._buf = ""
        return "".join(parts)


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
