#!/bin/bash
# 八个源全接上之后的验收。只读。
T=$(systemctl show bridge -p Environment --value | tr ' ' '\n' | sed -n 's/^NOX_TOKEN=//p')
B=http://127.0.0.1:3003

echo "=== 三个新接口 ==="
for p in /api/memory/recent?limit=3 /api/music/recent /api/todo/events; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 -H "X-Nox-Token: $T" "$B$p")
  echo "  $code  $p"
done

echo
echo "=== 今天的时间线 ==="
curl -s -m 25 -H "X-Nox-Token: $T" "$B/api/nox/day" > /tmp/d.json
python3 - <<'PY'
import json
d = json.load(open("/tmp/d.json"))
s = d.get("summary") or {}
print("  惦记过", s.get("careConsidered"), "次 | 说出口", s.get("careSpoke"),
      "| 聊了", s.get("conversations"), "段")
print("  做完", s.get("tasksCompleted"), "件 | 听了", s.get("songsPlayed"),
      "首 | 一起度过", s.get("activeHours"), "小时")
print()
by = {}
for e in d.get("events") or []:
    by[e["type"]] = by.get(e["type"], 0) + 1
print("  事件按类型：", by or "（今天还没有）")
print()
for e in (d.get("events") or [])[-10:]:
    print(f"    {e['timestamp'][11:16]}  [{e['type']:12}] {e['status']:9} {e['title']} — {e.get('summary','')[:26]}")
print()
bad = [k for k, v in (d.get("sources") or {}).items() if not v.get("wired")]
print("  还没接的源：", bad or "没有了，八个全接上")
PY
rm -f /tmp/d.json
