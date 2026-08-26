#!/bin/bash
echo "=== conversation count for main session ==="
sqlite3 /root/data/nox-bridge.db "SELECT COUNT(*) FROM conversations WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b';" 2>&1
echo "=== top 5 sessions by count ==="
sqlite3 /root/data/nox-bridge.db "SELECT id, COUNT(*) AS c FROM conversations GROUP BY id ORDER BY c DESC LIMIT 6;" 2>&1
echo "=== rowid range of main session ==="
sqlite3 /root/data/nox-bridge.db "SELECT MIN(rowid), MAX(rowid) FROM conversations WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b';" 2>&1
echo "=== last 10 rowids of main session ==="
sqlite3 /root/data/nox-bridge.db "SELECT rowid, substr(timestamp,12,8), role, substr(content,1,20) FROM conversations WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b' ORDER BY rowid DESC LIMIT 10;" 2>&1
