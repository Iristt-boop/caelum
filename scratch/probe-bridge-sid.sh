#!/bin/bash
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
echo "=== 直接调 bridge /api/chat 带 session_id=1809ea16，看 done 帧返回什么 id ==="
curl -s -N -X POST https://noxtang.com/api/chat \
  -H "Content-Type: application/json" -H "X-Nox-Token: $TOK" \
  -d '{"message":"链路验证三","session_id":"1809ea16d4f74dd4a46a291ce1e4a84b"}' | grep -o '"type":"done"[^}]*' | tail -1
echo
echo "=== Core 里刚才那条 '链路验证三' 落在哪个会话 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT session_id, substr(text,1,15) FROM messages WHERE text LIKE '%链路验证三%';" 2>&1
echo "=== bridge conversations 里它落在哪 ==="
sqlite3 /root/data/nox-bridge.db "SELECT id, role, substr(content,1,15) FROM conversations WHERE content LIKE '%链路验证三%';" 2>&1
