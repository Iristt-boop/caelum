#!/usr/bin/env python3
"""`unarchive` 的测试（排期 4.3）。

## 怎么跑

    python scratch/test-ob-unarchive.py        # 本机（系统 python，有 frontmatter）
    python3 /tmp/test-ob-unarchive.py          # VPS

⚠️ **别用 nox-core 那个 venv** —— 它没有 `frontmatter`。

## 测法

`unarchive` 是 `BucketManager` 的方法，但它只用到 self 的五样东西
（四个目录 + `_find_bucket_file`）。所以这里拿一个**桩对象**绑上去测，
不需要真的把整个 BucketManager 立起来（那要 config、embedding engine…）。

## 最要紧的一条

`test_refreshes_last_active_or_decay_will_re_archive_it` ——
衰减分数看 `last_active`，一个桶之所以被归档正是因为这个分数掉下去了。
**放回来却不刷新，下一轮衰减立刻再归档** —— 从外面看就是"没生效"，
而且不报错。这条不钉住的话，整个 4.3 就是演戏。
"""

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import frontmatter

HERE = Path(__file__).resolve().parent


def _load_unarchive():
    """把真源文件里的 unarchive 抠出来执行，注入它需要的依赖。

    这样测的就是**将要贴上线的那份源码**，不是复制品。
    """
    src = (HERE / "ob-unarchive.py").read_text(encoding="utf-8")
    body = src[src.index("async def unarchive("):]

    class _Log:
        def error(self, *a, **kw): pass
        def info(self, *a, **kw): pass

    ns = {
        "os": os, "shutil": shutil, "frontmatter": frontmatter,
        "logger": _Log(),
        "now_iso": lambda: datetime.now(timezone.utc).isoformat(),
        "safe_path": lambda d, n: Path(d) / n,
        "sanitize_name": lambda s: str(s).replace("/", "_"),
        "write_atomic": lambda p, t: Path(p).write_text(t, encoding="utf-8"),
    }
    exec(compile(body, "ob-unarchive.py", "exec"), ns, ns)
    return ns["unarchive"]


unarchive = _load_unarchive()


class _Mgr:
    """只带 unarchive 需要的那五样东西。"""

    def __init__(self, base):
        self.base_dir = base
        self.permanent_dir = os.path.join(base, "permanent")
        self.dynamic_dir = os.path.join(base, "dynamic")
        self.archive_dir = os.path.join(base, "archive")
        self.feel_dir = os.path.join(base, "feel")
        for d in (self.permanent_dir, self.dynamic_dir, self.archive_dir, self.feel_dir):
            os.makedirs(d, exist_ok=True)

    def _find_bucket_file(self, bucket_id):
        for d in (self.permanent_dir, self.dynamic_dir, self.archive_dir, self.feel_dir):
            for root, _, files in os.walk(d):
                for f in files:
                    if f.endswith(".md") and (f[:-3] == bucket_id or f[:-3].endswith(f"_{bucket_id}")):
                        return os.path.join(root, f)
        return None

    unarchive = unarchive


