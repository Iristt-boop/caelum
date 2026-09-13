#!/usr/bin/env python3
r"""Ombre-Brain 写入耐久性（排期 4.1 + 4.2）。

在 VPS 上跑：
    python3 /tmp/apply-ob-durability.py            # 改
    python3 /tmp/apply-ob-durability.py --check    # 只看现状，不改

## 它治的是什么

**4.2 —— 桶文件是"打开-清空-重写"。** 全仓 6 处：

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(frontmatter.dumps(post))

`open(..., "w")` **先把文件截成 0 字节**。从截断到写完这中间，
磁盘上那个桶是空的。这期间被打断（进程被杀 / 断电 / 磁盘满），
**那条记忆永久没了 —— 不报错、不留痕**。
249 个桶，写者不止一个（server 的 hold/grow、decay_engine、dehydrator、
reclassify 脚本…），谁都可能撞上谁。

**4.1 —— 10 处 `sqlite3.connect` 零 PRAGMA。** 实测两个库都是
`journal_mode=delete`：读会被写整个挡住，崩在事务中间要靠回滚日志恢复。
而 nox-core 那边三个库都设了 WAL —— 同一套系统两套标准。

⚠️ **`busy_timeout` 不在缺口里**（2026-09-13 实测）：Python 的
`sqlite3.connect()` 默认 `timeout=5.0` 本来就等价 5000。
用 `sqlite3` 命令行读到的 `busy_timeout=0` 是**命令行自己那条连接**的值，
不代表 OB 运行时 —— 别拿它当证据。

## 怎么改

`utils.py` 加两个函数（源码从 `ob-utils-additions.py` 读，**那边是唯一真源**），
然后把 6 + 10 处机械替换掉。

## 保险

  · 改前整目录备份到 /root/ob-backup-<时间戳>
  · 每个锚点出现次数必须**正好等于预期**，否则中止不猜
  · 改完 `py_compile` 全部文件
  · 改完跑 `/tmp/test-ob-utils-additions.py`（纯 stdlib，VPS 上跑得动）
  · 不重启服务 —— 那一步留给人看完再决定
"""

import os
import pathlib
import py_compile
import shutil
import subprocess
import sys
import time

OB = pathlib.Path("/root/ombre-brain")
ADDITIONS = pathlib.Path("/tmp/ob-utils-additions.py")

CHECK_ONLY = "--check" in sys.argv

# ── 预期的替换点数量。对不上就中止 —— 说明代码变了，我的假设过期了 ──
EXPECT = {
    "bucket_manager.py": 6,     # 桶写入
    "dehydrator.py": 4,         # sqlite3.connect
    "embedding_engine.py": 6,   # sqlite3.connect
}

OLD_WRITE = '''            with open(file_path, "w", encoding="utf-8") as f:
                f.write(frontmatter.dumps(post))'''
NEW_WRITE = '''            write_atomic(file_path, frontmatter.dumps(post))'''


def survey() -> dict:
    out = {}
    for name in EXPECT:
        p = OB / name
        s = p.read_text(encoding="utf-8")
        out[name] = {
            "open_w": s.count('open(file_path, "w", encoding="utf-8")'),
            "connect": s.count("sqlite3.connect("),
            "already": s.count("write_atomic(") + s.count("connect_db("),
        }
    return out


