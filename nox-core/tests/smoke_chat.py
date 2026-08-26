"""端到端冒烟：真实调 LLM，走完整 loop。

两句话覆盖两条路径：
  1. 闲聊     —— 不该调任何工具，验证「轻量」这个目标真的成立
  2. 问记忆   —— 该调 recall_memory，验证工具链和 guard

    .venv\\Scripts\\python.exe tests\\smoke_chat.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s | %(message)s")

from agent.llm import Message  # noqa: E402
from nox import Nox  # noqa: E402


def show(label: str, result, elapsed: float) -> None:
    print(f"\n{'=' * 60}")
    print(f"【{label}】{elapsed:.1f}s | 结局={result.outcome} | 轮数={result.iterations}")
    print("=" * 60)
    if result.text:
        print(result.text)
    if result.detail:
        print(f"[detail] {result.detail}")

    called = [
        c.name
        for m in result.messages
        if m.role == "assistant"
        for c in m.tool_calls
    ]
    print(f"\n调用的工具: {called or '（一个都没调）'}")

    u = result.usage
    print(
        f"token: 输入 {u.input_tokens} | 输出 {u.output_tokens} | "
        f"缓存命中 {u.cache_read_tokens} | 缓存写入 {u.cache_write_tokens}"
    )


def main() -> int:
    try:
        nox = Nox()
    except RuntimeError as exc:
        print(f"启动失败：{exc}")
        return 1

    print(f"\n静态前缀 {len(nox.system_prompt)} 字符 | 工具 {list(nox.loop.tools)}")

    history: list[Message] = []

    # --- 第一句：闲聊，不该碰记忆 ---
    t0 = time.time()
    r1 = nox.chat("在吗", history)
    show("闲聊：在吗", r1, time.time() - t0)
    history = r1.messages

    # --- 第二句：该去查记忆 ---
    t0 = time.time()
    r2 = nox.chat("还记得 Stack-chan 第一次开口说话是什么时候吗", history)
    show("问记忆：Stack-chan 第一次说话", r2, time.time() - t0)

    print(f"\n{'=' * 60}")
    ok = r1.ok and r2.ok
    print("✅ 两句都答上了" if ok else "⚠ 有失败，看上面的 detail")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
