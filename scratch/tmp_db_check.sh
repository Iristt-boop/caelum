#!/bin/bash
ls -la /root/nox-core/data/sessions.db
python3 << 'PYEOF'
import sqlite3
db = sqlite3.connect("/root/nox-core/data/sessions.db")
count = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
print(count, "个会话")
for row in db.execute("SELECT session_id, length(messages) FROM sessions ORDER BY updated_at DESC LIMIT 5"):
    print(row[0][:12], row[1], "条消息")
PYEOF
