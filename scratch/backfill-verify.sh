#!/bin/bash
echo "=== 重启 nox-core（清缓存，重读回填后的库）==="
systemctl restart nox-core
sleep 3
systemctl is-active nox-core
echo
echo "=== 回填后会话总条数 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='1809ea16d4f74dd4a46a291ce1e4a84b';"
echo
echo "=== 端到端：问他 8-09 的事（验证他记得回填的历史）==="
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
curl -s -N -X POST https://noxtang.com/api/chat \
  -H "Content-Type: application/json" -H "X-Nox-Token: $TOK" \
  -d "{\"message\":\"我们8月9号聊过什么特别的事吗\",\"sessionId\":\"$SID\"}" | head -c 600
echo
echo "=== 验证后这条消息也落库了（条数应 +2）==="
sleep 1
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='$SID';"
