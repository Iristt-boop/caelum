#!/bin/bash
TOK=$(systemctl show bridge -p Environment | tr ' ' '\n' | grep '^NOX_TOKEN=' | cut -d= -f2-)
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
echo "=== 删掉我验证用的对话轮（8月9号相关的提问/回复，seq 469+ 我的测试）==="
# 我的测试消息特征：'我们8月9号聊过什么特别的事吗' 和它的两条回复
echo "先看当前最新几条 seq（确认要删哪些）:"
sqlite3 /root/nox-core/data/sessions.db "SELECT seq, role, substr(text,1,35) FROM messages WHERE session_id='$SID' ORDER BY seq DESC LIMIT 8;"
echo
echo "=== Core 侧删除测试消息（seq >= 475 的全部是我测的）==="
cd /root/nox-core && .venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/root/nox-core/data/sessions.db')
c.execute(\"DELETE FROM messages WHERE session_id='$SID' AND seq >= 475\")
c.commit()
print('Core 删除完成')
"
echo "=== bridge 侧也删掉对应的测试轮 ==="
sqlite3 /root/data/nox-bridge.db "DELETE FROM conversations WHERE id='$SID' AND content LIKE '%8月9号聊过什么%';"
sqlite3 /root/data/nox-bridge.db "SELECT COUNT(*) FROM conversations WHERE id='$SID';"
echo
echo "=== 重启 nox-core 清缓存 ==="
systemctl restart nox-core
sleep 3
systemctl is-active nox-core
echo
echo "=== 最终状态 ==="
sqlite3 /root/nox-core/data/sessions.db "SELECT COUNT(*) AS total FROM messages WHERE session_id='$SID';"
sqlite3 /root/nox-core/data/sessions.db "SELECT seq, role, substr(text,1,30) FROM messages WHERE session_id='$SID' ORDER BY seq DESC LIMIT 3;"
echo "=== 清理临时脚本 ==="
rm -f /root/backfill-sessions.py /root/backfill-real.sh /root/backfill-dryrun*.sh /root/backfill-check.sh /root/backfill-verify*.sh /root/probe-*.sh /root/mem-reply.txt /tmp/mem-reply.txt
rm -rf /root/backfill-test
echo "清理完成"
