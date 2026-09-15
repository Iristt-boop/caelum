#!/usr/bin/env python3
"""Moments shadow 的当日汇总 —— **跑在 VPS 上**，读 journalctl，打一份人话报告。

用法（本地）：
    scp scripts/moments-shadow-digest.py root@noxtang.com:/root/moments-shadow-digest.py
    ssh root@noxtang.com 'python3 /root/moments-shadow-digest.py --since "24 hours ago"'

## 这份汇总要回答的就两个问题（设计文档第九节）

    1. 「他什么时候会想发一条」准不准
    2. 「发出来的东西」像不像他自言自语      ← 这个 shadow 阶段还看不到（不落帖）

所以这里数的核心是 **would_post**：过了阈值、没撞上限也没撞间隔、**骰子也中了**
的那些 tick —— 「要不是 shadow，这一刻他就发出去了」。
`reason=shadow` 就是这个意思（见 `moments/record.py` 的 NOT_POSTED_REASONS）。

## 🔴 空集不是通过

一条日志都没解析到时，**打「不下结论」并以退出码 2 退出**，不许打一份
漂亮的全零报告 —— 那正是 CAELUM-MAP 第三·五节第一个案例的形状
（查密钥历史「✅ 全部通过」，其实找到 0 条）。

journalctl 会轮转、服务会重启、日志级别会被人调低，
这几种情况下「0 条记录」和「一整天没想发」在报告里长得一模一样。

## 判据挑值，不挑中文

每行日志末尾那两段是给人读的中文，**措辞会变**。所以解析只认
`value=` / `reason=` / `posted=` / `threshold=` 这些 ASCII 键
（memory: dont-judge-success-by-text）。
"""
from __future__ import annotations

import argparse
import ast
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from statistics import median

#: 一行 shadow 记录里那几个**不会随措辞变**的键
RE_MODE = re.compile(r"mode=(\w+)")
RE_DRIVES = re.compile(r"drives=(\{[^}]*\})")
RE_VALUE = re.compile(r"value=([\d.]+) inner=([\d.]+) timing=([\d.]+)")
RE_GATE = re.compile(r"threshold=([\d.]+) dice=(\S+) p=(\S+)")
RE_OUT = re.compile(r"posted=(True|False)(?: reason=(\w+))?")

#: reason → 人话。和 `moments/record.py` 的 NOT_POSTED_REASONS 一一对应；
#: 表外的 reason 原样打印（以后加了新原因，这里不改也不会拼出半句话）
REASON_WORDS = {
    "below_threshold": "没到阈值（心里没那么多事，或者时机不对）",
    "dice": "过了阈值，骰子没中",
    "daily_cap": "今天两条发满了",
    "min_gap": "距上一条不到 3 小时",
    "shadow": "🌱 本来就发出去了（只是 shadow 不落帖）",
    "write_failed": "⚠️ 生成或落库失败",
}

DB = "/root/data/nox-bridge.db"


def read_lines(since: str, unit: str) -> list[str]:
    out = subprocess.run(
        ["journalctl", "-u", unit, "--since", since, "--no-pager", "-o", "cat"],
        capture_output=True, text=True, errors="replace",
    )
    #: journalctl 本身失败（unit 名写错 / 没权限）和「这段时间没有记录」
    #: 是两回事，必须分开报 —— 混在一起就又是一次「空集当通过」
    if out.returncode != 0:
        print(f"journalctl 读不到（退出码 {out.returncode}）：{out.stderr.strip()[:200]}")
        sys.exit(3)
    return [ln for ln in out.stdout.splitlines() if "Moments｜" in ln]


def parse(line: str) -> dict | None:
    v = RE_VALUE.search(line)
    o = RE_OUT.search(line)
    g = RE_GATE.search(line)
    if not (v and o and g):
        return None
    drives = {}
    d = RE_DRIVES.search(line)
    if d:
        try:
            drives = ast.literal_eval(d.group(1))
        except Exception:  # noqa: BLE001
            drives = {}
    m = RE_MODE.search(line)
    return {
        "mode": m.group(1) if m else "?",
        "drives": drives,
        "value": float(v.group(1)),
        "inner": float(v.group(2)),
        "timing": float(v.group(3)),
        "threshold": float(g.group(1)),
        "posted": o.group(1) == "True",
        "reason": o.group(2) or "",
    }


