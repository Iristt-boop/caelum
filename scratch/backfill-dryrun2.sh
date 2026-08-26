#!/bin/bash
echo "=== v2 演练：副本库 ==="
rm -rf /root/backfill-test && mkdir -p /root/backfill-test
cp /root/data/nox-bridge.db /root/backfill-test/bridge.db
cp /root/nox-core/data/sessions.db /root/backfill-test/core.db
# 把脚本改成副本路径（临时）
cp /root/backfill-sessions.py /root/backfill-test/run.py
sed -i 's|BRIDGE_DB = "/root/data/nox-bridge.db"|BRIDGE_DB = "/root/backfill-test/bridge.db"|; s|CORE_DB = "/root/nox-core/data/sessions.db"|CORE_DB = "/root/backfill-test/core.db"|' /root/backfill-test/run.py
python3 /root/backfill-test/run.py
echo
echo "=== 演练后的副本时间线抽查（8-13 你猜猜/滴滴 应该在里面）==="
sqlite3 /root/backfill-test/core.db "SELECT seq, role, substr(text,1,25) FROM messages WHERE session_id='1809ea16d4f74dd4a46a291ce1e4a84b' AND text LIKE '%你猜猜%' OR (session_id='1809ea16d4f74dd4a46a291ce1e4a84b' AND text LIKE '%滴滴%') ORDER BY seq;" 2>&1 | head -8
