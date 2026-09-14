"""变异测试：把债 5 的每一处改动故意改坏，确认对应的检查会红。

规矩见 CAELUM-MAP.md 第三·五节。**红不了的检查不算检查。**

用法（在 nox-core/ 下）：
    <venv>/python.exe ../scratch/mutate-restart-state.py

每条变异：改一处源码 → 跑测试 → 必须红 → 还原。
最后必须原样全绿（否则说明还原没干净）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "nox-core"
MOOD = ROOT / "personality" / "mood.py"
NOX = ROOT / "nox.py"
TESTS = "tests/test_restart_state.py"

# (名字, 文件, 原文, 换成, 期望哪条红)
MUTATIONS = [
    (
        "落盘整个不写了",
        NOX,
        'store.set_source_state(STATE_KEY, {',
        'pass or (lambda *a: None)(STATE_KEY, {',
        "存进去再读回来 / 走真的_dynamic",
    ),
    (
        "恢复时换对象而不是就地改（引用陷阱）",
        MOOD,
        "self.valence = round(DEFAULT_VALENCE + (valence - DEFAULT_VALENCE) * decay, 3)",
        "_throwaway = round(DEFAULT_VALENCE + (valence - DEFAULT_VALENCE) * decay, 3)",
        "恢复必须就地改 / 衰减曲线",
    ),
    (
        "不衰减，一律原样恢复",
        MOOD,
        "decay = 0.5 ** (elapsed / HALF_LIFE.total_seconds())",
        "decay = 1.0",
        "衰减曲线（隔了一天还是热的）",
    ),
    (
        "衰减到 0 而不是默认坐标",
        MOOD,
        "self.valence = round(DEFAULT_VALENCE + (valence - DEFAULT_VALENCE) * decay, 3)",
        "self.valence = round(valence * decay, 3)",
        "衰减的目标是默认坐标不是零",
    ),
    (
        "时钟回拨不 clamp",
        MOOD,
        "elapsed = max((now - saved_at).total_seconds(), 0.0)",
        "elapsed = (now - saved_at).total_seconds()",
        "时钟回拨不会把情绪放大",
    ),
    (
        "她上一轮的情绪不过期",
        MOOD,
        "fresh = decay >= 0.5",
        "fresh = True",
        "离开久了她上一轮的情绪不再算数",
    ),
    (
        "拿不到 store 也标记成已恢复",
        NOX,
        "if store is None:\n            return  # attention 还没起来，下一轮再试",
        "if store is None:\n            self._state_restored = True\n            return",
        "拿不到 store 时不炸也不算已恢复",
    ),
    (
        "落盘换一个键（等于另起一张表）",
        NOX,
        'STATE_KEY = "personality.state"',
        'STATE_KEY = "personality.state.v2"',
        "（键换了但存取都用同一个常量，预期**抓不到** —— 见结论）",
    ),
    (
        "欠卡账不过期",
        NOX,
        "if (now - when) > mood.HALF_LIFE:\n                continue",
        "if False:\n                continue",
        "欠卡账短间隔留住隔久了丢掉",
    ),
    (
        "欠卡账变动不触发落盘",
        NOX,
        "    def _changed(self) -> None:\n        if self._on_change is not None:\n            self._on_change()",
        "    def _changed(self) -> None:\n        return",
        "欠卡账每次变动都会触发落盘",
    ),
    (
        "_dynamic 不调恢复（最要紧的一条）",
        NOX,
        "        self._restore_state_once()",
        "        pass  # 变异：不恢复",
        "第一轮就把情绪接回来_走真的_dynamic",
    ),
]


def run_tests() -> bool:
    """跑测试，返回是否全绿。判据是**退出码**，不是匹配输出文本。

    （匹配文本正是 deploy.ps1 栽过的那个坑：编码一变判据永不成立。）

    🔴 `PYTHONDONTWRITEBYTECODE=1` 不是优化，是**正确性**（2026-09-14 踩过）：
    脚本写回源码的时刻和 pytest 写 `.pyc` 的时刻会落在同一秒，而 Python 判断
    `.pyc` 过没过期是**秒级**比对源码 mtime —— 于是磁盘上留下的是**变异体的
    字节码**，还原之后所有运行都在跑那个被改坏的版本。
    症状极其阴：`inspect.getsource()` 读 `.py` 显示新代码，执行的却是旧的。
    我为此追了半小时"代码明明在那儿却不执行"。
    """
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", TESTS, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, env=env,
    )
    return r.returncode == 0


def clear_pycache() -> None:
    """开跑前清干净 —— 上一次跑可能已经留下了被污染的 .pyc。"""
    for d in ROOT.rglob("__pycache__"):
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    clear_pycache()
    print("=== 基线：不改任何东西，必须全绿 ===")
    if not run_tests():
        print("🔴 基线就是红的，先修好再跑变异")
        return 1
    print("✅ 基线全绿\n")

    caught, missed = [], []
    for name, path, old, new, expect in MUTATIONS:
        src = path.read_text(encoding="utf-8")
        if old not in src:
            print(f"⚠️  锚点找不到，跳过：{name}")
            print(f"    （源码改过了？锚点：{old[:60]!r}）")
            missed.append((name, "锚点失效"))
            continue
        try:
            path.write_text(src.replace(old, new, 1), encoding="utf-8", newline="")
            red = not run_tests()
        finally:
            path.write_text(src, encoding="utf-8", newline="")

        if red:
            print(f"✅ 改坏了会红：{name}")
            caught.append(name)
        else:
            print(f"🔴 改坏了还绿：{name}")
            print(f"    本该红的：{expect}")
            missed.append((name, expect))

    print("\n=== 还原之后必须仍然全绿 ===")
    ok = run_tests()
    print("✅ 还原干净" if ok else "🔴 还原之后是红的，源码被弄坏了")

    print(f"\n抓到 {len(caught)}/{len(MUTATIONS)} 条")
    if missed:
        print("没抓到的：")
        for name, why in missed:
            print(f"  - {name} → {why}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
