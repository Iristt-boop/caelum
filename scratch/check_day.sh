#!/bin/bash
# 看今天的时间线长什么样。只读。
T=$(systemctl show bridge -p Environment --value | tr ' ' '\n' | sed -n 's/^NOX_TOKEN=//p')

echo "=== 经 bridge 拿 /api/nox/day ==="
curl -s -m 20 -H "X-Nox-Token: $T" http://127.0.0.1:3003/api/nox/day > /tmp/day.json
python3 - <<'PY'
import json
d = json.load(open("/tmp/day.json"))
print("  日期      :", d.get("date"), "|", d.get("timezone"))
s = d.get("summary") or {}
print("  惦记过    :", s.get("careConsidered"), "次")
print("    说出口  :", s.get("careSpoke"))
print("    想了没说:", s.get("careSkipped"))
print("    被拦下  :", s.get("careBlocked"))
print("  对话      :", s.get("conversations"), "段")
print("  一起度过  :", s.get("activeHours"), "小时")
print("  待办完成  :", s.get("tasksCompleted"), "（null = 没接，不是 0）")
print()
ev = d.get("events") or []
print(f"  事件 {len(ev)} 条：")
for e in ev[:15]:
    t = e["timestamp"][11:16]
    print(f"    {t}  [{e['type']:12}] {e['status']:9} {e['title']} — {e.get('summary','')[:28]}")
print()
print("  没接的源  :", [k for k, v in (d.get("sources") or {}).items() if not v.get("wired")])
PY
rm -f /tmp/day.json
