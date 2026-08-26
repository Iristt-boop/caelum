#!/bin/bash
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
echo "=== 服务状态 ==="
echo "bridge: $(systemctl is-active bridge)"
echo "nox-core: $(systemctl is-active nox-core)"
echo
echo "=== 聊天链路还通吗（发一条真消息）==="
curl -s -N -X POST https://noxtang.com/api/chat \
  -H "Content-Type: application/json" -H "X-Nox-Token: $TOK" \
  -d "{\"message\":\"统一开口闸测试，回个短句\",\"sessionId\":\"$SID\"}" | grep -o '"type":"done"[^}]*' | tail -1
echo
echo "=== /health 确认 attention 里有 gate ==="
curl -s http://127.0.0.1:8100/health | python3 -c "import json,sys; d=json.load(sys.stdin); a=d.get('attention'); print('attention gate:', a.get('gate') if a else None)" 2>/dev/null || echo "（attention 快照可能没有 gate 字段，看日志确认）"
echo
echo "=== Care 链路日志（重启后有无报错）==="
journalctl -u bridge -n 8 --no-pager | grep -iE "Care|error|failed" | tail -3
