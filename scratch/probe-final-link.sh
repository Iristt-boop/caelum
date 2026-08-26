#!/bin/bash
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
echo "=== 修后：主会话条数（现在 65）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='$SID';"
echo "=== bridge 全链路：传 sessionId（驼峰，和前端一致）==="
curl -s -N -X POST https://noxtang.com/api/chat \
  -H "Content-Type: application/json" -H "X-Nox-Token: $TOK" \
  -d "{\"message\":\"最终链路验证\",\"sessionId\":\"$SID\"}" | grep -o '"type":"done"[^}]*' | tail -1
sleep 1
echo "=== 主会话条数（应 67）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='$SID';"
echo "=== 最新 2 条 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT seq, role, substr(text,1,20) FROM messages WHERE session_id='$SID' ORDER BY seq DESC LIMIT 2;"
echo "=== bridge conversations 同会话最新 ==="
sqlite3 /root/data/nox-bridge.db "SELECT substr(id,1,16), role, substr(content,1,18) FROM conversations WHERE id='$SID' ORDER BY rowid DESC LIMIT 2;"
echo "=== updated_at 刷新了吗 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT updated_at FROM sessions WHERE id='$SID';"
