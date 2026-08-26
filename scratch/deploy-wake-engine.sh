#!/bin/bash
set -e
echo "=== 部署：唤醒引擎重构（删 Care + 时间醒来）==="

# ---- Core 侧 ----
cd /root/nox-core
echo "--- 备份 ---"
for f in attention/service.py attention/waker.py api/server.py; do
  [ -f "$f" ] && cp "$f" "$f.bak-20260814-prewalk"
done
echo "--- 替换 ---"
mv attention/service.py.new attention/service.py
mv attention/waker.py.new attention/waker.py
mv api/server.py.new api/server.py
mv attention/sources/times.py.new attention/sources/times.py
echo "--- 语法检查 ---"
.venv/bin/python -c "import ast; [ast.parse(open(f).read()) for f in ['attention/service.py','attention/waker.py','api/server.py','attention/sources/times.py']]; print('SYNTAX OK')"
echo "--- 导入检查 ---"
.venv/bin/python -c "
import sys; sys.path.insert(0, '/root/nox-core')
from attention.sources.times import TimeWakeSource, TimeWake
from attention.service import AttentionService
print('IMPORT OK, parse:', [w.subject for w in TimeWake.parse('12:00:午饭,18:30:晚饭')])
"
echo "--- 重启 nox-core ---"
systemctl restart nox-core
sleep 4
systemctl is-active nox-core

# ---- bridge 侧 ----
echo "--- bridge 备份+替换 ---"
cd /root/bridge
cp server.js server.js.bak-20260814-prewalk
mv server.js.new server.js
node --check server.js && echo "BRIDGE SYNTAX OK"
systemctl restart bridge
sleep 3
systemctl is-active bridge

echo "=== 验证 /proactive/check 应已 404（端点删除）==="
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8100/proactive/check -H "Content-Type: application/json" -d "{}"

echo "=== 启动日志 ==="
journalctl -u nox-core -n 10 --no-pager | grep -iE "时间醒来|error|exception|就绪" | tail -5
