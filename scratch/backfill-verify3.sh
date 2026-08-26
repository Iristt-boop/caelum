#!/bin/bash
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
echo "=== 完整问他 8-09 的事（不截断，等 done）==="
curl -s -N -X POST https://noxtang.com/api/chat \
  -H "Content-Type: application/json" -H "X-Nox-Token: $TOK" \
  -d "{\"message\":\"我们8月9号聊过什么特别的事吗\",\"sessionId\":\"$SID\"}" > /tmp/mem-reply.txt
echo "=== 回复内容 ==="
cat /tmp/mem-reply.txt | sed 's/^data: //' | python3 -c "
import sys, json
text = ''
for line in sys.stdin:
    try:
        ev = json.loads(line)
        if ev.get('type') == 'text':
            text += ev.get('content', '')
        elif ev.get('type') == 'done':
            print('DONE ok=', ev.get('ok'), 'session=', ev.get('sessionId'))
    except Exception:
        pass
print(text[:800])
"
echo
echo "=== 落库了吗（应 477）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='$SID';"
echo "=== 最新 2 条 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT seq, role, substr(text,1,30) FROM messages WHERE session_id='$SID' ORDER BY seq DESC LIMIT 2;"
