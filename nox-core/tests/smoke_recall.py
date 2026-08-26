"""验证记忆切分：核心准则和动态浮现要分得开，且比例合理。

    .venv\\Scripts\\python.exe tests\\smoke_recall.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.ob_client import OmbreBrain  # noqa: E402

URL = "https://noxtang.com/ombre/mcp"


def main() -> int:
    ob = OmbreBrain(URL, timeout=25.0)

    print("--- 自动浮现（不带 query）---")
    r = ob.recall(max_results=8, max_tokens=4000)
    if not r.ok:
        print(f"失败: {r.error}")
        return 1

    print(f"  核心准则: {len(r.core):>6} 字符  ← 每轮不变，该进缓存段")
    print(f"  动态浮现: {len(r.dynamic):>6} 字符  ← 每轮变化，老实付钱")
    total = len(r.core) + len(r.dynamic)
    if total:
        print(f"  核心占比: {len(r.core) / total:.0%}")

    print("\n--- 带 query 检索：Stack-chan ---")
    r2 = ob.recall("Stack-chan 表情", max_results=5, max_tokens=2500)
    if r2.ok:
        print(f"  核心准则: {len(r2.core):>6} 字符（应与上面基本一致）")
        print(f"  动态浮现: {len(r2.dynamic):>6} 字符（应该换成相关内容了）")
        print("\n  动态部分前几行：")
        for line in r2.dynamic.splitlines()[:6]:
            print("    " + line[:96])
    else:
        print(f"  失败: {r2.error}")

    print("\n--- 省钱估算（按 Sonnet 5 输入 $3/MTok、缓存命中 $0.30/MTok）---")
    core_tok = len(r.core)  # 中文粗估 1 字符 ≈ 1 token
    print(f"  核心准则约 {core_tok} tokens")
    print(f"  每轮当新内容发: ${core_tok * 3 / 1_000_000:.4f}")
    print(f"  每轮缓存命中  : ${core_tok * 0.3 / 1_000_000:.4f}")
    print(f"  按每天 50 轮，一个月省: ${core_tok * 2.7 / 1_000_000 * 50 * 30:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
