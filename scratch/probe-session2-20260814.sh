#!/bin/bash
echo "=== Core sessions.db 8-13 16:00 UTC 之后（=北京 8-14 00:00 后）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT session_id, substr(created_at,12,8) AS t, role, substr(text,1,40) FROM messages WHERE created_at >= '2026-08-13T15:30' ORDER BY id;" 2>&1 | head -40
echo
echo "=== Core sessions 里 1809ea16 存在吗 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT id, created_at, updated_at, title FROM sessions WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b';" 2>&1
echo
echo "=== Core sessions 全部（按 updated_at desc 前 8）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT substr(id,1,16), updated_at, substr(title,1,30) FROM sessions ORDER BY updated_at DESC LIMIT 8;" 2>&1
echo
echo "=== Core messages 表总行数 / 1809ea16 行数 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages; SELECT COUNT(*) FROM messages WHERE session_id='1809ea16d4f74dd4a46a291ce1e4a84b';" 2>&1
