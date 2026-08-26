#!/bin/bash
echo "=== 1. record 记额度：连续调 3 次后 check 应被拦 ==="
for i in 1 2 3; do
  echo -n "record #$i: "
  curl -s -X POST http://127.0.0.1:8100/proactive/record -H "Content-Type: application/json" -d "{}"
  echo
done
echo -n "check after 3: "
curl -s -X POST http://127.0.0.1:8100/proactive/check -H "Content-Type: application/json" -d "{}"
echo
echo
echo "=== 2. 重置：直接清 source_state 里的 gate（模拟跨天）==="
cd /root/nox-core && .venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/root/nox-core/data/attention.db')
c.execute(\"DELETE FROM source_state WHERE key='gate'\")
c.commit()
print('gate 状态已清（模拟跨天重置）')
"
echo -n "check after reset: "
curl -s -X POST http://127.0.0.1:8100/proactive/check -H "Content-Type: application/json" -d "{}"
echo
echo
echo "=== 3. 安静时段验证：当前如果是 1-9 点（CST）应被拦 ==="
echo "当前 CST 时间: $(TZ='Asia/Shanghai' date '+%H:%M')"
