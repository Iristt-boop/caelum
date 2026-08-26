#!/bin/bash
echo "=== dist index.html head ==="
head -c 1200 /root/frontend/dist/index.html 2>/dev/null || head -c 1200 /var/www/html/index.html 2>/dev/null || echo "no dist found at /root/frontend"
echo
echo "=== dist assets ==="
ls -la /root/frontend/dist/assets/ 2>/dev/null | head -15
echo "=== dist index.html mtime ==="
stat -c '%y %n' /root/frontend/dist/index.html 2>/dev/null
echo "=== which dist does caddy serve? check /root/frontend ==="
ls -la /root/frontend/ 2>/dev/null | head -10
echo "=== caddy serve config ==="
grep -A5 'root\|file_server\|handle {' /etc/caddy/Caddyfile 2>/dev/null | head -30
