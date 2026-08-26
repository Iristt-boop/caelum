#!/bin/bash
# 看快循环有没有真的在动。只读。
echo "=== 快循环近况 ==="
journalctl -u nox-core --since '5 min ago' --no-pager \
  | grep -Ei 'Care|想起她|位置变|快循环|快源' | tail -12

echo
echo "=== Care 现状 ==="
curl -s -m 10 http://127.0.0.1:8100/health > /tmp/h.json
python3 - <<'PY'
import json
d = json.load(open("/tmp/h.json"))
a = d.get("attention") or {}
c = a.get("care") or {}
print("  dry_run      :", a.get("dry_run"))
print("  活着的关心链 :", len(c.get("threads_alive") or []))
for t in (c.get("threads_alive") or []):
    print(f"      · {t['kind']} / {t['subject']} / 走了 {t['steps']} 步")
print("  押后的念头   :", len(c.get("held") or []))
PY
rm -f /tmp/h.json

echo
echo "=== 他现在在家吗（HA 实时）==="
set -a; . /root/nox-core/.env 2>/dev/null; set +a
curl -s -m 10 -H "Authorization: Bearer $NOX_HA_API_TOKEN" \
  "$NOX_HA_API_URL/api/states/person.nox" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('  person.nox =',d['state'],'| 最后变化',d.get('last_changed'))"
