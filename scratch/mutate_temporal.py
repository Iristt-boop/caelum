"""变异测试：把「昨晚」和「一会」的修法挨个改坏，确认对应的检查会红。

    PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python -u scratch/mutate_temporal.py

🔴 变异前断言锚点唯一；子进程限时（某些变异会让测试挂住而不是失败）。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / "nox-core" / ".venv" / "Scripts" / "python.exe")
TESTS = "nox-core/tests/test_temporal_resolver.py"

RESOLVER = ROOT / "nox-core" / "temporal" / "resolver.py"
INTENT = ROOT / "nox-core" / "temporal" / "intent.py"

# (名字, 文件, 锚点, 换成什么, 期望哪条红)
MUTATIONS = [
    (
        "昨晚退回按日历日算（原来那个 bug）",
        RESOLVER,
        "        woke = ref.date() if ref.hour >= 5 else ref.date() - timedelta(days=1)",
        "        woke = ref.date() - timedelta(days=1)",
        "test_昨晚_早上说归今天",
    ),
    (
        "昨晚的边界从 5 点挪到 0 点",
        RESOLVER,
        "if ref.hour >= 5 else",
        "if ref.hour >= 0 else",
        "test_昨晚_深夜说归昨天",
    ),
    (
        "昨晚的边界挪到 6 点（差一小时）",
        RESOLVER,
        "if ref.hour >= 5 else",
        "if ref.hour >= 6 else",
        "test_昨晚_边界就在五点",
    ),
    (
        "昨晚的 range 不跨午夜",
        RESOLVER,
        "            range=(datetime.combine(night, time(18), tz),\n                   datetime.combine(woke, time(5), tz)),",
        "            range=(datetime.combine(woke, time(18), tz),\n                   datetime.combine(woke, time(23), tz)),",
        "test_昨晚_range跨午夜",
    ),
    (
        "一会又变回 30 分钟",
        RESOLVER,
        '        return _unresolved("vague_no_reminder")',
        '        return Resolution(precision="datetime", at=ref + timedelta(minutes=30))',
        "test_一会是模糊_不给时刻",
    ),
    (
        "一会解析成今天（看着无害，其实是假精确）",
        RESOLVER,
        '        return _unresolved("vague_no_reminder")',
        '        return Resolution(precision="date", date=ref.date())',
        "test_一会是模糊_不给时刻",
    ),
    (
        "契约允许 last_night 带 slot",
        INTENT,
        '            if self.kind in ("last_night", "vague"):',
        "            if False:",
        "test_昨晚_不许带slot",
    ),
    (
        "封闭集合的断言被放宽",
        INTENT,
        '    "last_night",      # 昨晚 —— **跨午夜**，不是一个日历日，见 resolver',
        '    "last_night", "next_month",  #',
        "test_kind_是封闭集合",
    ),
]


def clear_pyc():
    for d in (ROOT / "nox-core" / "temporal", ROOT / "nox-core" / "tests"):
        shutil.rmtree(d / "__pycache__", ignore_errors=True)


def run_tests():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run([PY, "-m", "pytest", TESTS, "-q", "--no-header"],
                           cwd=str(ROOT), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=90)
    except subprocess.TimeoutExpired:
        return 124, "🔴 超时"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    originals = {p: p.read_text(encoding="utf-8") for p in (RESOLVER, INTENT)}
    clear_pyc()
    code, out = run_tests()
    if code != 0:
        print("🔴 没改之前就是红的：")
        print(out[-1500:])
        return 1
    print("基线：绿 ✅\n")

    caught, missed = 0, []
    for name, path, anchor, repl, expect in MUTATIONS:
        src = originals[path]
        n = src.count(anchor)
        if n != 1:
            print(f"⚠️ 跳过「{name}」：锚点出现 {n} 次，不唯一")
            missed.append((name, f"锚点 {n} 次"))
            continue

        path.write_text(src.replace(anchor, repl, 1), encoding="utf-8")
        clear_pyc()
        try:
            code, out = run_tests()
        finally:
            path.write_text(src, encoding="utf-8")
            clear_pyc()

        if code != 0:
            hit = expect in out
            print(("✅" if hit else "⚠️") + f" 「{name}」→ 红了"
                  + ("" if hit else f"（但红的不是 {expect}）"))
            if hit:
                caught += 1
            else:
                missed.append((name, f"红的不是 {expect}"))
        else:
            print(f"🔴 「{name}」→ **还是绿的**。这条保护没有检查守着。")
            missed.append((name, "全绿"))

    print(f"\n抓到 {caught}/{len(MUTATIONS)}")
    for n, why in missed:
        print(f"   漏：{n} —— {why}")

    for p, s in originals.items():
        assert p.read_text(encoding="utf-8") == s, f"{p.name} 没还原干净！"
    clear_pyc()
    code, _ = run_tests()
    print("还原后：" + ("绿 ✅" if code == 0 else "🔴 红的，源码没还原干净"))
    return 0 if (caught == len(MUTATIONS) and code == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
