#!/bin/bash
# full public-path probe: https -> caddy -> bridge -> core
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
echo "=== public path chat (https://noxtang.com/api/chat) ==="
curl -s -o /tmp/pubchat.out -w 'code=%{http_code}\n' -X POST https://noxtang.com/api/chat \
  -H "Content-Type: application/json" -H "X-Nox-Token: $TOK" \
  -d '{"message":"公网链路测试","session_id":"probe-public-20260814"}'
head -c 300 /tmp/pubchat.out; echo
echo "=== public conv-sessions ==="
curl -s -o /tmp/pubconv.out -w 'code=%{http_code}\n' https://noxtang.com/api/conv-sessions -H "X-Nox-Token: $TOK"
head -c 120 /tmp/pubconv.out; echo
echo "=== bridge log right after (should show Chat request) ==="
journalctl -u bridge -n 6 --no-pager | tail -6
