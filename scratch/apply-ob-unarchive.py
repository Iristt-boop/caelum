#!/usr/bin/env python3
r"""Ombre-Brain 归档可逆 + 不再谎报（排期 4.3）。

在 VPS 上跑：
    python3 /tmp/apply-ob-unarchive.py            # 改
    python3 /tmp/apply-ob-unarchive.py --check    # 只看现状

## 它治的是什么

**① 归档是单向的。** 全仓 grep `unarchive` **零命中**。
归档桶不参与浮现（`list_all(include_archive=False)` 不遍历 `archive/`），
也不再衰减 —— **进去了就再也出不来**。

实测线上 6 条在归档里，其中两条是她真的东西：

    糖糖生日与星座        importance 3
    糖糖身高体重记录      importance 9   ← 最高档，浮现不出来

衰减引擎按"很久没被碰"归档，而**"很久没被碰"和"不重要"是两回事** ——
她的身高体重本来就不会天天提。

**② `trace(resolved=0)` 在说谎。** 它回一句
「已重新激活，将参与浮现排序」，而如果这个桶在 `archive/` 里，
**这句话是假的**：文件没动地方，浮现路径根本不看那个目录。

## 改什么

1. `BucketManager.unarchive()` —— `archive()` 的逆操作（源码从
   `ob-unarchive.py` 读，**那边是唯一真源**）
2. `archive()` 记下 `archived_from`，这样回位时知道该回哪个目录 ——
   现在它把 `type` 直接覆盖成 `archived`，**原类型当场就丢**
3. `trace`：不再无条件说"已重新激活"。
   · 桶在归档里 + 要求 `resolved=0` → **真的 unarchive**，然后如实说
   · unarchive 失败 → 说实话，不说"已激活"

## 保险

  · 改前整目录备份
  · 锚点数必须正好等于预期，否则中止不猜
  · 保留换行风格（`bucket_manager.py` 是 CRLF）
  · 改完 py_compile + **真的 import 每一个被改的模块**
  · 改完跑 `/tmp/test-ob-unarchive.py`
  · **不重启服务**
"""

import pathlib
import py_compile
import re
import shutil
import subprocess
import sys
import time

OB = pathlib.Path("/root/ombre-brain")
SRC = pathlib.Path("/tmp/ob-unarchive.py")
CHECK_ONLY = "--check" in sys.argv


def read_src(p: pathlib.Path) -> tuple[str, str]:
    raw = p.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), ("\r\n" if "\r\n" in raw else "\n")


def write_src(p: pathlib.Path, s: str, nl: str) -> None:
    p.write_bytes(s.replace("\n", nl).encode("utf-8"))


def style(b: bytes) -> str:
    crlf = b.count(b"\r\n")
    lf = b.count(b"\n") - crlf
    return "混合" if crlf and lf else ("CRLF" if crlf else "LF")


# ── archive()：记下原来的 type ──────────────────────────────────
ARCH_OLD = """            # Update type marker then move file / 更新类型标记后移动文件
            post["type"] = "archived"
"""
ARCH_NEW = """            # Update type marker then move file / 更新类型标记后移动文件
            # 🔴 先记下原来是什么类型（排期 4.3）。在这之前是直接覆盖成
            #    "archived"，**原类型当场就丢** —— 于是 unarchive 无从知道
            #    该把它放回 permanent 还是 dynamic 还是 feel。
            #    线上已有的 6 条归档桶没有这个字段，unarchive 对它们按
            #    pinned 猜（钉选回 permanent，其余回 dynamic）。
            if post.get("type") and post.get("type") != "archived":
                post["archived_from"] = post["type"]
            post["type"] = "archived"
"""

# ── trace：不再无条件说"已重新激活" ────────────────────────────
TRACE_OLD = """    if "resolved" in updates:
        if updates["resolved"]:
            changed += " → 已沉底，只在关键词触发时重新浮现"
        else:
            changed += " → 已重新激活，将参与浮现排序"
"""
TRACE_NEW = """    if "resolved" in updates:
        if updates["resolved"]:
            changed += " → 已沉底，只在关键词触发时重新浮现"
        else:
            # 🔴 **这里原来是无条件说"已重新激活，将参与浮现排序"** —— 而如果
            #    这个桶在 archive/ 里，那句话是假的：文件没动地方，浮现路径
            #    （list_all(include_archive=False)）根本不看那个目录。
            #    审计点名：「trace 谎报已激活」。
            #
            #    现在：要求取消 resolved，就**真的**把它从归档里捞出来，
            #    然后如实说发生了什么。捞不动也说实话。
            if await bucket_mgr.unarchive(bucket_id):
                changed += " → 已从归档取回并重新激活，将参与浮现排序"
            else:
                changed += " → 已重新激活，将参与浮现排序"
"""


