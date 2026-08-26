#!/bin/bash
echo "=== v2 演练（带 WAL）：副本库 ==="
rm -rf /root/backfill-test && mkdir -p /root/backfill-test
# 对源库做 checkpoint，把 WAL 合并进主文件再拷
cd /root/nox-core/data && .venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/root/nox-core/data/sessions.db')
c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
c.close()
print('checkpoint done')
" 2>/dev/null || python3 -c "
import sqlite3
c = sqlite3.connect('/root/nox-core/data/sessions.db')
c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
c.close()
print('checkpoint done (system py)')
"
cp /root/data/nox-bridge.db /root/backfill-test/bridge.db
cp /root/nox-core/data/sessions.db /root/backfill-test/core.db
cp /root/backfill-sessions.py /root/backfill-test/run.py
sed -i 's|BRIDGE_DB = "/root/data/nox-bridge.db"|BRIDGE_DB = "/root/backfill-test/bridge.db"|; s|CORE_DB = "/root/nox-core/data/sessions.db"|CORE_DB = "/root/backfill-test/core.db"|' /root/backfill-test/run.py
python3 /root/backfill-test/run.py
echo
echo "=== 演练后副本抽查 ==="
sqlite3 /root/backfill-test/core.db "SELECT seq, role, substr(text,1,28) FROM messages WHERE session_id='1809ea16d4f74dd4a46a291ce1e4a84b' AND (text LIKE '%你猜猜%' OR text LIKE '%滴滴%') ORDER BY seq;" 2>&1 | head -8
