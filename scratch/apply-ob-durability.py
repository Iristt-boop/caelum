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
import re
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

# 🔴 **缩进不能写死。** 第一版按 12 格缩进硬匹配，只命中 5 处 ——
# `bucket_manager.py:416` 那处在两层 try 里面，缩进是 20 格。
# 脚本当时**中止了而不是放宽匹配去凑数**，那个行为是对的：
# 锚点对不上说明我的假设过期了，猜一下就可能改错地方。
WRITE_RE = re.compile(
    r'^(?P<ind>[ ]+)with open\(file_path, "w", encoding="utf-8"\) as f:\n'
    r'(?P=ind)    f\.write\(frontmatter\.dumps\(post\)\)\n',
    re.MULTILINE,
)


def _to_atomic(m) -> str:
    return m.group("ind") + "write_atomic(file_path, frontmatter.dumps(post))\n"


def read_src(p: pathlib.Path) -> tuple[str, str]:
    """读文件，返回 (LF 归一化的正文, 原来的换行符)。

    🔴 **2026-09-13 第一版在这里翻过车**：直接用 `read_text()` / `write_text()`，
    而 `bucket_manager.py` 是 CRLF 的（779 行）—— 读的时候被悄悄转成 LF，
    写回去就变成整个文件都改了。**diff 1552 行，只有 7 行是我真想改的。**
    功能上没坏（Linux 上 Python 不在乎），但 **diff 没法审 = 等于没法验**。
    """
    raw = p.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), nl


def write_src(p: pathlib.Path, s: str, nl: str) -> None:
    p.write_bytes(s.replace("\n", nl).encode("utf-8"))


