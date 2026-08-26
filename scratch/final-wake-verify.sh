#!/bin/bash
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
echo "=== 1. bridge 里 Care 定时器删了吗（应无输出）==="
grep -c "lastProactiveAt\|沉默.*4" /root/bridge/server.js || echo "0 处（已删干净）"
echo
echo "=== 2. 聊天链路正常（发一条真消息）==="
curl -s -N -X POST https://noxtang.com/api/chat \
  -H "Content-Type: application/json" -H "X-Nox-Token: $TOK" \
  -d "{\"message\":\"唤醒重构测试，回个短句\",\"sessionId\":\"$SID\"}" | grep -o '"type":"done"[^}]*' | tail -1
echo
echo "=== 3. 服务状态 ==="
echo "bridge: $(systemctl is-active bridge) | nox-core: $(systemctl is-active nox-core)"
echo
echo "=== 4. 清理验证用的测试消息（Core 里刚那条）==="
sqlite3 /root/nox-core/data/sessions.db "DELETE FROM messages WHERE session_id='$SID' AND text LIKE '%唤醒重构测试%';"
sqlite3 /root/data/nox-bridge.db "DELETE FROM conversations WHERE id='$SID' AND content LIKE '%唤醒重构测试%';"
echo "已清"
echo
echo "=== 5. 清理临时脚本 ==="
rm -f /root/deploy-wake-engine.sh /root/enable-time-wakes.sh /root/verify-times-live.sh
echo "完成"
