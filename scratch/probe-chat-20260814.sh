#!/bin/bash
# probe chat path 2026-08-14
echo "=== find caddy logs ==="
ls -la /var/log/caddy/ 2>/dev/null || echo "no /var/log/caddy"
echo "=== caddy journal today: chat/post ==="
journalctl -u caddy --since '2026-08-14 00:00' --no-pager 2>/dev/null | grep -iE 'chat|post|403|401' | tail -15
echo "=== curl bridge /api/chat with wrong token (expect 403) ==="
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:3003/api/chat \
  -H 'Content-Type: application/json' \
  -H 'X-Nox-Token: probe-this-is-not-the-token' \
  -d '{"message":"ping"}'
echo "=== bridge /api/health ==="
curl -s http://127.0.0.1:3003/api/health | head -c 300
echo
echo "=== nox-core /health ==="
curl -s http://127.0.0.1:8100/health | head -c 300
echo
echo "=== ss listening ports ==="
ss -lntp | grep -E ':3003|:8100|:80 |:443' | head
