#!/bin/bash
# 经期链路验收。**只读 + 一次可撤销的写**（用未来日期，验完删掉）。
T=$(systemctl show bridge -p Environment --value | tr ' ' '\n' | sed -n 's/^NOX_TOKEN=//p')
B=http://127.0.0.1:3003

echo "=== 1. 启动日志：工具注册了吗 ==="
journalctl -u nox-core -n 60 --no-pager | grep -F record_period

echo
echo "=== 2. 读历史（App 卡片读的就是它）==="
curl -s -m 15 -H "X-Nox-Token: $T" "$B/api/health/facts?type=menstrual&days=180" > /tmp/f.json
python3 - <<'PY'
import json
d = json.load(open("/tmp/f.json"))
items = d.get("items") or []
print(f"  {len(items)} 条")
for i in items:
    r = i.get("raw") or {}
    print(f"    {r.get('date')}  {r.get('event'):6} {r.get('flow','')}   ← {r.get('origin','?')}")
starts = sorted(r["date"] for i in items if (r := i.get("raw") or {}).get("event") == "start")
print("  start：", starts)
if len(starts) >= 2:
    from datetime import date
    print(f"  上个周期 {(date.fromisoformat(starts[-1]) - date.fromisoformat(starts[-2])).days} 天")
PY
rm -f /tmp/f.json

echo
echo "=== 3. 写一条测试记录（2099 年，验完删）==="
curl -s -m 15 -H "X-Nox-Token: $T" -H "Content-Type: application/json" \
  -d '{"event":"start","flow":"test-验收用","date":"2099-01-01"}' \
  "$B/api/health/record/period"
echo

echo "=== 4. 确认写进去了 ==="
python3 - <<'PY'
import json, sqlite3
con = sqlite3.connect("file:/root/nox-core/data/world.db?mode=ro", uri=True)
n = 0
for (o,) in con.execute("SELECT observed FROM observations WHERE type='menstrual'"):
    if "2099-01-01" in o:
        n += 1
        print("  ", json.loads(o))
con.close()
print(f"  测试记录 {n} 条")
PY

echo
echo "=== 5. 删掉测试记录 ==="
python3 - <<'PY'
import sqlite3
con = sqlite3.connect("/root/nox-core/data/world.db")
cur = con.execute("DELETE FROM observations WHERE type='menstrual' AND observed LIKE '%2099-01-01%'")
con.commit()
print(f"  删了 {cur.rowcount} 条")
n = con.execute("SELECT COUNT(*) FROM observations WHERE type='menstrual'").fetchone()[0]
print(f"  剩下 {n} 条（应该是 7）")
con.close()
PY
