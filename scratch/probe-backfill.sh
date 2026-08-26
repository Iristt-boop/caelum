#!/bin/bash
echo "=== 1. 主会话 bridge 消息按天分布（看要回填多少）==="
sqlite3 /root/data/nox-bridge.db "SELECT substr(timestamp,1,10) AS d, COUNT(*), SUM(role='user'), SUM(role='assistant') FROM conversations WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b' GROUP BY d ORDER BY d;" 2>&1
echo
echo "=== 2. 主会话 Core 已存消息按天分布 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT substr(created_at,1,10) AS d, COUNT(*) FROM messages WHERE session_id='1809ea16d4f74dd4a46a291ce1e4a84b' GROUP BY d ORDER BY d;" 2>&1
echo
echo "=== 3. bridge 主会话最早/最晚时间 ==="
sqlite3 /root/data/nox-bridge.db "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM conversations WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b';" 2>&1
echo
echo "=== 4. bridge 主会话里有哪些特殊 metadata（看要不要过滤）==="
sqlite3 /root/data/nox-bridge.db "SELECT DISTINCT substr(metadata,1,50) FROM conversations WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b' AND metadata != '' LIMIT 10;" 2>&1
echo
echo "=== 5. 其他会话 8-08 之后也有丢失吗（全表看）==="
sqlite3 /root/data/nox-bridge.db "SELECT id, COUNT(*) FROM conversations WHERE timestamp >= '2026-08-07T12:30' GROUP BY id ORDER BY COUNT(*) DESC LIMIT 6;" 2>&1
