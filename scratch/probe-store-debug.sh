#!/bin/bash
echo "=== nox-core 最近 40 行日志（找 chat/stream 和 sync 相关）==="
journalctl -u nox-core -n 40 --no-pager | grep -iE "chat|sync|锚点|append|落盘|恢复会话|error|exception|warning" | tail -25
echo
echo "=== 直接测 Core 的 /chat/stream（不走 bridge，看落库）==="
SID="probe-store-test-20260814"
curl -s -N -X POST http://127.0.0.1:8100/chat/stream \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"测试落库\",\"session_id\":\"$SID\"}" | tail -3
sleep 1
echo "=== 该测试会话条数（应为 2）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='$SID';"
echo "=== 该测试会话内容 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT seq, role, substr(text,1,25) FROM messages WHERE session_id='$SID' ORDER BY seq;"
