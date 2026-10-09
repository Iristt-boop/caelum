"""🔴 请求里各段的**位置**，决定了缓存命不命中。

## 为什么单独一个文件测「位置」

DeepSeek 走的是**自动前缀缓存** —— 没有 `cache_control` 断点，
匹配的是整条请求的最长公共前缀。所以位置就是一切：

```text
system(静态) + 动态 + 历史 + 用户   →  动态一变，历史全废
system(静态) + 历史 + 动态 + 用户   →  动态一变，只有尾巴重算
```

2026-08-26 实测（9,138 token 的请求，只改动态块那一句话）：

```text
拼进 system   命中 0      （0%）
放到尾部      命中 9,088  （99%）
```

`context/base.py` 开头那条约束（「渲染结果只能进 dynamic_system，
永远不许进 system」）本来就是为这个写的 —— 但只在 OpenRouter 那条路上
兑现了，DeepSeek 这条把它拼了回去，等于那条约束白写了不知道多久。

**这种错不会报任何异常，只会让账单悄悄翻倍。** 所以要有测试盯着。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.adapters import OpenAICompatAdapter  # noqa: E402
from agent.llm import Message, ToolCall, ToolResult  # noqa: E402
from config import LLMConfig  # noqa: E402

SYS = "静态系统提示"
DYN = "现在 14 点，她心情不错"


def _adapter(base_url: str, model: str = "m") -> OpenAICompatAdapter:
    """造一个 adapter 但不真连网 —— 我们只看它拼出来的消息数组。"""
    cfg = LLMConfig(
        provider="x", api_key="k", base_url=base_url,
        model=model, max_tokens=16, timeout=5, max_retries=0,
    )
    return OpenAICompatAdapter(cfg)


class _Spy:
    """假客户端：把 adapter 真正准备发出去的 kwargs 截下来。

    🔴 **不许在测试里手抄一遍拼装逻辑。**

    这个文件第一版就是那么写的 —— `_native()` 里复制了一份
    `complete()` 的组装代码。结果是：真代码里那个
    `insert(len-1)` 的 bug（把动态块插进 tool_calls 和结果中间，
    线上 400）**测试完全测不到**，因为测试测的是副本。

    现在走真的 `complete()`，只把最后那次网络调用换掉。
    """

    def __init__(self) -> None:
        self.kwargs: dict = {}
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.kwargs = kw
                raise RuntimeError("到此为止，我们只要 kwargs")

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _native(a: OpenAICompatAdapter, msgs, dynamic=DYN):
    """跑真的 `complete()`，返回它拼出来的 messages 数组。"""
    spy = _Spy()
    a._client = spy          # type: ignore[assignment]
    a.complete(list(msgs), [], system=SYS, dynamic_system=dynamic)
    return spy.kwargs["messages"]


def _native_stream(a: OpenAICompatAdapter, msgs, dynamic=DYN):
    """跑真的 `stream()`（主聊天走的是它），返回它拼出来的 messages 数组。"""
    spy = _Spy()
    a._client = spy          # type: ignore[assignment]
    list(a.stream(list(msgs), [], system=SYS, dynamic_system=dynamic))
    return spy.kwargs["messages"]


HIST = [
    Message(role="user", text="第一句"),
    Message(role="assistant", text="第二句"),
    Message(role="user", text="现在这句"),
]


class Test自动前缀缓存的后端:
    """DeepSeek 这类：没有断点，位置决定一切。"""

    def test_静态_system_里不许有动态内容(self):
        a = _adapter("https://api.deepseek.com")
        assert a._supports_cache is False
        sys_msg = _native(a, HIST)[0]
        assert sys_msg["content"] == SYS
        assert DYN not in sys_msg["content"], "动态内容混进静态前缀了"

    def test_动态块在尾巴上(self):
        a = _adapter("https://api.deepseek.com")
        native = _native(a, HIST)
        #: 倒数第二条 —— 排在用户这句话前面
        assert native[-2] == {"role": "system", "content": DYN}
        assert native[-1]["content"] == "现在这句"

    def test_动态块变了_前缀一个字都不变(self):
        """🔴 这条就是那 0% vs 99% 的差别。"""
        a = _adapter("https://api.deepseek.com")
        one = _native(a, HIST, dynamic="现在 14 点")
        two = _native(a, HIST, dynamic="现在 15 点，她有点累")
        #: 除了动态块那一条，其余全部逐字相同
        assert one[:-2] == two[:-2], "动态块之前的内容不该受影响"
        assert one[-1] == two[-1]
        assert one[-2] != two[-2]

    def test_没有动态内容时不插空消息(self):
        a = _adapter("https://api.deepseek.com")
        native = _native(a, HIST, dynamic=None)
        assert all(m["content"] != "" for m in native)
        assert len(native) == 1 + len(HIST)

    def test_历史越长_稳定前缀越长(self):
        """历史全在动态块之前 —— 聊得越久，能命中的越多。"""
        a = _adapter("https://api.deepseek.com")
        long_hist = [Message(role="user", text=f"第{i}句") for i in range(50)]
        native = _native(a, long_hist)
        # 动态块在倒数第二，说明前面 50 条全在稳定前缀里
        assert native.index({"role": "system", "content": DYN}) == len(native) - 2


class Test工具循环里不许插坏配对:
    """🔴 2026-08-27 线上 400 的复现。

    第一版把动态块插在「倒数第二条」。工具循环里最后两条是
    `assistant(tool_calls)` + `tool(结果)` —— 于是它正好插进了中间，
    把配对切断了：

    ```text
    assistant(tool_calls)
    system(动态块)        ← 插错地方
    tool(结果)
    ```

    DeepSeek 直接 400：

        An assistant message with 'tool_calls' must be followed by
        tool messages responding to each 'tool_call_id'

    表现是糖糖问了句话，他答「我这会儿连不上」—— 而工具其实**执行成功了**，
    审计里两条都是 ok=True。错在最后那次回话的请求上。
    """

    @staticmethod
    def _loop_msgs():
        """一轮工具调用之后的消息形状。"""
        return [
            Message(role="user", text="历史问题"),
            Message(role="assistant", text="历史回答"),
            Message(role="user", text="看一下 git 状态"),
            Message(role="tool_calls", tool_calls=[
                ToolCall(id="c1", name="computer_git_status", arguments={})]),
            Message(role="tool_results", tool_results=[
                ToolResult(call_id="c1", content="## master")]),
        ]

    def test_动态块不插进_tool_calls_和结果中间(self):
        a = _adapter("https://api.deepseek.com")
        native = _native(a, self._loop_msgs())
        roles = [m.get("role") for m in native]
        #: 找到带 tool_calls 的那条，它后面必须紧跟 tool
        for i, m in enumerate(native):
            if m.get("tool_calls"):
                assert native[i + 1].get("role") == "tool", (
                    f"配对被切断了：{roles}")

    def test_动态块在最后一条_user_之前(self):
        a = _adapter("https://api.deepseek.com")
        native = _native(a, self._loop_msgs())
        i = next(j for j, m in enumerate(native)
                 if m.get("role") == "system" and m.get("content") == DYN)
        assert native[i + 1]["content"] == "看一下 git 状态"

    def test_循环里位置稳定_所以缓存不掉(self):
        """🔴 循环每轮追加 assistant/tool，而动态块的位置不动 ——
        前缀逐轮不变，缓存才命中。"""
        a = _adapter("https://api.deepseek.com")
        one = _native(a, self._loop_msgs())
        # 再来一轮工具调用
        more = self._loop_msgs() + [
            Message(role="tool_calls", tool_calls=[
                ToolCall(id="c2", name="computer_git_log", arguments={})]),
            Message(role="tool_results", tool_results=[
                ToolResult(call_id="c2", content="abc 提交")]),
        ]
        two = _native(a, more)
        #: 前面那一段（到动态块 + user 为止）逐字相同
        i = next(j for j, m in enumerate(one)
                 if m.get("role") == "system" and m.get("content") == DYN)
        assert one[: i + 2] == two[: i + 2]


CLAUDE = "anthropic/claude-haiku-5.5"
OR = "https://openrouter.ai/api/v1"


def _marked(native):
    """所有带 cache_control 的 (消息下标, block)。"""
    out = []
    for i, m in enumerate(native):
        if isinstance(m.get("content"), list):
            for blk in m["content"]:
                if "cache_control" in blk:
                    out.append((i, blk))
    return out


class Test有断点的后端:
    """OpenRouter → Claude：显式断点。

    🔴 2026-10-09 实测（Haiku 5.5，同一个请求连发两轮、第二轮多一对历史、动态块变了）：

    ```text
    动态块在 system 第二个 block（旧）   第二轮命中 18,808 / 42,486 = 44%   $0.00256
    动态块挪到尾部 + 历史加断点（新）    第二轮命中 36,536 / 42,488 = 86%   $0.00112
    ```

    旧布局里动态块每轮一变，它后面的整段对话历史永远命不中，只有 system 那一块在缓存。
    """

    def test_system_只有静态块_带断点(self):
        native = _native(_adapter(OR, CLAUDE), HIST)
        blocks = native[0]["content"]
        assert [b["text"] for b in blocks] == [SYS], "动态块不许再待在 system 里"
        assert blocks[0]["cache_control"]["type"] == "ephemeral"

    def test_anthropic_型号用_1h_档(self):
        native = _native(_adapter(OR, CLAUDE), HIST)
        assert native[0]["content"][0]["cache_control"]["ttl"] == "1h"
        assert all(blk["cache_control"].get("ttl") == "1h" for _, blk in _marked(native))

    def test_环境变量能退回_5m_档(self, monkeypatch):
        monkeypatch.setenv("NOX_OR_CACHE_TTL", "5m")
        native = _native(_adapter(OR, CLAUDE), HIST)
        assert all("ttl" not in blk["cache_control"] for _, blk in _marked(native))

    def test_别家型号不带_ttl(self):
        """经 OpenRouter 的非 Anthropic 后端不一定认 ttl 字段，宁可不带。"""
        native = _native(_adapter(OR, "google/gemini-x"), HIST)
        assert _marked(native), "断点还是要打"
        assert all("ttl" not in blk["cache_control"] for _, blk in _marked(native))

    def test_动态块在最后一条_user_之前_不在_system(self):
        native = _native(_adapter(OR, CLAUDE), HIST)
        assert native[-2] == {"role": "system", "content": DYN}
        assert native[-1]["content"] == "现在这句"

    def test_历史末尾有断点_而最后那句不带(self):
        native = _native(_adapter(OR, CLAUDE), HIST)
        marks = _marked(native)
        #: system 一个 + 历史最后一条（assistant「第二句」）一个，共两个
        assert [i for i, _ in marks] == [0, 2]
        assert native[2]["role"] == "assistant"
        assert native[2]["content"][0]["text"] == "第二句"
        assert native[-1]["content"] == "现在这句", "当前这句每轮都变，不该是断点"

    def test_动态块变了_断点之前一个字都不变(self):
        """🔴 这条就是 44% 和 86% 的差别。"""
        a = _adapter(OR, CLAUDE)
        one = _native(a, HIST, dynamic="现在 14 点")
        two = _native(a, HIST, dynamic="现在 15 点，她有点累")
        assert one[:-2] == two[:-2]
        assert one[-2] != two[-2]

    def test_历史变长后_上一轮的断点位置仍是新前缀的一部分(self):
        """下一轮多了一对 user/assistant，上一轮打断点的那条消息要原样留在前缀里。"""
        a = _adapter(OR, CLAUDE)
        one = _native(a, HIST)
        more = HIST[:-1] + [Message(role="user", text="现在这句"), Message(role="assistant", text="好呀"),
                            Message(role="user", text="再来一句")]
        two = _native(a, more)
        # 断点标记本身不算缓存内容，比的是文字（2026-10-09 实测：断点往后移一格，前缀照样命中）
        def plain(m):
            c = m["content"]
            return c[0]["text"] if isinstance(c, list) else c
        prefix = [(m["role"], plain(m)) for m in one[:3]]
        assert prefix == [(m["role"], plain(m)) for m in two[:3]], "上一轮断点之前的内容必须逐字不变"

    def test_第一轮没有历史_不报错也不乱打断点(self):
        native = _native(_adapter(OR, CLAUDE), [Message(role="user", text="你好")])
        assert [i for i, _ in _marked(native)] == [0]

    def test_没有动态内容时不插空消息_断点照打(self):
        native = _native(_adapter(OR, CLAUDE), HIST, dynamic=None)
        assert len(native) == 1 + len(HIST)
        assert [i for i, _ in _marked(native)] == [0, 2]

    def test_工具循环里_配对不被切断_断点不打在_tool_上(self):
        a = _adapter(OR, CLAUDE)
        native = _native(a, Test工具循环里不许插坏配对._loop_msgs())
        for i, m in enumerate(native):
            if m.get("tool_calls"):
                assert native[i + 1].get("role") == "tool"
        for i, _ in _marked(native):
            assert native[i].get("role") in ("system", "user", "assistant")
            assert not native[i].get("tool_calls")

    def test_stream_和_complete_拼出同一份(self):
        """🔴 主聊天走的是 stream()。只改一边，账单上才看得出来，而且没有任何报错。"""
        a = _adapter(OR, CLAUDE)
        assert _native_stream(a, HIST) == _native(a, HIST)
        loop = Test工具循环里不许插坏配对._loop_msgs()
        assert _native_stream(a, loop) == _native(a, loop)
        d = _adapter("https://api.deepseek.com")
        assert _native_stream(d, HIST) == _native(d, HIST)

    def test_deepseek_这条不受影响_没有任何断点(self):
        native = _native(_adapter("https://api.deepseek.com"), HIST)
        assert _marked(native) == []
        assert all(isinstance(m["content"], str) for m in native)
