#!/bin/bash
echo "=== 跨天验证：往内存塞一条『昨天』的记录，今天 check 应放行 ==="
cd /root/nox-core && .venv/bin/python -c "
import sys; sys.path.insert(0, '/root/nox-core')
from datetime import datetime, timedelta, timezone
from attention.gate import DailyGate
g = DailyGate()
yesterday = datetime.now(timezone.utc) - timedelta(days=1)
g.note_spoke(yesterday)   # 记一条昨天的
d = g.can_speak()         # 今天 check
print('昨天记了 1 次，今天 check:', d.can_speak, '|', d.reason, '| spoken_today =', d.spoken_today)
assert d.can_speak is True, '跨天必须重置'
print('跨天重置 ✓')
"
echo
echo "=== 安静时段：用 python 造一个 CST 凌晨 3 点验证 ==="
.venv/bin/python -c "
import sys; sys.path.insert(0, '/root/nox-core')
from datetime import datetime, timezone, timedelta
from attention.gate import DailyGate
g = DailyGate()
CN = timezone(timedelta(hours=8))
t = datetime(2026, 8, 8, 3, 0, tzinfo=CN)   # CST 凌晨 3 点
d = g.can_speak(t)
print('CST 03:00 check:', d.can_speak, '|', d.reason)
assert d.can_speak is False
print('安静时段 ✓')
"
