"""把 `health.db` 的经期记录搬进 World Model（2026-08-19，糖糖拍板）。

## 为什么搬

那张表的数据源已经不可靠了：`shortcuts` 那条链只成功写进来两条，
其中 8-14 那条还写坏了（`flow_level` = `"未指定\\n未指定\\n\\n\\n\\n\\n\\n量少"`）。
7 月那六条整齐的记录全是 `manual_fix` —— 有人手工补的，不是同步来的。

以后经期一律走 World Model（`record_period` 工具 + App 入口）。
历史一起搬过去，否则周期分析没有历史就没意义 ——
7-19 到 8-14 那 26 天的间隔，是他将来能说出「你这次比上次晚了几天」的唯一依据。

## 不假装它们是新记的

每条都带上 `origin`（manual_fix / shortcuts）和 `migrated_at`。
以后翻出来能看出「这条是搬过来的，不是他当时记的」。

## 幂等

`dedup_key = menstrual/<date>/<event>`，和 `tools/record.py` 一致。
重复跑不会写第二遍，也不会覆盖她之后手动记的同一天。

用法（在服务器上）：

    cd /root/nox-core
    .venv/bin/python scripts/migrate_menstrual.py            # 只看要写什么
    .venv/bin/python scripts/migrate_menstrual.py --apply    # 真写
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from world_model import WorldModel  # noqa: E402

LOCAL_TZ = timezone(timedelta(hours=8))
HEALTH_DB = "/root/data/health.db"
WORLD_DB = "/root/nox-core/data/world.db"

#: 快捷指令把多个字段拼一起写坏了，形如
#: "未指定\n未指定\n\n\n\n\n\n量少" —— 取最后一个非空、且不是「未指定」的段
_JUNK = {"", "未指定", "无", "none", "None"}


def clean_flow(raw: str | None) -> str:
    """把写坏的 flow_level 收拾成一个词。收拾不出来就返回空字符串。

    ⚠️ **不猜**。只做「挑出真正的那一段」，不做同义词归一
    （「量少」和「少量」是她自己的两种说法，都保留原样）。
    """
    parts = [p.strip() for p in re.split(r"[\n\r]+", str(raw or ""))]
    good = [p for p in parts if p not in _JUNK]
    return good[-1] if good else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的写入，缺省只打印")
    ap.add_argument("--health-db", default=HEALTH_DB)
    ap.add_argument("--world-db", default=WORLD_DB)
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.health_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM menstrual WHERE date IS NOT NULL ORDER BY date")]
    con.close()

    if not rows:
        print("menstrual 表是空的，没什么可搬。")
        return 0

    # ---- 先把「假的周期开始」揪出来 ----
    #
    # 2026-08-19 实测：7-23 那条（shortcuts 写的）自称是新周期第 1 天，
    # 但它夹在 7-19 那个周期中间 —— 前一天是第 3 天、后一天是「最后一天」。
    # 那四条是 manual_fix，糖糖亲手确认过的。
    #
    # **冲突时信手工确认的，不信那条已经证明会写坏数据的同步链**
    # （8-14 那条的 flow_level 就是证据）。糖糖 2026-08-19 拍的板：
    # 「那天本来就应该是 day」。
    #
    # 规则：一个周期的起始日如果**落在另一个周期的日期范围之内**，
    # 它就不是真的开始 —— 降成 day，那天的流量观察保留。
    spans: dict[str, tuple[str, str]] = {}
    for r in rows:
        cs = r.get("cycle_start") or r["date"]
        lo, hi = spans.get(cs, (r["date"], r["date"]))
        spans[cs] = (min(lo, r["date"]), max(hi, r["date"]))

    spurious: set[str] = set()
    for cs, (lo, hi) in spans.items():
        for other, (o_lo, o_hi) in spans.items():
            if other != cs and o_lo < cs <= o_hi:
                spurious.add(cs)
                print(f"⚠️  {cs} 自称新周期开始，但它落在 {o_lo}~{o_hi} 那个周期里 —— 降成 day")
    if spurious:
        print()

    # 每个周期的最后一天 → end 事件。靠 cycle_start 分组，取 day_number 最大那条
    last_of_cycle: dict[str, str] = {}
    for r in rows:
        cs = r.get("cycle_start") or r["date"]
        if cs in spurious:
            continue
        prev = last_of_cycle.get(cs)
        if prev is None or (r.get("day_number") or 0) >= _daynum(rows, cs, prev):
            last_of_cycle[cs] = r["date"]

    world = WorldModel(args.world_db) if args.apply else None
    planned = []

    for r in rows:
        date = r["date"]
        cs = r.get("cycle_start") or date
        day_n = r.get("day_number")
        flow = clean_flow(r.get("flow_level"))
        note = (r.get("note") or "").strip()

        # 一天可能既是开始也是结束（只记了一天的那种），所以分开判、都写
        events = []
        if (day_n == 1 or cs == date) and cs not in spurious:
            events.append("start")
        if last_of_cycle.get(cs) == date and (day_n or 0) > 1:
            events.append("end")
        if not events:
            # 中间那些天：不是 start 也不是 end，但流量和备注有价值，
            # 记成 day（record_period 不产生这种，只有历史里有）
            events.append("day")

        for ev in events:
            observed = {
                "event": ev,
                "flow": flow,
                "date": date,
                "day_number": day_n,
                "cycle_start": cs,
                # **标明它是搬过来的**，别让以后的人以为是他当时记的
                "origin": r.get("source") or "unknown",
                "migrated": True,
            }
            if cs in spurious:
                # 留个痕：这天原本自称是周期开始，被降级了。
                # 以后谁翻出来能看见判断依据，不用重新推一遍
                observed["demoted_from"] = "start"
            if note:
                observed["note"] = note
            planned.append((date, ev, observed))

            if world is not None:
                world.observe(
                    source="migration",
                    type="menstrual",
                    observed=observed,
                    # 事情**发生**的时间是那一天，不是搬运的今天
                    observed_at=datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ),
                    dedup_key=f"menstrual/{date}/{ev}",
                )

    print(f"{'写入' if args.apply else '将写入'} {len(planned)} 条：\n")
    for date, ev, o in planned:
        bits = [f"第{o['day_number']}天" if o.get("day_number") else "",
                o.get("flow", ""), o.get("note", "")]
        detail = " · ".join(b for b in bits if b)
        print(f"  {date}  {ev:6} {detail}   ← {o['origin']}")

    if not args.apply:
        print("\n（这是预览。加 --apply 才真写）")
    return 0


def _daynum(rows: list[dict], cycle_start: str, date: str) -> int:
    for r in rows:
        if r["date"] == date and (r.get("cycle_start") or r["date"]) == cycle_start:
            return r.get("day_number") or 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