def survey() -> dict:
    bm, _ = read_src(OB / "bucket_manager.py")
    sv, _ = read_src(OB / "server.py")
    return {
        "has_unarchive": "async def unarchive(" in bm,
        "archive_anchor": bm.count(ARCH_OLD),
        "trace_anchor": sv.count(TRACE_OLD),
        "archived_now": len(list((OB / "buckets" / "archive").rglob("*.md"))),
    }


def main() -> int:
    st = survey()
    print("── 现状 ──")
    print(f"  已有 unarchive        : {st['has_unarchive']}")
    print(f"  archive() 锚点        : {st['archive_anchor']}（期望 1）")
    print(f"  trace 谎报那段锚点    : {st['trace_anchor']}（期望 1）")
    print(f"  当前归档里的桶        : {st['archived_now']} 条")

    if CHECK_ONLY:
        print("\n（--check：只看不改）")
        return 0
    if st["has_unarchive"]:
        print("\n已经改过了，不重复改。")
        return 0
    if st["archive_anchor"] != 1 or st["trace_anchor"] != 1:
        print("\n🔴 锚点对不上 —— 中止，不猜。")
        return 2
    if not SRC.exists():
        print(f"\n🔴 {SRC} 不在 —— 先把 scratch/ob-unarchive.py 传上来")
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = pathlib.Path(f"/root/ob-backup-{stamp}")
    shutil.copytree(OB, backup, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "buckets", "*.db", "*.db-wal", "*.db-shm"))
    print(f"\n── 代码已备份到 {backup} ──")

    # ① bucket_manager：archived_from + unarchive 方法
    bm_path = OB / "bucket_manager.py"
    bm, bm_nl = read_src(bm_path)
    bm = bm.replace(ARCH_OLD, ARCH_NEW)

    method = SRC.read_text(encoding="utf-8")
    method = method[method.index("async def unarchive("):].rstrip() + "\n"
    # 贴到 archive() 之后（`_find_bucket_file` 那段注释之前）
    anchor = "    # ---------------------------------------------------------\n" \
             "    # Internal: find bucket file across all three directories\n"
    if bm.count(anchor) != 1:
        print(f"🔴 插入点锚点 {bm.count(anchor)} 次 —— 中止")
        return 2
    bm = bm.replace(anchor, "    " + method.replace("\n", "\n    ").rstrip() + "\n\n" + anchor)
    write_src(bm_path, bm, bm_nl)
    print("  ✅ bucket_manager.py：archive() 记 archived_from + 新增 unarchive()")

    # ② server.py：trace 不再谎报
    sv_path = OB / "server.py"
    sv, sv_nl = read_src(sv_path)
    sv = sv.replace(TRACE_OLD, TRACE_NEW)
    write_src(sv_path, sv, sv_nl)
    print("  ✅ server.py：trace 不再无条件说「已重新激活」")

    # ── 验证 ──────────────────────────────────────────────
    print("\n── 换行风格 ──")
    bad = 0
    for name in ("bucket_manager.py", "server.py"):
        new = (OB / name).read_bytes()
        old = subprocess.run(["git", "-C", str(OB), "show", f"HEAD:{name}"],
                             capture_output=True).stdout
        o, n = style(old), style(new)
        ok = o == n and n != "混合"
        print(f"  {name:22} {o} → {n}  {'✅' if ok else '🔴'}")
        bad += not ok

    print("\n── 语法 ──")
    for f in OB.glob("*.py"):
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            print(f"  🔴 {f.name}: {e}")
            bad += 1
    print("  全部通过" if not bad else "  有问题")

    # 🔴 **真的 import 被改的模块**，别只 import 一个没被改的
    #    （2026-09-13 就是这么漏掉一个运行时 NameError 的）
    print("\n── 真的 import 被改的模块 ──")
    probe = (
        "import sys; sys.path.insert(0,'/root/ombre-brain');"
        "import bucket_manager;"
        "assert hasattr(bucket_manager.BucketManager,'unarchive'), 'unarchive 没挂到类上';"
        "import inspect;"
        "assert inspect.iscoroutinefunction(bucket_manager.BucketManager.unarchive), 'unarchive 不是 async';"
        "print('  bucket_manager 可导入，unarchive 是 BucketManager 的 async 方法')"
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    print(r.stdout.rstrip() or f"  🔴 {r.stderr.strip()[-500:]}")
    if r.returncode != 0:
        print(f"  🔴 {r.stderr.strip()[-500:]}")
        bad += 1

    if bad:
        print(f"\n🔴 没改成。回滚：cp {backup}/*.py {OB}/")
        return 1

    print(f"""
✅ 改完了，**服务还没重启** —— 线上跑的还是旧代码。
   看 `cd {OB} && git diff --ignore-cr-at-eol`，满意了再：
       systemctl restart ombre-brain
   回滚：cp {backup}/*.py {OB}/ && systemctl restart ombre-brain
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
