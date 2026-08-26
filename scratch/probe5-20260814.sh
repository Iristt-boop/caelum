#!/bin/bash
echo "=== bridge static root: what does it serve? ==="
grep -nE 'static|express\.static|dist' /root/bridge/server.js | grep -ivE '^\s*//' | head -10
echo "=== does /var/www exist and hold an old dist? ==="
ls -la /var/www/ 2>/dev/null | head
echo "=== bridge cwd ==="
ls -l /proc/$(systemctl show bridge -p MainPID | cut -d= -f2)/cwd 2>/dev/null
echo "=== public: fetch main JS bundle (as a browser would) ==="
curl -s -o /dev/null -w 'index-js code=%{http_code} size=%{size_download}\n' https://noxtang.com/assets/index-C722LQH2.js
echo "=== public: does /var/www copy serve? check caddy again ==="
grep -nE 'root|file_server|reverse_proxy|handle' /etc/caddy/Caddyfile | head -20
