#!/bin/bash
set -e
cd /root/nox-core
echo "=== 备份 ==="
for f in attention/gate.py attention/service.py attention/waker.py api/server.py; do
  [ -f "$f" ] && cp "$f" "$f.bak-20260814-pregate"
done
echo "备份完成"

echo "=== 替换 ==="
mv attention/gate.py.new attention/gate.py
mv attention/service.py.new attention/service.py
mv attention/waker.py.new attention/waker.py
mv api/server.py.new api/server.py
echo "替换完成"

echo "=== 语法检查 ==="
.venv/bin/python -c "import ast; [ast.parse(open(f).read()) for f in ['attention/gate.py','attention/service.py','attention/waker.py','api/server.py']]; print('SYNTAX OK')"

echo "=== 导入检查 ==="
.venv/bin/python -c "
import sys; sys.path.insert(0, '/root/nox-core')
from attention.gate import DailyGate, STATE_KEY
from attention.service import AttentionService
g = DailyGate()
print('IMPORT OK, quota =', g.daily_quota, '| state key =', STATE_KEY)
"

echo "=== 重启 nox-core ==="
systemctl restart nox-core
sleep 4
systemctl is-active nox-core

echo "=== 验证 /proactive/check ==="
curl -s -X POST http://127.0.0.1:8100/proactive/check -H "Content-Type: application/json" -d "{}"
echo
echo "=== 启动日志 ==="
journalctl -u nox-core -n 8 --no-pager | grep -iE "error|exception|就绪|Attention|gate" | tail -5
