"""端到端验证两条路径的实际差距。

跑三句：闲聊 → 正事 → 闲聊，看 token 与耗时的对比。

    .venv\\Scripts\\python.exe tests\\smoke_router.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.WARNING)

from agent.llm import Message  # noqa: E402
from nox import Nox  # noqa: E402

CASES = [
    "在吗",
    "还记得 Stack-chan 第一次开口说话是什么时候吗",
    "晚安",
]


def main() -> int:
    try:
        nox = Nox()
    except RuntimeError as exc:
        print(f"启动失败：{exc}")
        return 1

    print(f"静态前缀 {len(nox.system_prompt)} 字符 | 工具 {list(nox.loop.tools)}\n")
    print(f"{'输入':<24}{'路径':<6}{'耗时':>7}{'输入tok':>9}{'命中':>8}{'输出tok':>8}")
    print("-" * 64)

    history: list[Message] = []
    rows = []

    for text in CASES:
        t0 = time.time()
        r = nox.chat(text, history)
        dt = time.time() - t0
        history = r.messages
        u = r.result.usage
        path = "轻量" if r.decision.light else "完整"
        rows.append((path, u.input_tokens, dt))
        print(
            f"{text:<24}{path:<6}{dt:>6.1f}s{u.input_tokens:>9}"
            f"{u.cache_read_tokens:>8}{u.output_tokens:>8}"
        )
        print(f"  → {(r.text or '')[:70]}")

    light = [r for r in rows if r[0] == "轻量"]
    full = [r for r in rows if r[0] == "完整"]
    print("\n" + "-" * 64)
    if light and full:
        li = sum(r[1] for r in light) / len(light)
        fu = sum(r[1] for r in full) / len(full)
        lt = sum(r[2] for r in light) / len(light)
        ft = sum(r[2] for r in full) / len(full)
        print(f"轻量平均: {li:>7.0f} tok  {lt:.1f}s")
        print(f"完整平均: {fu:>7.0f} tok  {ft:.1f}s")
        if fu:
            print(f"轻量路径省下: {(1 - li / fu):.0%} 的输入 token")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
