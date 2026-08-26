"""连通性冒烟：试着连上 Ombre Brain 并浮现一次记忆。

不是单元测试 —— 它要打真实网络，手动跑：
    .venv\\Scripts\\python.exe tests\\smoke_ob.py [url]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.ob_client import OmbreBrain  # noqa: E402

CANDIDATES = [
    "https://noxtang.com/ombre/mcp",
    "https://noxtang.com/ombre/",
    "http://47.84.92.71:8002/mcp",
]


def try_url(url: str) -> bool:
    print(f"\n--- 试 {url} ---")
    ob = OmbreBrain(url, timeout=20.0)

    r = ob.pulse()
    if not r.ok:
        print(f"  pulse 失败: {r.error}")
        return False

    print("  pulse 成功，记忆系统状态：")
    for line in r.text.splitlines()[:6]:
        print("    " + line)

    b = ob.breath(max_results=3, max_tokens=800)
    if not b.ok:
        print(f"  breath 失败: {b.error}")
        return False

    print(f"\n  breath 成功，浮现了 {len(b.text)} 字符：")
    for line in b.text.splitlines()[:8]:
        print("    " + line[:100])
    return True


if __name__ == "__main__":
    urls = [sys.argv[1]] if len(sys.argv) > 1 else CANDIDATES
    for u in urls:
        if try_url(u):
            print(f"\n✅ 可用地址：{u}")
            sys.exit(0)
    print("\n❌ 所有候选地址都连不上")
    sys.exit(1)
