#!/bin/bash
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
echo "=== /api/messages?sessionId=1809ea16... AFTER FIX ==="
curl -s "http://127.0.0.1:3003/api/messages?sessionId=1809ea16d4f74dd4a46a291ce1e4a84b" -H "X-Nox-Token: $TOK" -o /tmp/msgs3.json -w 'code=%{http_code} size=%{size_download}\n'
python3 -c "
import json
msgs=json.load(open('/tmp/msgs3.json'))
print('total:', len(msgs))
for m in msgs[-6:]:
    print(m['id'], m['role'], m['timestamp'][11:19], repr(m['content'][:24]))
hits=[m for m in msgs if '滴滴' in str(m.get('content','')) or '你猜猜' in str(m.get('content',''))]
print('hits 你猜猜/滴滴:', len(hits))
"
echo "=== also verify public path (through caddy) ==="
curl -s "https://noxtang.com/api/messages?sessionId=1809ea16d4f74dd4a46a291ce1e4a84b" -H "X-Nox-Token: $TOK" -o /tmp/msgs4.json -w 'code=%{http_code} size=%{size_download}\n'
python3 -c "
import json
msgs=json.load(open('/tmp/msgs4.json'))
print('public total:', len(msgs), '| last:', repr(msgs[-1]['content'][:20]) if msgs else 'EMPTY')
"
