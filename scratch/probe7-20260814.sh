#!/bin/bash
echo "=== assets: oldest files (possible stale-ref targets) ==="
ls -la /root/frontend/dist/assets/ | sort -k6,7 | head -8
echo "=== assets: any file older than Aug 11 12:00? ==="
find /root/frontend/dist/assets -name '*.js' -newermt '2026-08-10' ! -newermt '2026-08-11 12:00' | head
echo "=== count assets js ==="
ls /root/frontend/dist/assets/*.js | wc -l
echo "=== dist/ root files ==="
ls -la /root/frontend/dist/ | head -20
