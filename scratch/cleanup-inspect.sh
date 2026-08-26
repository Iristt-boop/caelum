#!/bin/bash
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
echo "=== 当前最新 12 条 seq（看清测试轮边界）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT seq, role, created_at, substr(text,1,40) FROM messages WHERE session_id='$SID' ORDER BY seq DESC LIMIT 12;"
echo
echo "=== 找出所有含我测试特征的 seq ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT seq, role FROM messages WHERE session_id='$SID' AND (text LIKE '%8月9号聊过什么%' OR text LIKE '%又问了一遍%' OR text LIKE '%翻翻记忆%' OR text LIKE '%让我翻翻%') ORDER BY seq;"
