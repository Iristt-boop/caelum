# -*- coding: utf-8 -*-
"""把 topics.db 的账拉成可读报告（昨天抓了什么）。只读。"""
import sqlite3

c = sqlite3.connect('/root/nox-core/data/topics.db')
c.row_factory = sqlite3.Row
out = []
w = out.append

w("== 候选缓存（48h 滚动窗口）==")
n = c.execute("select count(*) from candidates").fetchone()[0]
w(f"总共 {n} 条")
r = c.execute("select min(fetched_at), max(fetched_at) from candidates").fetchone()
w(f"抓取时间范围：{r[0][:16]} ~ {r[1][:16]} UTC")

w("\n按来源：")
for r in c.execute("select source, count(*) n from candidates group by source order by n desc"):
    w(f"  {r['source']}: {r['n']}")

w("\n按方向：")
for r in c.execute("select category, count(*) n from candidates group by category order by n desc"):
    w(f"  {r['category']}: {r['n']}")

w("\n== 池子 topics（全部状态）==")
rows = c.execute(
    "select status, origin, category, hook, source_title, observed_at, expires_at, relevance"
    " from topics order by observed_at").fetchall()
w(f"共 {len(rows)} 条")
for r in rows:
    w(f"[{r['status']}|{r['origin']}|{r['category']}|rel={r['relevance']}] {r['hook']}")
    w(f"    来源：{r['source_title']}")
    w(f"    进池：{r['observed_at'][:16]}Z  过期：{(r['expires_at'] or '')[:16]}Z")

w("\n== 候选抽样（每方向最新 6 条标题）==")
for (cat,) in c.execute("select distinct category from candidates order by category"):
    w(f"\n[{cat}]")
    for r in c.execute(
            "select title, source from candidates where category=? order by id desc limit 6",
            (cat,)):
        w(f"  · ({r['source']}) {r['title'][:80]}")

print("\n".join(out))
