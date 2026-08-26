#!/bin/bash
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
echo "=== 8-14 21:20 之后 Core 新增的会话 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT substr(id,1,20), created_at FROM sessions WHERE created_at > '2026-08-14T13:20' ORDER BY created_at;" 2>&1
echo "=== bridge 最新消息落在哪个会话 ==="
sqlite3 /root/data/nox-bridge.db "SELECT id, substr(timestamp,12,8), role, substr(content,1,25) FROM conversations WHERE timestamp > '2026-08-14T13:20' ORDER BY rowid;" 2>&1 | tail -8
echo "=== 那条 curl bridge 的消息（验证记忆链路）在 Core 里吗 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT session_id, role, substr(text,1,25) FROM messages WHERE text LIKE '%验证记忆链路%' ORDER BY id;" 2>&1
