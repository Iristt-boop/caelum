#!/bin/bash
# verify bridge API with real token WITHOUT printing it
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
if [ -z "$TOK" ]; then
  TOK=$(cat /proc/$(systemctl show bridge -p MainPID | cut -d= -f2)/environ 2>/dev/null | tr '\0' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
fi
echo "token length: ${#TOK}"
echo "=== /api/auth/verify ==="
curl -s -o /tmp/verify.out -w 'code=%{http_code}\n' http://127.0.0.1:3003/api/auth/verify -H "X-Nox-Token: $TOK"
head -c 200 /tmp/verify.out; echo
echo "=== /api/conv-sessions ==="
curl -s -o /tmp/conv.out -w 'code=%{http_code}\n' http://127.0.0.1:3003/api/conv-sessions -H "X-Nox-Token: $TOK"
head -c 300 /tmp/conv.out; echo
echo "=== /api/chat non-stream (probe, short) ==="
curl -s -o /tmp/chat.out -w 'code=%{http_code}\n' -X POST http://127.0.0.1:3003/api/chat \
  -H "Content-Type: application/json" -H "X-Nox-Token: $TOK" \
  -d '{"message":"测试接口通不通，回一个词就行","session_id":"probe-20260814"}'
head -c 400 /tmp/chat.out; echo
echo "=== nox-core recent sessions ==="
curl -s http://127.0.0.1:8100/sessions | head -c 300; echo
