#!/bin/bash
python3 << 'PYEOF'
import sqlite3
db = sqlite3.connect("/root/data/nox-bridge.db")
# 最近活跃的会话
rows = db.execute("""
    SELECT id, COUNT(*) as cnt, MAX(timestamp) as last_ts
    FROM conversations
    WHERE id NOT LIKE 'test-%'
    GROUP BY id
    ORDER BY last_ts DESC
    LIMIT 5
""").fetchall()
for r in rows:
    print(f"session={r[0][:24]}... | {r[1]}条 | 最后={r[2]}")
PYEOF
