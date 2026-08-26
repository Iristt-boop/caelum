#!/bin/bash
echo "=== 线上 store.py 是否含锚点修复 ==="
grep -c "锚点\|_last_text\|内容锚点" /root/nox-core/data/store.py
echo "=== 语法检查 ==="
cd /root/nox-core && .venv/bin/python -c "import ast; ast.parse(open('data/store.py').read())" && echo SYNTAX-OK
echo "=== 重启 nox-core ==="
systemctl restart nox-core
sleep 3
systemctl is-active nox-core
echo "=== 启动日志尾部 ==="
journalctl -u nox-core -n 8 --no-pager | tail -8
