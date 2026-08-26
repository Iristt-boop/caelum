#!/bin/bash
echo "=== 清理：清掉测试产生的 gate 状态（今天 3/3 是我 record 造的）==="
cd /root/nox-core && .venv/bin/python -c "
import sys; sys.path.insert(0, '/root/nox-core')
import sqlite3
c = sqlite3.connect('/root/nox-core/data/attention.db')
c.execute(\"DELETE FROM source_state WHERE key='gate'\")
c.commit()
print('gate 状态已清（回到干净状态，今天 0/3）')
"
echo "=== 重启 nox-core 让内存实例也干净 ==="
systemctl restart nox-core
sleep 4
systemctl is-active nox-core
echo -n "check 干净状态: "
curl -s -X POST http://127.0.0.1:8100/proactive/check -H "Content-Type: application/json" -d "{}"
echo
echo "=== 清理临时脚本 ==="
rm -f /root/deploy-gate.sh /root/deploy-bridge-gate.sh /root/verify-gate-live.sh /root/verify-gate-crossday.sh
echo "完成"
