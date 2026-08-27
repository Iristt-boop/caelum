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


def _adapter(base_url: str) -> OpenAICompatAdapter:
    """造一个 adapter 但不真连网 —— 我们只看它拼出来的消息数组。"""
    cfg = LLMConfig(
        provider="x", api_key="k", base_url=base_url,
        model="m", max_tokens=16, timeout=5, max_retries=0,
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


class Test有断点的后端:
    """OpenRouter 那条：靠 cache_control，动态块跟在断点之后就行。"""

    def test_走断点_不重复插尾部(self):
        a = _adapter("https://openrouter.ai/api/v1")
        assert a._supports_cache is True
        native = _native(a, HIST)
        #: 动态块在 system 的第二个 block 里，不该在消息数组里再来一条
        assert sum(1 for m in native if m.get("content") == DYN) == 0
        blocks = native[0]["content"]
        assert blocks[0]["text"] == SYS
        assert blocks[0]["cache_control"]["type"] == "ephemeral"
        assert blocks[1]["text"] == DYN
