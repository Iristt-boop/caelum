#!/bin/bash
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
echo "=== 删 Core 测试轮 seq 475-480 ==="
cd /root/nox-core && .venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/root/nox-core/data/sessions.db')
c.execute('DELETE FROM messages WHERE session_id=? AND seq >= 475', ('$SID',))
c.commit()
print('Core 删除完成')
"
echo "=== 删 bridge 测试轮（按内容）==="
sqlite3 /root/data/nox-bridge.db "DELETE FROM conversations WHERE id='$SID' AND (content LIKE '%8月9号聊过什么%' OR content LIKE '%又问了一遍%' OR content LIKE '%翻翻记忆%' OR content LIKE '%让我翻翻%');"
echo "bridge 删除完成"
echo
echo "=== 重启 nox-core ==="
systemctl restart nox-core
sleep 3
systemctl is-active nox-core
echo
echo "=== 最终状态：主会话 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) FROM messages WHERE session_id='$SID';"
echo "--- 最后 3 条（应是回填的真实历史）---"
sqlite3 /root/nox-core/data/sessions.db "SELECT seq, role, created_at, substr(text,1,35) FROM messages WHERE session_id='$SID' ORDER BY seq DESC LIMIT 3;"
echo "--- bridge 同步 ---"
sqlite3 /root/data/nox-bridge.db "SELECT COUNT(*) FROM conversations WHERE id='$SID';"
echo
echo "=== 清理临时文件 ==="
rm -f /root/backfill-sessions.py /root/backfill-real.sh /root/backfill-dryrun*.sh /root/backfill-check.sh /root/backfill-verify*.sh /root/probe-*.sh /root/cleanup-inspect.sh /tmp/mem-reply.txt /tmp/msgs*.json 2>/dev/null
rm -rf /root/backfill-test
echo "清理完成"
ls /root/*.sh 2>/dev/null | head
