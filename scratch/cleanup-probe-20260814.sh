#!/bin/bash
# cleanup probe data from 2026-08-14 chat debugging
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
echo "=== delete bridge test sessions ==="
for sid in c7bf10ec910041198183b324f20ab738 456c491e9d2b4100bf5433411b07c554; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "http://127.0.0.1:3003/api/conv-sessions/$sid" -H "X-Nox-Token: $TOK")
  echo "  bridge del $sid -> $code"
done
echo "=== delete core test sessions ==="
for sid in c7bf10ec910041198183b324f20ab738 456c491e9d2b4100bf5433411b07c554; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "http://127.0.0.1:8100/session/$sid")
  echo "  core del $sid -> $code"
done
echo "=== remove probe scripts ==="
rm -f /root/probe-chat-20260814.sh /root/probe2-20260814.sh /root/probe3-20260814.sh /root/probe4-20260814.sh /root/probe5-20260814.sh /root/probe6-20260814.sh /root/probe7-20260814.sh /root/probe8-20260814.sh /root/probe9-20260814.sh /root/probe10-20260814.sh /root/probe11-20260814.sh /root/probe12-20260814.sh /root/probe13-20260814.sh /root/verify-fix-20260814.sh /root/fix-messages-query.py
rm -f /tmp/msgs*.json /tmp/pubchat.out /tmp/pubconv.out /tmp/verify.out /tmp/conv.out /tmp/chat.out
echo "=== keep backup of server.js ==="
ls -la /root/bridge/server.js.bak-20260814-msgfix
echo "=== final check: sessions remain ==="
curl -s http://127.0.0.1:8100/sessions | python3 -c "import json,sys; [print(s['id'], s.get('title','')[:25], s.get('messages')) for s in json.load(sys.stdin).get('sessions',[])]"
