#!/usr/bin/env python3
"""线上漂移检查（N150 本机跑，只读，不需要 git）。

她 10-29：「先治线上和 git 对不上那条」—— 那天对账发现 5 个文件没提交就在线上跑、
两处安全加固只在线上，版本指针停在 10-15。根子是「谁都能 cp 一个文件上去，没人知道」。

做法：每次用 `scripts/ship.py` 发文件，它在 `/root/deployed/<服务>.json` 里记下
每个文件的 sha1 和它来自哪个提交（清单）。这个脚本每天对一遍：

  改了   线上文件 sha1 ≠ 清单 —— 有人绕过 ship.py 改过
  丢了   清单里有、线上没有
  未登记 服务目录里多出来、清单里没有的代码文件（scan 打开的服务才查）

用法：
  check-drift.py              一行结论（晨检用）：部署✓ / 部署✗N处
  check-drift.py --detail     逐条列出
  check-drift.py --list DIR [--exclude-dirs a,b]   列出目录里的代码文件和 sha1（ship.py 建清单用）
  check-drift.py --hash F...  打印这些文件的 sha1（原样 / 去掉 CR 后），JSON

退出码：0 = 一致，1 = 有漂移，2 = 检查本身出错。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import sys

MANIFEST_DIR = "/root/deployed"

#: 不算代码的目录 / 文件：运行数据、依赖、缓存、日志、备份、密钥
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".git", ".pytest_cache", ".mypy_cache"}
SKIP_FILES = ["*.pyc", "*.db", "*.db-shm", "*.db-wal", "*.db-journal", "*.sqlite*", "*.log",
              "*.bak*", "*.jsonl", "*.orig", "*.tmp", ".env", "*.env", ".secret", ".netease_cred"]


def _sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def file_hashes(path: str) -> dict | None:
    try:
        with open(path, "rb") as f:
            b = f.read()
    except OSError:
        return None
    return {"sha1": _sha(b), "sha1_lf": _sha(b.replace(b"\r\n", b"\n"))}


def skipped_file(name: str) -> bool:
    if name.endswith(".env.example"):
        return False
    return any(fnmatch.fnmatch(name, p) for p in SKIP_FILES)


def list_files(root: str, exclude_dirs: list[str] | None = None) -> dict[str, dict]:
    """root 下的代码文件 → sha1。exclude_dirs 是相对 root 的目录（如 buckets）。"""
    ex = {d.strip("/") for d in (exclude_dirs or [])}
    out: dict[str, dict] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and (f"{rel_dir}/{d}" if rel_dir else d) not in ex)
        for name in sorted(filenames):
            if skipped_file(name):
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            h = file_hashes(os.path.join(dirpath, name))
            if h:
                out[rel] = h
    return out


def load_manifests() -> list[dict]:
    out = []
    if not os.path.isdir(MANIFEST_DIR):
        return out
    for name in sorted(os.listdir(MANIFEST_DIR)):
        if name.endswith(".json"):
            with open(os.path.join(MANIFEST_DIR, name), encoding="utf-8") as f:
                out.append(json.load(f))
    return out


def check(manifests: list[dict]) -> list[tuple[str, str, str]]:
    """返回 [(服务, 类别, 文件)]。类别：改了 / 丢了 / 未登记"""
    issues = []
    for m in manifests:
        svc, root = m["service"], m["root"]
        files = m.get("files", {})
        for rel, ent in files.items():
            h = file_hashes(os.path.join(root, rel))
            if h is None:
                issues.append((svc, "丢了", rel))
            elif h["sha1"] != ent["sha1"]:
                issues.append((svc, "改了", rel))
        if m.get("scan"):
            ignore = set(m.get("ignore", []))
            for rel in list_files(root, m.get("exclude_dirs")):
                if rel not in files and rel not in ignore:
                    issues.append((svc, "未登记", rel))
    return issues


def main(argv: list[str]) -> int:
    if "--list" in argv:
        root = argv[argv.index("--list") + 1]
        ex = []
        if "--exclude-dirs" in argv:
            ex = [x for x in argv[argv.index("--exclude-dirs") + 1].split(",") if x]
        print(json.dumps(list_files(root, ex), ensure_ascii=False))
        return 0
    if "--hash" in argv:
        paths = argv[argv.index("--hash") + 1:]
        print(json.dumps({p: file_hashes(p) for p in paths}, ensure_ascii=False))
        return 0

    try:
        manifests = load_manifests()
        issues = check(manifests)
    except Exception as e:  # noqa: BLE001 —— 晨检要的是一行字，炸了也得说出来
        print(f"部署✗检查炸了:{e}")
        return 2
    if not manifests:
        print("部署·没有清单")
        return 2
    n_files = sum(len(m.get("files", {})) for m in manifests)
    if "--detail" in argv:
        for svc, kind, rel in issues:
            print(f"{kind}\t{svc}\t{rel}")
    if not issues:
        print(f"部署✓{len(manifests)}服务{n_files}文件")
        return 0
    by_kind: dict[str, int] = {}
    for _, kind, _ in issues:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    first = issues[0]
    print("部署✗" + "".join(f"{k}{v}" for k, v in by_kind.items()) + f"（{first[0]}/{first[2]}）")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
