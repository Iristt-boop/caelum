#!/bin/bash
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
echo "=== 修前条数 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='$SID';"
echo "=== 直接调 Core /chat/stream（主会话）==="
curl -s -N -X POST http://127.0.0.1:8100/chat/stream \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"记忆链路第二次验证\",\"session_id\":\"$SID\"}" | tail -2
sleep 1
echo "=== 修后条数 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='$SID';"
echo "=== 最新 3 条 seq ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT seq, role, substr(text,1,20) FROM messages WHERE session_id='$SID' ORDER BY seq DESC LIMIT 3;"
echo "=== 本轮日志（落盘/锚点/异常）==="
journalctl -u nox-core -n 15 --no-pager | grep -iE "落盘|锚点|恢复会话|sync|append|WARNING|ERROR|Exception" | tail -10
