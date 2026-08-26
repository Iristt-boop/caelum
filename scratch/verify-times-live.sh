#!/bin/bash
cd /root/nox-core
echo "=== 线上验证：TimeWakeSource 窗口命中逻辑 ==="
.venv/bin/python -c "
import sys; sys.path.insert(0, '/root/nox-core')
from datetime import datetime, timedelta, timezone
from attention.sources.times import TimeWakeSource, TimeWake

# 用内存 store 测（不碰线上 attention.db）
class MemStore:
    def __init__(self): self.s = {}
    def get_source_state(self, k): return self.s.get(k)
    def set_source_state(self, k, v): self.s[k] = v

CN = timezone(timedelta(hours=8))
store = MemStore()
src = TimeWakeSource('12:00:午饭,18:30:晚饭,22:30:睡前', store)

# 11:55（窗口前 5 分钟边缘）→ 应命中
ev = src.poll(datetime(2026, 8, 8, 11, 55, tzinfo=CN))
print('11:55 →', ev.subtype if ev else None)

# 12:00 整 → 已触发过，同一天不再触发
ev2 = src.poll(datetime(2026, 8, 8, 12, 0, tzinfo=CN))
print('12:00 再来 →', ev2)

# 18:35（窗口内）→ 命中晚饭
ev3 = src.poll(datetime(2026, 8, 8, 18, 35, tzinfo=CN))
print('18:35 →', ev3.subtype if ev3 else None)

# 12:30（窗口外）→ 不命中
ev4 = src.poll(datetime(2026, 8, 8, 12, 30, tzinfo=CN))
print('12:30 →', ev4)

# 第二天 12:00 → 重新触发
store.set_source_state('source.times', {'date': '2026-08-08', 'fired_today': ['午饭','晚饭']})
ev5 = src.poll(datetime(2026, 8, 9, 12, 0, tzinfo=CN))
print('第二天 12:00 →', ev5.subtype if ev5 else None)
"
