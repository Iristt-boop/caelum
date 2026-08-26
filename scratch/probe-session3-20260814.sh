#!/bin/bash
echo "=== Core messages: 1809ea16 最后 8 条（真实 created_at）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT id, seq, role, created_at, substr(text,1,35) FROM messages WHERE session_id='1809ea16d4f74dd4a46a291ce1e4a84b' ORDER BY id DESC LIMIT 8;" 2>&1
echo
echo "=== bridge conversations: 1809ea16 最后 8 条 ==="
sqlite3 /root/data/nox-bridge.db "SELECT rowid, role, timestamp, substr(content,1,35) FROM conversations WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b' ORDER BY rowid DESC LIMIT 8;" 2>&1
echo
echo "=== 两边各有多少条（对比）==="
echo -n "Core: "; sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='1809ea16d4f74dd4a46a291ce1e4a84b';"
echo -n "Bridge: "; sqlite3 /root/data/nox-bridge.db "SELECT COUNT(*) FROM conversations WHERE id='1809ea16d4f74dd4a46a291ce1e4a84b';"
