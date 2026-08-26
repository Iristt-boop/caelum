#!/bin/bash
echo "=== Core sessions.db schema ==="
sqlite3 /root/nox-core/data/sessions.db ".schema" 2>&1 | head -30
echo "=== bridge conversations in session 1809ea16... (last 20, Aug 13+) ==="
sqlite3 /root/data/nox-bridge.db "SELECT substr(timestamp,1,19), role, substr(content,1,50) FROM conversations WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b' AND timestamp >= '2026-08-13' ORDER BY timestamp LIMIT 40;" 2>&1
