#!/bin/bash
echo "=== 真实库回填开始 $(date) ==="
# 1. 先对源库 checkpoint（避免 WAL 没合并）
cd /root/nox-core && .venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/root/nox-core/data/sessions.db')
c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
c.close()
print('checkpoint done')
"
# 2. 备份真实库（脚本里也会备份，这里双保险）
cp /root/nox-core/data/sessions.db /root/nox-core/data/sessions.db.bak-20260814-prebackfill
echo "备份完成: sessions.db.bak-20260814-prebackfill"
# 3. 跑回填（脚本指向真实路径 —— 检查脚本顶部路径）
grep -n "BRIDGE_DB = \|CORE_DB = " /root/backfill-sessions.py
python3 /root/backfill-sessions.py
echo
echo "=== 真实库回填完成 $(date) ==="
