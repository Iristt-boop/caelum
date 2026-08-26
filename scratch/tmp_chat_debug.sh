#!/bin/bash
echo "=== Bridge 服务状态 ==="
systemctl status bridge --no-pager | head -3

echo ""
echo "=== 最近活跃会话的最后消息 ==="
python3 << 'PYEOF'
import sqlite3
db = sqlite3.connect("/root/data/nox-bridge.db")
# 找最近的会话
rows = db.execute("""
    SELECT id, role, substr(content,1,80), timestamp
    FROM conversations
    WHERE id NOT LIKE 'test-%'
    ORDER BY rowid DESC
    LIMIT 8
""").fetchall()
for r in rows:
    print(f"{r[3][:19]} | {r[1]:10s} | {r[0][:12]}... | {r[2]}")
PYEOF

echo ""
echo "=== 用 token 直接调 /api/messages（最近会话） ==="
SID=$(python3 -c "
import sqlite3
db = sqlite3.connect('/root/data/nox-bridge.db')
r = db.execute(\"SELECT id FROM conversations WHERE id NOT LIKE 'test-%' ORDER BY rowid DESC LIMIT 1\").fetchone()
print(r[0] if r else 'none')
")
echo "Session: $SID"
curl -s -H "X-Nox-Token: REDACTED-BRIDGE-TOKEN" "http://localhost:3003/api/messages?sessionId=$SID" | python3 -c "
import sys,json
msgs = json.load(sys.stdin)
print(f'返回 {len(msgs)} 条消息')
if msgs:
    print(f'第一条: {msgs[0].get(\"role\",\"?\")} | {msgs[0].get(\"content\",\"\")[:60]}')
    print(f'最后一条: {msgs[-1].get(\"role\",\"?\")} | {msgs[-1].get(\"content\",\"\")[:60]}')
"
