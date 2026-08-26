#!/bin/bash
echo "=== bridge DB tables ==="
sqlite3 /root/data/nox-bridge.db ".tables" 2>&1
echo "=== bridge conversations schema ==="
sqlite3 /root/data/nox-bridge.db ".schema conversations" 2>&1 | head -20
echo "=== Core store DB location ==="
ls -la /root/nox-core/data/ 2>/dev/null | head
echo "=== Core store tables ==="
find /root/nox-core -name '*.db' 2>/dev/null | head -5
