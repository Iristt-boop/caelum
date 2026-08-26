#!/bin/bash
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
echo "=== /api/messages?sessionId=1809ea16... (as frontend does) ==="
curl -s "http://127.0.0.1:3003/api/messages?sessionId=1809ea16d4f74dd4a46a291ce1e4a84b" -H "X-Nox-Token: $TOK" -o /tmp/msgs.json -w 'code=%{http_code} size=%{size_download}\n'
python3 -c "
import json
msgs=json.load(open('/tmp/msgs.json'))
print('total:', len(msgs))
for m in msgs[-8:]:
    print(m['id'], m.get('sessionId','')[:12], m['role'], m['timestamp'][11:19], repr(m['content'][:30]))
"
echo
echo "=== /api/messages (global last 200) - do 滴滴/你猜猜 appear? ==="
curl -s "http://127.0.0.1:3003/api/messages" -H "X-Nox-Token: $TOK" -o /tmp/msgs2.json -w 'code=%{http_code} size=%{size_download}\n'
python3 -c "
import json
msgs=json.load(open('/tmp/msgs2.json'))
print('total:', len(msgs))
hits=[m for m in msgs if '滴滴' in str(m.get('content','')) or '你猜猜' in str(m.get('content',''))]
print('hits for 滴滴/你猜猜:', len(hits))
for m in msgs[-5:]:
    print(m['id'], m.get('sessionId','')[:12], m['role'], m['timestamp'][11:19], repr(m['content'][:30]))
"
