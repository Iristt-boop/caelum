#!/bin/bash
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
echo "=== 修前：库里条数（应为 63）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='$SID';"
echo "=== 发一轮真实对话（走 bridge 公网路径）==="
curl -s -X POST https://noxtang.com/api/chat \
  -H "Content-Type: application/json" -H "X-Nox-Token: $TOK" \
  -d "{\"message\":\"验证记忆链路，回复简短点\",\"session_id\":\"$SID\"}" | head -c 300
echo
sleep 2
echo "=== 修后：库里条数（应 > 63，多了 user+assistant 两条）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='$SID';"
echo "=== 最新几条（确认写进去了）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT seq, role, substr(text,1,30) FROM messages WHERE session_id='$SID' ORDER BY seq DESC LIMIT 3;"
echo "=== sessions.updated_at（应刷新到今天）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT updated_at FROM sessions WHERE id='$SID';"
