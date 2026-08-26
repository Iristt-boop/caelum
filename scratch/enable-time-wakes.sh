#!/bin/bash
set -e
echo "=== 给 nox-core 配置 NOX_TIME_WAKES ==="
# 先看现有 EnvironmentFile
echo "--- 现有 .env 尾部（不打印密钥）---"
grep -c "NOX_TIME_WAKES" /root/nox-core/.env 2>/dev/null || echo "还没有 NOX_TIME_WAKES"
# 追加配置（.env 是 EnvironmentFile，server.py 的 config.py 会 load）
if ! grep -q "NOX_TIME_WAKES" /root/nox-core/.env 2>/dev/null; then
  echo 'NOX_TIME_WAKES=12:00:午饭,18:30:晚饭,22:30:睡前' >> /root/nox-core/.env
  echo "已追加 NOX_TIME_WAKES"
fi
# 确认 systemd 用的是 EnvironmentFile
systemctl show nox-core -p EnvironmentFiles | head -1
echo
echo "=== 重启 nox-core ==="
systemctl restart nox-core
sleep 4
systemctl is-active nox-core
echo
echo "=== 日志确认时间醒来已启用 ==="
journalctl -u nox-core -n 15 --no-pager | grep -iE "时间醒来|固定时间" | tail -3
echo
echo "=== 手动验证 TimeWakeSource 解析（服务内）==="
curl -s http://127.0.0.1:8100/health | python3 -c "
import json,sys
d = json.load(sys.stdin)
a = d.get('attention')
print('attention gate:', a.get('gate') if a else None)
" 2>/dev/null || echo "（health 无 attention 字段属正常，dry-run 下也有）"
