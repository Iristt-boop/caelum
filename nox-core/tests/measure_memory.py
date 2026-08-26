"""量 Nox Core 的常驻内存 —— 这个数字决定 VPS 升到几 G。

一个进程内分阶段加载并即时采样。

Windows 上读 WorkingSet64，Linux 上读 /proc/self/status 的 VmRSS ——
部署到 VPS 后同一个脚本能直接跑，数字可比。

    .venv\\Scripts\\python.exe tests\\measure_memory.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _rss_mb_windows() -> float:
    import ctypes

    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_uint32),
            ("PageFaultCount", ctypes.c_uint32),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    # 关键：必须声明 restype，否则句柄会被当成 32 位 int 截断，
    # 调用静默失败、读数恒为 0（第一版就栽在这儿）
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(PMC),
        ctypes.c_uint32,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int

    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    if not psapi.GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb
    ):
        raise OSError("GetProcessMemoryInfo 调用失败")
    return pmc.WorkingSetSize / 1024 / 1024


def _rss_mb_linux() -> float:
    with open("/proc/self/status", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return 0.0


def rss_mb() -> float:
    return _rss_mb_windows() if sys.platform == "win32" else _rss_mb_linux()


def main() -> int:
    rows: list[tuple[str, float]] = []

    def mark(label: str) -> None:
        rows.append((label, rss_mb()))

    mark("裸解释器")

    import anthropic  # noqa: F401
    import openai  # noqa: F401

    mark("+ anthropic / openai")

    import fastapi  # noqa: F401
    import mcp  # noqa: F401

    mark("+ mcp / fastapi")

    from agent.loop import AgentLoop
    from memory import tools as mtools
    from memory.ob_client import OmbreBrain
    from personality import prompt as personality

    mark("+ nox-core 模块")

    ob = OmbreBrain("https://noxtang.com/ombre/mcp", timeout=25.0)
    core = ob.core_principles()
    prefix = personality.build(core.text if core.ok else "")
    loop = AgentLoop(adapter=None)  # type: ignore[arg-type]
    mtools.register_all(loop, ob)

    mark("+ 核心准则与工具")

    print(f"{'阶段':<24}{'常驻':>9}{'增量':>9}")
    print("-" * 42)
    prev = 0.0
    for label, mb in rows:
        delta = mb - prev
        print(f"{label:<24}{mb:>7.1f} MB{delta:>+7.1f} MB")
        prev = mb

    print()
    print(f"静态前缀 {prefix.size} 字符 | 工具 {len(loop.tools)} 个 | "
          f"核心准则 {'已载入' if core.ok else '取不到（' + str(core.error) + '）'}")
    print(f"\n>>> 常驻约 {rows[-1][1]:.0f} MB")
    print(f">>> 加 uvicorn worker 与并发缓冲，线上按 {rows[-1][1] * 1.5:.0f} MB 估")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
