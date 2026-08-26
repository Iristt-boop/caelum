#!/bin/bash
echo "=== 8-14 早上所有消息，按会话分组（看 Care/晨报/早安 是否同 sid）==="
sqlite3 /root/data/nox-bridge.db "SELECT id, substr(timestamp,12,8) AS t, role, substr(content,1,30) FROM conversations WHERE timestamp >= '2026-08-13T23:00' AND timestamp < '2026-08-14T03:30' ORDER BY rowid;" 2>&1
echo
echo "=== Core sessions.db 8-14 早上的消息（模型真正读的上下文）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT session_id, substr(created_at,12,8) AS t, role, substr(text,1,30) FROM messages WHERE created_at >= '2026-08-13T23:00' ORDER BY id;" 2>&1 | head -30
echo
echo "=== 各会话最近更新时间 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT id, updated_at, title FROM sessions ORDER BY updated_at DESC LIMIT 5;" 2>&1
