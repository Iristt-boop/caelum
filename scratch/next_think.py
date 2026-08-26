"""他下次几点想起她。只读 attention.db。"""
import json
import sqlite3
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
con = sqlite3.connect("/root/nox-core/data/attention.db")
try:
    rows = dict(con.execute("SELECT key, value FROM source_state").fetchall())
finally:
    con.close()

for key, label in (("source.thinking", "下次想起她"), ("source.presence", "上次看到她在")):
    raw = rows.get(key)
    if not raw:
        print(f"{label}: 还没排（快循环还没跑到第一轮）")
        continue
    d = json.loads(raw)
    if key == "source.thinking":
        at = datetime.fromisoformat(d["next_at"])
        left = (at - datetime.now(timezone.utc)).total_seconds() / 60
        print(f"{label}: {at.astimezone(CST):%H:%M:%S}（还有 {left:.0f} 分钟）")
    else:
        print(f"{label}: {d.get('last')}")
