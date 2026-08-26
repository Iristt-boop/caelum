#!/bin/bash
echo "=== nox-core 完整启动日志（看是否就绪）==="
journalctl -u nox-core -n 30 --no-pager | tail -20
echo
echo "=== 等 15 秒再试 ==="
sleep 15
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
curl -s -N -X POST https://noxtang.com/api/chat \
  -H "Content-Type: application/json" -H "X-Nox-Token: $TOK" \
  -d "{\"message\":\"我们8月9号聊过什么特别的事吗\",\"sessionId\":\"$SID\"}" | head -c 800
echo
echo "=== 这轮落库了吗（应 477）==="
sleep 1
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='$SID';"
