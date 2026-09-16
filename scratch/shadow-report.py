"""把影子模式一周多的日志整理成一页给她看。

判据只挑数字和结构，不匹配中文（编码一变就永远为假 —— deploy.ps1 那个坑）。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLOOR = 0.25  # context/providers/understanding.py:57 —— 低于它进不了他的上下文

LINE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T.*意义推断：(?P<subject>[^｜]+)｜(?P<kind>[^ ]+)"
    r" → (?P<meaning>.+?)（(?P<strength>[\d.]+)，确信 (?P<conf>[\d.]+)）\s*$"
)

rows = []
unparsed = []
for raw in (HERE / "shadow-log.txt").read_text(encoding="utf-8").splitlines():
    m = LINE.match(raw.strip())
    if m:
        d = m.groupdict()
        d["strength"] = float(d["strength"])
        d["conf"] = float(d["conf"])
        rows.append(d)
    elif raw.strip():
        unparsed.append(raw.strip())

print(f"解析成功 {len(rows)} 条，解析不了 {len(unparsed)} 条")
# 空集不是通过：太少就别下结论
if len(rows) < 20:
    print("🔴 样本少于 20 条，不下结论")
    for u in unparsed[:5]:
        print("  ?", u[:160])
    raise SystemExit(1)

print(f"覆盖 {min(r['date'] for r in rows)} ~ {max(r['date'] for r in rows)}")
print()

print("── 按 kind 分布 ──")
for k, n in Counter(r["kind"] for r in rows).most_common():
    print(f"  {k:<12} {n:>3}")
print()

above = [r for r in rows if r["strength"] >= FLOOR]
print(f"── 转正之后会发生什么（FLOOR={FLOOR}）──")
print(f"  写进 Registry 的锚点：{len(rows)} 条")
print(f"  其中够得着他上下文的：{len(above)} 条（strength ≥ {FLOOR}）")
print(f"  低于门槛、写了也看不见：{len(rows) - len(above)} 条")
print()

print("── 强度分布 ──")
buckets = Counter()
for r in rows:
    s = r["strength"]
    buckets["0.00–0.24（看不见）" if s < 0.25 else
            "0.25–0.39" if s < 0.40 else
            "0.40–0.59" if s < 0.60 else "0.60+"] += 1
for b in ["0.00–0.24（看不见）", "0.25–0.39", "0.40–0.59", "0.60+"]:
    if buckets[b]:
        print(f"  {b:<18} {buckets[b]:>3}")
print()

print("── 置信度 ──")
confs = sorted(r["conf"] for r in rows)
print(f"  最低 {confs[0]:.2f}｜中位 {confs[len(confs)//2]:.2f}｜最高 {confs[-1]:.2f}")
print(f"  低于 0.7 的：{sum(1 for c in confs if c < 0.7)} 条")
print()

print("── 强度最高的 12 条（转正后最会影响他的）──")
for r in sorted(rows, key=lambda r: -r["strength"])[:12]:
    print(f"  [{r['date'][5:]}] {r['strength']:.2f}/{r['conf']:.2f} {r['kind']:<10} "
          f"{r['subject'][:18]}")
    print(f"        → {r['meaning'][:70]}")
print()

print("── 最近 8 条（看它现在判得怎么样）──")
for r in rows[-8:]:
    print(f"  [{r['date'][5:]}] {r['strength']:.2f}/{r['conf']:.2f} {r['kind']:<10} "
          f"{r['subject'][:18]}")
    print(f"        → {r['meaning'][:70]}")

if unparsed:
    print(f"\n── 解析不了的 {len(unparsed)} 条（格式可能变过）──")
    for u in unparsed[:5]:
        print("  ", u[:160])
