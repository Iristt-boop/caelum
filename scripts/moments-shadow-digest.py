#!/usr/bin/env python3
"""Moments 的当日汇总 —— **跑在 VPS 上**，读 journalctl + bridge 的库，打一份人话报告。

⚠️ 2026-09-23 Moments 转 on（她拍板），这份从「shadow 汇总」改成日报：
on 的时候主指标是「真发了几条、发了什么」（正文从库里读全文），
库里有 moment 是正常的；只有**全是 shadow 的那天**库里出现帖子才算 bug。
文件名没改（本机计划任务 Caelum-Moments-Shadow 认的是这个路径）。

用法（本地）：
    scp scripts/moments-shadow-digest.py root@noxtang.com:/root/moments-shadow-digest.py
    ssh root@noxtang.com 'python3 /root/moments-shadow-digest.py --since "24 hours ago"'

## 这份汇总要回答的就两个问题（设计文档第九节）

    1. 「他什么时候会想发一条」准不准
    2. 「发出来的东西」像不像他自言自语      ← 2026-09-18 起 shadow 也真生成，
       正文打在行尾 `｜body=<正文>`（不落帖，但看得到他会写什么）

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
from datetime import datetime
from statistics import median

#: 一行 shadow 记录里那几个**不会随措辞变**的键
RE_MODE = re.compile(r"mode=(\w+)")
RE_AT = re.compile(r"Moments｜([^｜]+)｜")
RE_DRIVES = re.compile(r"drives=(\{[^}]*\})")
RE_VALUE = re.compile(r"value=([\d.]+) inner=([\d.]+) timing=([\d.]+)")
RE_GATE = re.compile(r"threshold=([\d.]+) dice=(\S+) p=(\S+)")
RE_OUT = re.compile(r"posted=(True|False)(?: reason=(\w+))?")
#: 行尾的正文（`MomentRecord.log()` 追加的那一段）。`-` = 这一 tick 没有正文
RE_BODY = re.compile(r"｜body=(.*)$")

#: drive 名 → 人话。**直接读线上 nox-core 的那一张**（2026-09-23：原来这里抄了一份，
#: 醋意/委屈加进来时没跟上 —— 她说过「总不能加一个情绪就要这样维护一个，会乱的」）。
#: 读不到（代码目录换了）就显示原 key，只影响显示，不参与任何判据。
NOX_CODE = "/root/nox-core/code"
try:
    sys.path.insert(0, NOX_CODE)
    from attention.resonance import DRIVE_WORDS  # noqa: E402
except Exception:  # noqa: BLE001
    DRIVE_WORDS = {}
#: 不参与 Moments 冲动和心情的情绪（同 moments/impulse.py 的 EXCLUDED_FROM_INNER）。
#: 「领头的心事」要按 Moments 自己的算法数 —— 不然报告说 concern 领头，
#: 而 concern 09-21 起根本没参与，报告就在说谎
NOT_IN_MOMENTS = {"concern"}

#: 报告里每条正文截到多少字（给人扫的，不是全文归档）
BODY_CLIP_CHARS = 80
#: 报告里最多列几条
BODY_LIMIT = 5
#: 解析不到正文时写这一句 —— **不许打一个空标题**
NO_BODY_PLACEHOLDER = "（这一段还没有数据）"

#: reason → 人话。和 `moments/record.py` 的 NOT_POSTED_REASONS 一一对应；
#: 表外的 reason 原样打印（以后加了新原因，这里不改也不会拼出半句话）
REASON_WORDS = {
    "below_threshold": "没到阈值（心里没那么多事，或者时机不对）",
    "dice": "过了阈值，骰子没中",
    "daily_cap": "今天两条发满了",
    "min_gap": "距上一条不到 3 小时",
    "shadow": "🌱 本来就发出去了（只是 shadow 不落帖）",
    "write_failed": "⚠️ 生成或落库失败（on 下记欠账，一小时内下一 tick 补写）",
    "posted": "🌱 发出去了",
}
#: 日志里欠账补写的那一行（moments/loop.py）
OWED_MARK = "补上次没写出来的那条"

DB = "/root/data/nox-bridge.db"


def read_lines(since: str, unit: str, only_records: bool = True) -> list[str]:
    out = subprocess.run(
        ["journalctl", "-u", unit, "--since", since, "--no-pager", "-o", "cat"],
        capture_output=True, text=True, errors="replace",
    )
    #: journalctl 本身失败（unit 名写错 / 没权限）和「这段时间没有记录」
    #: 是两回事，必须分开报 —— 混在一起就又是一次「空集当通过」
    if out.returncode != 0:
        print(f"journalctl 读不到（退出码 {out.returncode}）：{out.stderr.strip()[:200]}")
        sys.exit(3)
    lines = out.stdout.splitlines()
    if only_records:
        return [ln for ln in lines if "Moments｜" in ln]
    return [ln for ln in lines if "moments." in ln]


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
    a = RE_AT.search(line)
    b = RE_BODY.search(line)
    return {
        "mode": m.group(1) if m else "?",
        "at": a.group(1) if a else "",
        "drives": drives,
        "value": float(v.group(1)),
        "inner": float(v.group(2)),
        "timing": float(v.group(3)),
        "threshold": float(g.group(1)),
        "posted": o.group(1) == "True",
        "reason": o.group(2) or "",
        #: `body=-` 是「没有正文」的占位，不是一条叫 `-` 的帖子
        "body": (b.group(1).strip() if b and b.group(1).strip() != "-" else ""),
    }


def _body_timestamp(at: str) -> str:
    """ISO 时刻 → `MM-DD HH:MM`。看不懂就原样给前 16 个字符。"""
    try:
        return datetime.fromisoformat(at).strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return at[:16]


def format_recent_bodies(recs: list[dict], limit: int = BODY_LIMIT) -> list[str]:
    """「本来会发出去」的正文，排成人能扫的几行。**没有就返回 []**。

    只算 `reason="shadow"`（过了阈值、没撞上限也没撞间隔、骰子也中了）——
    那才是「要不是 shadow，这一刻他就发出去了」。每行形如：

        [09-18 00:18] 担心她领头 —— 「……」
    """
    lines: list[str] = []
    wanted = [
        r for r in recs
        if r.get("reason") == "shadow" and str(r.get("body") or "").strip()
    ][-limit:]
    for r in wanted:
        drives = r.get("drives") or {}
        lead = max(drives.items(), key=lambda kv: kv[1])[0] if drives else ""
        label = f"{DRIVE_WORDS.get(lead, lead)}领头" if lead else ""
        body = str(r["body"]).strip()[:BODY_CLIP_CHARS]
        when = _body_timestamp(str(r.get("at") or ""))
        prefix = f"  [{when}] " if when else "  "
        lines.append(f"{prefix}{label} —— 「{body}」" if label else
                     f"{prefix}「{body}」")
    return lines


def posts_in_db(hours: float) -> list[dict] | None:
    """最近 `hours` 小时他真发出去的帖子（全文 + 当时抽到的心情）。读不到返回 None。"""
    try:
        from datetime import timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = c.execute(
            "SELECT created_at, drive, body FROM diary WHERE kind='moment' AND author='Nox' "
            "AND created_at >= ? ORDER BY created_at", (since,)).fetchall()
        c.close()
        return [{"at": a, "drive": d or "", "body": b or ""} for a, d, b in rows]
    except Exception as exc:  # noqa: BLE001
        print(f"（读不到 bridge 的库：{type(exc).__name__}: {exc}）")
        return None


def _cst(iso: str) -> str:
    """库里是 UTC（`...Z`），她看的是本地时间。"""
    try:
        from datetime import timedelta, timezone
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return t.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return iso[:16]


def _hours(since: str) -> float:
    """`--since '24 hours ago'` → 24。认不出就按 24（只影响库里那段的时间窗）。"""
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*hours?\s+ago", since)
    return float(m.group(1)) if m else 24.0


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
    raw_all = read_lines(args.since, args.unit, only_records=False)

    all_shadow = bool(recs) and all(r["mode"] == "shadow" for r in recs)
    title = "shadow 汇总" if all_shadow else "日报"
    print(f"── Moments {title}（{args.since} 起）──")

    # 🔴 空集不是通过
    if len(recs) < args.min_ticks:
        print(f"只解析到 {len(recs)} 条记录（原始行 {len(raw)} 条），"
              f"少于 {args.min_ticks} 条 —— **这次不下结论**。")
        print("可能是：日志轮转了 / 服务刚重启 / 循环根本没在跑 / 日志级别被调低了。")
        print("先去看：systemctl is-active nox-core；curl /health 里 background.post_tick 的 count")
        return 2

    modes = Counter(r["mode"] for r in recs)
    reasons = Counter(r["reason"] or ("posted" if r["posted"] else "?") for r in recs)
    total = len(recs)
    wanted = sum(1 for r in recs if r["value"] >= r["threshold"])
    would = reasons.get("shadow", 0)
    real = sum(1 for r in recs if r["posted"])
    failed = reasons.get("write_failed", 0)
    owed = sum(1 for ln in raw_all if OWED_MARK in ln)

    print(f"跑了 {total} 个 tick｜模式 {dict(modes)}")
    print()
    print(f"  想发（冲动过阈值）      {wanted:>3} 次   {100*wanted/total:.0f}%")
    if all_shadow:
        print(f"  🌱 本来会发出去         {would:>3} 次   ← 这就是「他一天想发几条」")
    print(f"  🌱 真发了               {real:>3} 条")
    print(f"  ⚠️ 写不出来             {failed:>3} 次"
          + (f"（下一 tick 补写了 {owed} 次）" if owed else ""))
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
        scored = {k: v for k, v in (r["drives"] or {}).items() if k not in NOT_IN_MOMENTS}
        if scored:
            lead[max(scored.items(), key=lambda kv: kv[1])[0]] += 1
    if lead:
        print("领头的心事（不算担心，同 Moments 的算法）："
              + "、".join(f"{DRIVE_WORDS.get(k, k)} {n}次" for k, n in lead.most_common(5)))

    exit_code = 0
    print()
    if all_shadow:
        counts = moments_in_db()
        if counts is None:
            print("⚠️ 库里的条数没验到 —— 「shadow 没偷偷落帖」这条今天**没有**被确认")
        else:
            moment, diary = counts
            ok = "✅" if moment == 0 else "🔴"
            print(f"{ok} 库里 moment {moment} 条 / diary {diary} 条"
                  + ("（shadow 期间 moment 必须是 0）" if moment == 0
                     else " —— **shadow 落帖了，这是 bug，去看 moments/record.py 的结构闸门**"))
            if moment:
                exit_code = 1
        print()
        print("最近几条「本来会发出去」的正文：")
        for line in format_recent_bodies(recs) or [f"  {NO_BODY_PLACEHOLDER}"]:
            print(line)
        return exit_code

    #: on：帖子以库为准（全文 + 当时抽到的心情），日志里的「发了」要和库对得上
    posts = posts_in_db(_hours(args.since))
    print("这段时间他发的帖子（库里，全文）：")
    if posts is None:
        print("  ⚠️ 读不到库，帖子没验到")
        return 4
    if not posts:
        print("  （一条都没有）")
    for p in posts:
        mood = DRIVE_WORDS.get(p["drive"], p["drive"]) if p["drive"] else "?"
        print(f"  [{_cst(p['at'])}] 心情：{mood}")
        for ln in p["body"].strip().splitlines():
            if ln.strip():
                print(f"      {ln.strip()}")
    if len(posts) != real:
        #: 日志说发了 N 条、库里是 M 条：要么落库断了，要么有别的东西在往里写
        print(f"  ⚠️ 日志里记的「发了」是 {real} 条，库里是 {len(posts)} 条，对不上")
        exit_code = 4
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