def main() -> int:
    if not OB.exists():
        print(f"🔴 找不到 {OB}")
        return 2

    print("── 现状 ──")
    before = survey()
    for name, c in before.items():
        print(f"  {name:22} open(...,'w')={c['open_w']}  sqlite3.connect={c['connect']}"
              f"  已改过={c['already']}")

    if CHECK_ONLY:
        print("\n（--check：只看不改）")
        return 0

    if any(c["already"] for c in before.values()):
        print("\n已经改过了，不重复改。")
        return 0

    if not ADDITIONS.exists():
        print(f"🔴 {ADDITIONS} 不在 —— 先把 scratch/ob-utils-additions.py 传上来")
        return 2

    # ── 备份 ──────────────────────────────────────────────
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = pathlib.Path(f"/root/ob-backup-{stamp}")
    shutil.copytree(OB, backup, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "buckets", "*.db", "*.db-wal", "*.db-shm"))
    print(f"\n── 代码已备份到 {backup}（桶和库没动，它们本来就不该被这个脚本碰）──")

    # ── ① utils.py 加两个函数 ─────────────────────────────
    add_src = ADDITIONS.read_text(encoding="utf-8")
    # 去掉文件头的说明块，只取两个函数（从第一个 def 开始）
    body = add_src[add_src.index("def write_atomic("):]
    utils = OB / "utils.py"
    u = utils.read_text(encoding="utf-8")
    if "def write_atomic(" in u:
        print("  utils.py 已经有了，跳过")
    else:
        for imp in ("import sqlite3\n", "import tempfile\n"):
            if imp not in u:
                u = u.replace("import os\n", "import os\n" + imp, 1)
        u = u.rstrip() + "\n\n\n" + (
            "# ══════════════════════════════════════════════════════════════\n"
            "# 写入耐久性（排期 4.1 / 4.2，2026-09-13）\n"
            "# 说明和取舍见这两个函数各自的 docstring。\n"
            "# 改它们之前先跑 tests/test_atomic_write.py（纯 stdlib，不需要 pytest）。\n"
            "# ══════════════════════════════════════════════════════════════\n\n"
        ) + body
        utils.write_text(u, encoding="utf-8")
        print("  ✅ utils.py 加了 write_atomic / connect_db")

    # ── ② bucket_manager.py：6 处原子写 ───────────────────
    bm = OB / "bucket_manager.py"
    s = bm.read_text(encoding="utf-8")
    n = s.count(OLD_WRITE)
    if n != EXPECT["bucket_manager.py"]:
        print(f"🔴 bucket_manager 的写入锚点 {n} 处，期望 {EXPECT['bucket_manager.py']} 处 —— 中止，不猜")
        return 2
    s = s.replace(OLD_WRITE, NEW_WRITE)
    if "from utils import" in s and "write_atomic" not in s.split("\n\n")[0]:
        s = s.replace("from utils import", "from utils import write_atomic,", 1)
    else:
        s = s.replace("import os\n", "import os\nfrom utils import write_atomic\n", 1)
    bm.write_text(s, encoding="utf-8")
    print(f"  ✅ bucket_manager.py：{n} 处改成原子写")

    # ── ③ 两个库文件：connect → connect_db ────────────────
    for name in ("dehydrator.py", "embedding_engine.py"):
        p = OB / name
        s = p.read_text(encoding="utf-8")
        n = s.count("sqlite3.connect(")
        if n != EXPECT[name]:
            print(f"🔴 {name} 的 connect 锚点 {n} 处，期望 {EXPECT[name]} 处 —— 中止")
            return 2
        s = s.replace("sqlite3.connect(", "connect_db(")
        if "from utils import" in s:
            s = s.replace("from utils import", "from utils import connect_db,", 1)
        else:
            s = s.replace("import sqlite3\n", "import sqlite3\nfrom utils import connect_db\n", 1)
        p.write_text(s, encoding="utf-8")
        print(f"  ✅ {name}：{n} 处改走 connect_db")

    # ── ④ 验证 ───────────────────────────────────────────
    print("\n── 语法检查 ──")
    bad = 0
    for f in OB.glob("*.py"):
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            print(f"  🔴 {f.name}: {e}")
            bad += 1
    print(f"  {'全部通过' if not bad else f'{bad} 个文件编译失败'}")
    if bad:
        print(f"\n🔴 没改成。回滚：rm -rf {OB}/*.py && cp {backup}/*.py {OB}/")
        return 1

    print("\n── 导入检查（PRAGMA 真的设得上吗）──")
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0,'/root/ombre-brain');"
         "from utils import write_atomic, connect_db;"
         "import tempfile,os;"
         "d=tempfile.mkdtemp();"
         "c=connect_db(os.path.join(d,'t.db'));"
         "print('  journal_mode =', c.execute('PRAGMA journal_mode').fetchone()[0]);"
         "p=os.path.join(d,'x.md'); write_atomic(p,'ok');"
         "print('  write_atomic  =', open(p,encoding='utf-8').read())"],
        capture_output=True, text=True)
    print(r.stdout.rstrip() or f"  🔴 {r.stderr.strip()[:400]}")
    if r.returncode != 0:
        print(f"\n🔴 导入失败。回滚：cp {backup}/*.py {OB}/")
        return 1

    print(f"""
✅ 改完了，**但服务还没重启** —— 线上跑的还是旧代码。
   看一遍 `cd {OB} && git diff`，满意了再：
       systemctl restart ombre-brain && systemctl is-active ombre-brain
   回滚：cp {backup}/*.py {OB}/ && systemctl restart ombre-brain

⚠️ 已有的两个库还是 delete 模式 —— WAL 要等下一次 connect 才会翻。
   重启之后用这条确认：
       sqlite3 <库> 'PRAGMA journal_mode;'
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