def make_bucket(mgr, bucket_id, where="archive", domain="日常", **meta):
    root = {"archive": mgr.archive_dir, "dynamic": mgr.dynamic_dir,
            "permanent": mgr.permanent_dir, "feel": mgr.feel_dir}[where]
    sub = os.path.join(root, domain)
    os.makedirs(sub, exist_ok=True)
    p = os.path.join(sub, f"记忆_{bucket_id}.md")
    post = frontmatter.Post("她的身高体重", **{
        "id": bucket_id, "type": "archived" if where == "archive" else where,
        "domain": [domain], "importance": 9,
        "last_active": (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(),
        **meta,
    })
    Path(p).write_text(frontmatter.dumps(post), encoding="utf-8")
    return p


def run(coro):
    return asyncio.run(coro)


class Unarchive(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.mgr = _Mgr(self.base)

    def test_moves_back_out_of_archive(self):
        make_bucket(self.mgr, "abc123")
        self.assertTrue(run(self.mgr.unarchive("abc123")))
        p = self.mgr._find_bucket_file("abc123")
        self.assertIn("dynamic", p)
        self.assertNotIn("archive", p)

    def test_type_marker_restored(self):
        make_bucket(self.mgr, "abc123")
        run(self.mgr.unarchive("abc123"))
        post = frontmatter.load(self.mgr._find_bucket_file("abc123"))
        self.assertEqual(post["type"], "dynamic")

    def test_archived_from_is_cleared_after_use(self):
        """🔴 用完要清掉，不然下次归档会读到过期的值。

        ⚠️ 这条断言**本来是空过的**：原先它挂在 `test_type_marker_restored`
        里，而那个测试造的桶根本没有 `archived_from` —— `pop` 一个不存在的
        字段当然"成功"。做变异（把那行 `pop` 删掉）时它照样绿，才发现。
        现在造一个**真的带着这个字段**的桶来断言。
        """
        make_bucket(self.mgr, "perm2", archived_from="permanent")
        run(self.mgr.unarchive("perm2"))
        post = frontmatter.load(self.mgr._find_bucket_file("perm2"))
        self.assertNotIn("archived_from", post.metadata,
                         "archived_from 没清掉 —— 下次归档会按一个过期的值回位")

    def test_refreshes_last_active_or_decay_will_re_archive_it(self):
        """🔴 **这条是 4.3 的成败所在。**

        衰减分数 = `Importance × count^0.3 × e^(-λ×days) × emotion`，
        `days` 来自 `last_active`。桶之所以被归档就是因为这个分数掉下去了。
        放回来却不刷新 → **下一轮衰减立刻再归档**，而且不报错。
        """
        make_bucket(self.mgr, "abc123")   # last_active 是 400 天前
        run(self.mgr.unarchive("abc123"))
        post = frontmatter.load(self.mgr._find_bucket_file("abc123"))
        la = datetime.fromisoformat(str(post["last_active"]))
        age_days = (datetime.now(timezone.utc) - la).total_seconds() / 86400
        self.assertLess(age_days, 1,
                        f"🔴 last_active 还是 {age_days:.0f} 天前 —— 下一轮衰减会再把它归档")

    def test_uses_archived_from_when_present(self):
        make_bucket(self.mgr, "perm1", archived_from="permanent")
        run(self.mgr.unarchive("perm1"))
        self.assertIn("permanent", self.mgr._find_bucket_file("perm1"))

    def test_pinned_goes_to_permanent_when_origin_unknown(self):
        """老归档桶没有 archived_from —— 那时 archive() 直接把 type 覆盖掉了。"""
        make_bucket(self.mgr, "pin1", pinned=True)
        run(self.mgr.unarchive("pin1"))
        self.assertIn("permanent", self.mgr._find_bucket_file("pin1"))

    def test_feel_bucket_goes_back_to_feel(self):
        make_bucket(self.mgr, "feel1", archived_from="feel")
        run(self.mgr.unarchive("feel1"))
        self.assertIn("feel", self.mgr._find_bucket_file("feel1"))

    def test_domain_subdir_preserved(self):
        make_bucket(self.mgr, "d1", domain="健康")
        run(self.mgr.unarchive("d1"))
        self.assertIn(os.path.join("dynamic", "健康"), self.mgr._find_bucket_file("d1"))

    def test_content_intact(self):
        p = make_bucket(self.mgr, "c1")
        before = frontmatter.load(p).content
        run(self.mgr.unarchive("c1"))
        self.assertEqual(frontmatter.load(self.mgr._find_bucket_file("c1")).content, before)

    def test_not_archived_returns_false(self):
        """🔴 对一个**没归档**的桶报"已恢复"，正是 4.3 另一半要修的那种谎。"""
        make_bucket(self.mgr, "live1", where="dynamic")
        self.assertFalse(run(self.mgr.unarchive("live1")))
        self.assertIn("dynamic", self.mgr._find_bucket_file("live1"))

    def test_missing_bucket_returns_false(self):
        self.assertFalse(run(self.mgr.unarchive("不存在")))

    def test_no_leftovers_in_archive(self):
        make_bucket(self.mgr, "abc123")
        run(self.mgr.unarchive("abc123"))
        left = [f for _, _, fs in os.walk(self.mgr.archive_dir) for f in fs if f.endswith(".md")]
        self.assertEqual(left, [], f"archive 里还留着：{left}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
