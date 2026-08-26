#!/bin/bash
echo "=== 演练：复制库到临时目录 ==="
rm -rf /root/backfill-test && mkdir -p /root/backfill-test
cp /root/data/nox-bridge.db /root/backfill-test/bridge.db
# sessions.db 有 WAL，要连带拷
cp /root/nox-core/data/sessions.db /root/backfill-test/core.db 2>/dev/null
cp /root/nox-core/data/sessions.db-wal /root/backfill-test/core.db-wal 2>/dev/null
cp /root/nox-core/data/sessions.db-shm /root/backfill-test/core.db-shm 2>/dev/null
echo "复制完成"
ls -la /root/backfill-test/
echo
echo "=== 改脚本指向副本并跑 ==="
sed -i 's|BRIDGE_DB = "/root/data/nox-bridge.db"|BRIDGE_DB = "/root/backfill-test/bridge.db"|; s|CORE_DB = "/root/nox-core/data/sessions.db"|CORE_DB = "/root/backfill-test/core.db"|' /root/backfill-sessions.py
cd /root && python3 backfill-sessions.py