def add_toplevel_import(s: str, line: str) -> str:
    """在**最后一条顶层 import 之后**插一行 import。

    🔴 **这条是我今天亲手制造的 bug，写在这里免得再犯。**

    第一版是 `s.replace("from utils import", "from utils import X,", 1)` ——
    看着挺聪明：如果已经从 utils 引过东西就接在后面。
    结果 `embedding_engine.py` 里第一个 `from utils import` 在**第 150 行、
    函数体内部**。于是 `connect_db` 变成了那一个函数的局部名字，
    而另外 5 处调用在别的函数里 → **运行时 `NameError`**。

    `py_compile` 抓不到（那是运行时错误），我当时的"导入检查"也抓不到
    （我只 import 了 `utils`，没 import 被改的模块）。
    **检查没覆盖到被改的东西，就等于没检查。**
    """
    if line in s:
        return s
    lines = s.split("\n")
    last = -1
    for i, ln in enumerate(lines):
        # 只认顶格的 import —— 缩进的都是函数内的，插那儿就是上面那个 bug
        if re.match(r"^(import |from )\S", ln):
            last = i
    if last < 0:
        return line + "\n" + s
    lines.insert(last + 1, line)
    return "\n".join(lines)


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
    u, u_nl = read_src(utils)
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
        write_src(utils, u, u_nl)
        print("  ✅ utils.py 加了 write_atomic / connect_db")

    # ── ② bucket_manager.py：6 处原子写 ───────────────────
    bm = OB / "bucket_manager.py"
    s, bm_nl = read_src(bm)
    n = len(WRITE_RE.findall(s))
    if n != EXPECT["bucket_manager.py"]:
        print(f"🔴 bucket_manager 的写入锚点 {n} 处，期望 {EXPECT['bucket_manager.py']} 处 —— 中止，不猜")
        return 2
    s = WRITE_RE.sub(_to_atomic, s)
    s = add_toplevel_import(s, "from utils import write_atomic")
    write_src(bm, s, bm_nl)
    print(f"  ✅ bucket_manager.py：{n} 处改成原子写")

    # ── ③ 两个库文件：connect → connect_db ────────────────
    for name in ("dehydrator.py", "embedding_engine.py"):
        p = OB / name
        s, nl = read_src(p)
        n = s.count("sqlite3.connect(")
        if n != EXPECT[name]:
            print(f"🔴 {name} 的 connect 锚点 {n} 处，期望 {EXPECT[name]} 处 —— 中止")
            return 2
        s = s.replace("sqlite3.connect(", "connect_db(")
        s = add_toplevel_import(s, "from utils import connect_db")
        write_src(p, s, nl)
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

    # 🔴 **必须 import 被改的每一个模块，不能只 import utils。**
    #
    # 第一版只 import 了 `utils`，于是没抓到 `embedding_engine` 里
    # `connect_db` 被插成了**函数局部名字**（另外 5 处调用会运行时 NameError）。
    # `py_compile` 也抓不到 —— 那是运行时错误。
    # **检查没覆盖到被改的东西，就等于没检查。**
    print("\n── 换行符有没有被无意改掉 ──")
    #
    # ⚠️ **不能比绝对行数。** 第一版比的是 CRLF 行数，结果 779 → 774 报红 ——
    # 而那 5 行是**正当**的：6 处「两行变一行」(-6) 加 1 行 import (+1)。
    # 差点因为一个写笨了的检查，把一次正确的改动回滚掉。
    #
    # 要比的是**混进了多少裸 LF**：一个 CRLF 文件改完还该是纯 CRLF，
    # 裸 LF 数应该恒为 0。这个量和行数增减无关。
    # ⚠️ 第二版比"裸 LF 条数"也不对：纯 LF 文件里它就等于行数，加一行 import
    #    就变了。**行数本来就该变。**
    #    真正的不变量是**风格**：本来纯 CRLF 的还得纯 CRLF，纯 LF 的还得纯 LF，
    #    而且谁都不许变成混合。这个量和行数增减无关。
    def style(b: bytes) -> str:
        crlf = b.count(b"\r\n")
        lf = b.count(b"\n") - crlf
        if crlf and lf:
            return "混合"
        return "CRLF" if crlf else "LF"

    nl_bad = 0
    for name in EXPECT:
        raw_new = (OB / name).read_bytes()
        raw_old = subprocess.run(["git", "-C", str(OB), "show", f"HEAD:{name}"],
                                 capture_output=True).stdout
        o, n_ = style(raw_old), style(raw_new)
        ok = o == n_ and n_ != "混合"
        print(f"  {name:22} 换行风格 {o} → {n_}   {'✅' if ok else '🔴 变了'}")
        nl_bad += not ok
    if nl_bad:
        print(f"\n🔴 换行符被动了，diff 会没法审。回滚：cp {backup}/*.py {OB}/")
        return 1

    print("\n── 真正导入每一个被改的模块 ──")
    probe = (
        "import sys; sys.path.insert(0,'/root/ombre-brain');"
        "import utils, bucket_manager, dehydrator, embedding_engine;"
        # 名字必须在**模块级**够得着 —— 这一句就是上次漏掉的那道
        "assert hasattr(bucket_manager, 'write_atomic'), 'bucket_manager 里 write_atomic 不是模块级';"
        "assert hasattr(dehydrator, 'connect_db'), 'dehydrator 里 connect_db 不是模块级';"
        "assert hasattr(embedding_engine, 'connect_db'), 'embedding_engine 里 connect_db 不是模块级';"
        "import tempfile,os;"
        "d=tempfile.mkdtemp();"
        "c=utils.connect_db(os.path.join(d,'t.db'));"
        "print('  journal_mode =', c.execute('PRAGMA journal_mode').fetchone()[0]);"
        "p=os.path.join(d,'x.md'); utils.write_atomic(p,'ok');"
        "print('  write_atomic  =', open(p,encoding='utf-8').read());"
        "print('  四个模块都能导入，且三个名字都在模块级')"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    print(r.stdout.rstrip() or f"  🔴 {r.stderr.strip()[-600:]}")
    if r.returncode != 0:
        print(f"  🔴 {r.stderr.strip()[-600:]}")
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
