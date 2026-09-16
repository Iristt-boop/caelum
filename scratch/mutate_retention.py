"""变异测试：把 4.7 的每条保护挨个改坏，确认对应的检查会红。

    PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 python -u scratch/mutate_retention.py

🔴 每个变异前断言锚点唯一 —— 锚点不唯一时 replace(...,1) 改的是别处。
🔴 子进程必须限时 —— 某些变异会让测试挂住而不是失败（4.6 那次栽过）。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "scripts" / "retention.py"
PY = str(ROOT / "nox-core" / ".venv" / "Scripts" / "python.exe")
TESTS = "nox-core/tests/test_retention.py"

MUTATIONS = [
    (
        "没写进策略的 type 改成删（默认方向反过来）",
        "            days = self.default_days",
        "            days = 30",
        "test_没见过的type默认留着",
    ),
    (
        "KEEP_FOREVER 当成 0 天",
        "        days = p.days_for(t)\n        if days is KEEP_FOREVER:\n            continue\n        cutoff = (now - timedelta(days=days)).isoformat()\n        if p.type_col:\n            cur = conn.execute(",
        "        days = p.days_for(t)\n        if days is KEEP_FOREVER:\n            days = 0\n        cutoff = (now - timedelta(days=days)).isoformat()\n        if p.type_col:\n            cur = conn.execute(",
        "test_KEEP_FOREVER不等于零天",
    ),
    (
        "边界判据 < 改成 <=",
        'f"DELETE FROM {p.table} "\n                f"WHERE {p.type_col} = ? AND {p.time_col} < ?", (t, cutoff))',
        'f"DELETE FROM {p.table} "\n                f"WHERE {p.type_col} = ? AND {p.time_col} <= ?", (t, cutoff))',
        "test_边界那一行",
    ),
    (
        "删之前不备份",
        "                b = backup(p.db)",
        "                b = p.db",
        "test_真删之前先备份",
    ),
    (
        "干跑也真删",
        "            if apply:\n                b = backup(p.db)",
        "            if True:\n                b = backup(p.db)",
        "test_干跑一个字节都不写",
    ),
    (
        "干跑报 0 条（看起来像没事）",
        '                report["deleted"] += sum(plan.values())',
        '                report["deleted"] += 0',
        "test_干跑一个字节都不写",
    ),
    (
        "旧快照不清（保留策略自己攒垃圾）",
        "    for old in olds[:-KEEP_BACKUPS]:",
        "    for old in []:",
        "test_旧快照会被清掉",
    ),
    (
        "快照清理留错了头（留最旧的，清掉最新的）",
        "    for old in olds[:-KEEP_BACKUPS]:",
        "    for old in olds[KEEP_BACKUPS:]:",
        "test_旧快照会被清掉",
    ),
    (
        "按会话删退回按行删（从活着的对话里往前啃）",
        "f\"GROUP BY {p.group_col} HAVING MAX({p.time_col}) < ?\", (cutoff,))]",
        "f\"WHERE {p.time_col} < ?\", (cutoff,))]",
        "test_还在用的对话一个字都不动",
    ),
    (
        "整组判据用 MIN 而不是 MAX",
        "HAVING MAX({p.time_col}) < ?",
        "HAVING MIN({p.time_col}) < ?",
        "test_还在用的对话一个字都不动",
    ),
    (
        "出厂策略：对话退回按行删",
        '        group_col="id",\n        default_days=KEEP_FOREVER,',
        "        default_days=KEEP_FOREVER,",
        "test_出厂策略_对话的删法保持按会话",
    ),
    (
        "级联不删父行（留下空壳会话）",
        "            if p.cascade:\n                # 🔴 同一个事务里。父行留下来 = 空壳会话，",
        "            if False:\n                # 🔴 同一个事务里。父行留下来 = 空壳会话，",
        "test_级联删掉父行_不留空壳",
    ),
    (
        "出厂策略：messages 去掉级联",
        '        cascade=("sessions", "id"),\n        # 同上 —— 不删。',
        "        # 同上 —— 不删。",
        "test_出厂策略_对话的删法保持按会话",
    ),
    (
        "出厂策略：messages 被设了保留天数（和对话口径不一致）",
        '        default_days=KEEP_FOREVER,\n        why="不删 —— 和 conversations 同口径',
        '        default_days=30,\n        why="不删 —— 和 conversations 同口径',
        "test_出厂策略_对话两张表口径一致",
    ),
    (
        "近期地板：夹一下而不是拒绝（配置事故变成静默删除）",
        "            raise RetentionRefused(",
        "            return RECENT_FLOOR_DAYS  # noqa\n        if False:\n            raise RetentionRefused(",
        "test_近期地板_拒绝过短的保留天数",
    ),
    (
        "近期地板：调低到 1 天",
        "RECENT_FLOOR_DAYS = 7",
        "RECENT_FLOOR_DAYS = 1",
        "test_近期地板_拒绝过短的保留天数",
    ),
    (
        "熔断：只报警不停手",
        '                print(f"🔴 {msg}")\n                report["refused"].append(p.table)\n                continue',
        '                print(f"🔴 {msg}")',
        "test_熔断_要删掉大半张表就停手",
    ),
    (
        "熔断：熔断了还返回 0",
        '    if r["refused"]:',
        "    if False:",
        "test_熔断了要非零退出",
    ),
    (
        "出厂策略：给 usage_log 设保留天数",
        '        table="usage_log",\n        time_col="ts",\n        default_days=KEEP_FOREVER,',
        '        table="usage_log",\n        time_col="ts",\n        default_days=90,',
        "test_出厂策略_usage_log不许删",
    ),
    (
        "出厂策略：observations 默认方向反过来",
        '        type_col="type",\n        default_days=KEEP_FOREVER,          # ← 新 type 出现时默认留着',
        '        type_col="type",\n        default_days=90,',
        "test_出厂策略_observations默认是留",
    ),
    (
        "出厂策略：对话又被设了保留天数",
        '        default_days=KEEP_FOREVER,\n        why="不删 —— 量下来约 4MB/年',
        '        default_days=180,\n        why="不删 —— 量下来约 4MB/年',
        "test_出厂策略_对话两张表都不删",
    ),
    (
        "库不存在就默默跳过",
        '            report["missing"].append(str(p.db))',
        "            pass",
        "test_库不在不算通过",
    ),
]


def clear_pyc():
    for d in (ROOT / "scripts", ROOT / "nox-core" / "tests"):
        shutil.rmtree(d / "__pycache__", ignore_errors=True)


def run_tests():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run([PY, "-m", "pytest", TESTS, "-q", "--no-header"],
                           cwd=str(ROOT), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=90)
    except subprocess.TimeoutExpired:
        return 124, "🔴 超时（多半死锁）"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    original = TARGET.read_text(encoding="utf-8")
    clear_pyc()
    code, out = run_tests()
    if code != 0:
        print("🔴 没改之前就是红的：")
        print(out[-1500:])
        return 1
    print("基线：绿 ✅\n")

    caught, missed = 0, []
    for name, anchor, repl, expect in MUTATIONS:
        n = original.count(anchor)
        if n != 1:
            print(f"⚠️ 跳过「{name}」：锚点出现 {n} 次，不唯一")
            missed.append((name, f"锚点 {n} 次"))
            continue

        TARGET.write_text(original.replace(anchor, repl, 1), encoding="utf-8")
        clear_pyc()
        try:
            code, out = run_tests()
        finally:
            TARGET.write_text(original, encoding="utf-8")
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

    assert TARGET.read_text(encoding="utf-8") == original, "源码没还原干净！"
    clear_pyc()
    code, _ = run_tests()
    print("还原后：" + ("绿 ✅" if code == 0 else "🔴 红的，源码没还原干净"))
    return 0 if (caught == len(MUTATIONS) and code == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
