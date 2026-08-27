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
from agent.llm import Message  # noqa: E402
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


def _native(a: OpenAICompatAdapter, msgs, dynamic=DYN):
    """拿到它准备发出去的消息数组（复刻 complete 里的拼装）。"""
    native = []
    if SYS:
        native.append(a._system_message(SYS, dynamic))
    elif dynamic and a._supports_cache:
        native.append({"role": "system", "content": dynamic})
    for m in msgs:
        native.extend(a._to_native(m))
    if not a._supports_cache:
        tail = a._tail_block(dynamic)
        if tail is not None:
            native.insert(max(0, len(native) - 1), tail)
    return native


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
