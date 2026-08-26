#!/usr/bin/env python3
"""扫一遍 buckets，找元数据不全的记忆。只读不改。

起因：Dream 选材时冒出一条 name 就是 id、domain 是「未分类」的记忆
（2026-08-09）。说明某次写入没打上标。先看看有几条。

不修，只报 —— 改数据这种事得糖糖点头。
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dream_select import BUCKETS, as_list, created_of, parse_frontmatter  # noqa: E402

ID_RE = re.compile(r"^[0-9a-f]{8,16}$")


def main() -> int:
    problems: dict[str, list] = {
        "name 就是 id": [],
        "没有 name": [],
        "没有 domain": [],
        "domain=未分类": [],
        "没有 tags": [],
        "没有 created": [],
        "没有 importance": [],
        "没有 arousal": [],
    }
    total = 0
    by_type: Counter = Counter()

    for md in sorted(BUCKETS.rglob("*.md")):
        meta = parse_frontmatter(md)
        if not meta:
            continue
        total += 1
        rel = str(md.relative_to(BUCKETS))
        mid = str(meta.get("id", "")).strip()
        name = str(meta.get("name", "")).strip()
        doms = as_list(meta.get("domain"))
        by_type[str(meta.get("type", "?"))] += 1

        if name and mid and (name == mid or ID_RE.match(name)):
            problems["name 就是 id"].append(rel)
        elif not name:
            problems["没有 name"].append(rel)

        if not doms:
            problems["没有 domain"].append(rel)
        elif "未分类" in doms:
            problems["domain=未分类"].append(rel)

        if not as_list(meta.get("tags")):
            problems["没有 tags"].append(rel)
        if not created_of({"meta": meta}):
            problems["没有 created"].append(rel)
        if not str(meta.get("importance", "")).strip():
            problems["没有 importance"].append(rel)
        if not str(meta.get("arousal", "")).strip():
            problems["没有 arousal"].append(rel)

    print("=" * 62)
    print("元数据体检 · 共 %d 条记忆" % total)
    print("按桶：" + "  ".join("%s=%d" % kv for kv in sorted(by_type.items())))
    print("=" * 62)

    clean = True
    for label, items in problems.items():
        if not items:
            continue
        clean = False
        print("\n【%s】%d 条" % (label, len(items)))
        for p in items[:12]:
            print("   " + p)
        if len(items) > 12:
            print("   …… 还有 %d 条" % (len(items) - 12))

    if clean:
        print("\n✅ 没发现问题，元数据都是全的")
    else:
        print("\n" + "=" * 62)
        print("以上只是报告，一个字节都没改。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
