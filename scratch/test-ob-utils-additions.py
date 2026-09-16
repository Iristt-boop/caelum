#!/usr/bin/env python3
"""`write_atomic` / `connect_db` 的测试（排期 4.1 / 4.2）。

## 为什么单独一个文件、用 stdlib unittest

Ombre-Brain 本体在本机跑不了测试（没 venv、缺 `frontmatter`），
在 VPS 上也跑不了（没装 pytest，而它现有的 4 个测试要 `pytest_asyncio`）。

但这两个函数是**纯 stdlib** —— 所以它们可以两边都跑：

    本机：python scratch/test-ob-utils-additions.py
    VPS ：python3 /tmp/test-ob-utils-additions.py

这不是将就。**这一整条线（记忆写坏了就永久没了）此前零测试**，
能跑起来的一条，比跑不起来的十条有用。

## 最要紧的一条

`test_crash_midwrite_leaves_old_file_intact` —— 写到一半炸掉，
旧文件必须**一个字节都没变**。那正是原来 `open(..., "w")` 做不到的事：
它先把文件截成 0，再往里写。
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from importlib.machinery import SourceFileLoader

_mod = SourceFileLoader(
    "ob_utils_additions",
    str(Path(__file__).resolve().parent / "ob-utils-additions.py"),
).load_module()
write_atomic = _mod.write_atomic
connect_db = _mod.connect_db


class WriteAtomic(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "bucket.md")

    def test_writes_content(self):
        write_atomic(self.path, "记忆内容\n第二行")
        self.assertEqual(Path(self.path).read_text(encoding="utf-8"), "记忆内容\n第二行")

    def test_overwrites_existing(self):
        Path(self.path).write_text("旧的", encoding="utf-8")
        write_atomic(self.path, "新的")
        self.assertEqual(Path(self.path).read_text(encoding="utf-8"), "新的")

    def test_crash_midwrite_leaves_old_file_intact(self):
        """🔴 **这条是 4.2 的全部意义。**

        写到一半炸掉，旧文件必须一个字节都没动。
        原来那个 `open(path, "w")` 在这里会留下一个**空文件** —— 记忆没了。
        """
        Path(self.path).write_text("她说的那句话，很重要", encoding="utf-8")
        before = Path(self.path).read_bytes()

        real_write = os.fdopen

        class Boom(Exception):
            pass

        def exploding_fdopen(fd, *a, **kw):
            f = real_write(fd, *a, **kw)
            orig = f.write

            def w(s):
                orig(s[: len(s) // 2])   # 写一半
                raise Boom("磁盘满了")

            f.write = w
            return f

        os.fdopen = exploding_fdopen
        try:
            with self.assertRaises(Boom):
                write_atomic(self.path, "新内容，但是写不完")
        finally:
            os.fdopen = real_write

        self.assertEqual(Path(self.path).read_bytes(), before,
                         "🔴 旧文件被动过了 —— 原子写没起作用")

    def test_no_leftover_temp_after_failure(self):
        """失败之后不许留残骸。

        留了的话，桶目录会慢慢堆满 `.ob-tmp-*`，而且没人会发现。
        """
        Path(self.path).write_text("旧的", encoding="utf-8")
        real = os.fdopen

        def exploding(fd, *a, **kw):
            os.close(fd)
            raise RuntimeError("炸")

        os.fdopen = exploding
        try:
            with self.assertRaises(RuntimeError):
                write_atomic(self.path, "x")
        finally:
            os.fdopen = real

        leftovers = [f for f in os.listdir(self.dir) if f.startswith(".ob-tmp-")]
        self.assertEqual(leftovers, [], f"留下了残骸：{leftovers}")

    def test_temp_file_is_never_seen_as_a_bucket(self):
        """🔴 临时文件**不能**以 `.md` 结尾。

        `bucket_manager.py` 是用 `endswith(".md")` 枚举桶的。
        留一个半截的 `.md` 临时文件，下一次扫描会**把它当成一条记忆**。
        """
        seen = []
        real = os.fdopen

        def spy(fd, *a, **kw):
            seen.extend(os.listdir(self.dir))   # 此刻 tmp 还在
            return real(fd, *a, **kw)

        os.fdopen = spy
        try:
            write_atomic(self.path, "内容")
        finally:
            os.fdopen = real

        tmps = [f for f in seen if f.startswith(".ob-tmp-")]
        self.assertTrue(tmps, "没看到临时文件，这条测试本身没测到东西")
        for t in tmps:
            self.assertFalse(t.endswith(".md"),
                             f"🔴 临时文件叫 {t}，会被当成桶扫进去")

    def test_temp_is_in_the_same_directory(self):
        """临时文件必须和目标同目录 —— 跨文件系统 `os.replace` 不是原子的。"""
        seen = []
        real = os.fdopen

        def spy(fd, *a, **kw):
            seen.extend(os.listdir(self.dir))
            return real(fd, *a, **kw)

        os.fdopen = spy
        try:
            write_atomic(self.path, "内容")
        finally:
            os.fdopen = real
        self.assertTrue([f for f in seen if f.startswith(".ob-tmp-")],
                        "临时文件不在目标目录里")

    def test_accepts_path_objects(self):
        """`safe_path()` 返回的是 `Path`，不是 str。"""
        write_atomic(Path(self.path), "内容")
        self.assertEqual(Path(self.path).read_text(encoding="utf-8"), "内容")


class ConnectDb(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "x.db")

    def test_sets_wal(self):
        """🔴 WAL 是 4.1 真正缺的那个 —— 实测两个库当时都是 delete。"""
        with connect_db(self.path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_wal_is_persistent(self):
        """设一次就写进文件里了，下一条连接不用再设。"""
        connect_db(self.path).close()
        plain = sqlite3.connect(self.path)
        try:
            mode = plain.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            plain.close()
        self.assertEqual(mode.lower(), "wal")

    def test_busy_timeout_survives_a_zero_default(self):
        """⚠️ 这条挡的**不是**"有人删掉那行 PRAGMA"。

        Python 的 `connect()` 默认 `timeout=5.0` 本来就给 5000，
        删掉 PRAGMA 这条照样绿（今天在 nox-core 那边已经踩过一次）。
        它挡的是**有人传了 `timeout=0`** —— 那时 PRAGMA 要能盖回来。
        """
        with connect_db(self.path, timeout=0) as conn:
            got = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertGreaterEqual(got, 5000, "timeout=0 时 PRAGMA 没盖回来")

    def test_still_usable(self):
        conn = connect_db(self.path)
        conn.execute("CREATE TABLE t (a TEXT)")
        conn.execute("INSERT INTO t VALUES ('记忆')")
        conn.commit()
        self.assertEqual(conn.execute("SELECT a FROM t").fetchone()[0], "记忆")
        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
