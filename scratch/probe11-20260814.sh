#!/bin/bash
echo "=== full content of messages after 16:03:36 (the missing ones) ==="
sqlite3 /root/data/nox-bridge.db "SELECT rowid, substr(timestamp,12,8), role, '[' || substr(metadata,1,60) || ']', content FROM conversations WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b' AND timestamp >= '2026-08-13T08:03:30' ORDER BY rowid;" 2>&1
echo
echo "=== check for code fences / backticks in those rows ==="
sqlite3 /root/data/nox-bridge.db "SELECT rowid, role, content FROM conversations WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b' AND timestamp >= '2026-08-13T08:03:30' ORDER BY rowid;" 2>&1 | grep -c '```' || echo "no fences"
