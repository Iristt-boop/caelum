#!/bin/bash
set -e
cd /root/bridge
echo "=== 备份 ==="
cp server.js server.js.bak-20260814-pregate
echo "备份完成"

echo "=== 替换 ==="
mv server.js.new server.js

echo "=== node 语法检查 ==="
node --check server.js && echo "SYNTAX OK"

echo "=== 重启 bridge ==="
systemctl restart bridge
sleep 3
systemctl is-active bridge

echo "=== bridge 日志（Care 相关）==="
journalctl -u bridge -n 10 --no-pager | grep -iE "Care|error|Error" | tail -5
