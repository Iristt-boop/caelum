#!/bin/bash
echo "=== caddy journal today: ALL requests (non-MCP) ==="
journalctl -u caddy --since '2026-08-14 00:00' --no-pager 2>/dev/null | grep -E '"method"|"uri"' | grep -vE '/mcp|/health|/tracker|/.well-known' | tail -30
echo
echo "=== caddy today: request count by uri (top 15) ==="
journalctl -u caddy --since '2026-08-14 00:00' --no-pager 2>/dev/null | grep -oE '"uri":"[^"]*"' | sort | uniq -c | sort -rn | head -15
echo
echo "=== public curl: https://noxtang.com/api/health with bad token ==="
curl -s -o /dev/null -w 'code=%{http_code}\n' https://noxtang.com/api/health -H 'X-Nox-Token: bad'
echo "=== public curl: main page ==="
curl -s -o /dev/null -w 'code=%{http_code} size=%{size_download}\n' https://noxtang.com/
echo "=== bridge journal: any /api/ at all today ==="
journalctl -u bridge --since '2026-08-14 00:00' --no-pager 2>/dev/null | grep -iE 'api/|request|error|warn' | tail -20
echo "=== bridge uptime ==="
systemctl show bridge -p ActiveEnterTimestamp -p ExecMainStartTimestamp