def moments_in_db() -> tuple[int, int] | None:
    """(moment 条数, diary 条数)。读不到返回 None —— **不要假装是 0**。"""
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = dict(c.execute("SELECT kind, count(*) FROM diary GROUP BY kind").fetchall())
        c.close()
        return int(rows.get("moment", 0)), int(rows.get("diary", 0))
    except Exception as exc:  # noqa: BLE001
        print(f"（读不到 bridge 的库：{type(exc).__name__}: {exc}）")
        return None


def bar(n: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return ""
    return "█" * max(0 if n == 0 else 1, round(width * n / total))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="24 hours ago")
    ap.add_argument("--unit", default="nox-core")
    #: 少于这个数就不下结论。一天 96 个 tick，重启一两次也该有几十条；
    #: 个位数几乎一定是日志被截断了，而不是「他一天只想了 3 次」
    ap.add_argument("--min-ticks", type=int, default=10)
    args = ap.parse_args()

    raw = read_lines(args.since, args.unit)
    recs = [r for r in (parse(ln) for ln in raw) if r]

    print(f"── Moments shadow 汇总（{args.since} 起）──")

    # 🔴 空集不是通过
    if len(recs) < args.min_ticks:
        print(f"只解析到 {len(recs)} 条记录（原始行 {len(raw)} 条），"
              f"少于 {args.min_ticks} 条 —— **这次不下结论**。")
        print("可能是：日志轮转了 / 服务刚重启 / 循环根本没在跑 / 日志级别被调低了。")
        print("先去看：systemctl is-active nox-core；curl /health 里 background.post_tick 的 count")
        return 2

    modes = Counter(r["mode"] for r in recs)
    reasons = Counter(r["reason"] or "posted" for r in recs)
    total = len(recs)
    wanted = sum(1 for r in recs if r["value"] >= r["threshold"])
    would = reasons.get("shadow", 0)
    real = sum(1 for r in recs if r["posted"])

    print(f"跑了 {total} 个 tick｜模式 {dict(modes)}")
    print()
    print(f"  想发（冲动过阈值）      {wanted:>3} 次   {100*wanted/total:.0f}%")
    print(f"  🌱 本来会发出去         {would:>3} 次   ← 这就是「他一天想发几条」")
    print(f"  真发了                  {real:>3} 次   （shadow 期间必须是 0）")
    print()
    print("每个 tick 卡在哪一步：")
    for reason, n in reasons.most_common():
        word = REASON_WORDS.get(reason, reason)
        print(f"  {n:>3}  {bar(n, total):<24} {word}")

    vals = [r["value"] for r in recs]
    print()
    print(f"冲动值：最低 {min(vals):.2f}｜中位 {median(vals):.2f}｜最高 {max(vals):.2f}"
          f"（阈值 {recs[-1]['threshold']:.2f}）")

    lead = Counter()
    for r in recs:
        if r["drives"]:
            lead[max(r["drives"].items(), key=lambda kv: kv[1])[0]] += 1
    if lead:
        print("领头的心事：" + "、".join(f"{k} {n}次" for k, n in lead.most_common(4)))

    counts = moments_in_db()
    print()
    if counts is None:
        print("⚠️ 库里的条数没验到 —— 「shadow 没偷偷落帖」这条今天**没有**被确认")
    else:
        moment, diary = counts
        ok = "✅" if moment == 0 else "🔴"
        print(f"{ok} 库里 moment {moment} 条 / diary {diary} 条"
              + ("（shadow 期间 moment 必须是 0）" if moment == 0
                 else " —— **shadow 落帖了，这是 bug，去看 moments/record.py 的结构闸门**"))
        if moment:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
