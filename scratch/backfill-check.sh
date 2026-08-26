#!/bin/bash
echo "=== 重复检查：同一 (role, text) 出现 >1 次 ==="
sqlite3 /root/backfill-test/core.db "SELECT role, substr(text,1,40) AS t, COUNT(*) AS n FROM messages WHERE session_id='1809ea16d4f74dd4a46a291ce1e4a84b' GROUP BY role, text HAVING n > 1 ORDER BY n DESC LIMIT 10;" 2>&1
echo
echo "=== 8-06 为什么 41 条（bridge 只有 36）？看 8-06 末尾几条 ==="
sqlite3 /root/backfill-test/core.db "SELECT seq, role, created_at, substr(text,1,30) FROM messages WHERE session_id='1809ea16d4f74dd4a46a291ce1e4a84b' AND created_at < '2026-08-07T00:00' ORDER BY seq DESC LIMIT 5;" 2>&1
echo
echo "=== 8-06/8-07 交界处（UTC vs 北京）==="
sqlite3 /root/backfill-test/core.db "SELECT seq, role, created_at, substr(text,1,25) FROM messages WHERE session_id='1809ea16d4f74dd4a46a291ce1e4a84b' AND created_at BETWEEN '2026-08-06T15:00' AND '2026-08-07T01:00' ORDER BY seq LIMIT 8;" 2>&1
