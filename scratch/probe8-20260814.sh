#!/bin/bash
echo "=== Core sessions (all) ==="
curl -s http://127.0.0.1:8100/sessions | python3 -c "
import json,sys
d=json.load(sys.stdin)
for s in d.get('sessions',[]):
    print(s['id'], '| msgs:', s.get('messages'), '|', s.get('title','')[:30], '|', s.get('updated_at',''))
"
echo
echo "=== bridge conv-sessions (all) ==="
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
curl -s http://127.0.0.1:3003/api/conv-sessions -H "X-Nox-Token: $TOK" | python3 -c "
import json,sys
for s in json.load(sys.stdin):
    print(s['id'], '|', s.get('title','')[:40], '|', s.get('updated',''))
"
echo
echo "=== bridge DB: conversations around Aug 13 16:00+ (find 你猜猜/滴滴) ==="
sqlite3 /root/data/nox-bridge.db "SELECT session_id, substr(content,1,40), created_at FROM conversations WHERE created_at >= '2026-08-13 15:00' ORDER BY created_at;" 2>/dev/null | head -40
