#!/bin/bash
SID="1809ea16d4f74dd4a46a291ce1e4a84b"
echo "=== 库里最后一条原始 text（未加工）==="
sqlite3 /root/nox-core/data/sessions.db "SELECT seq, role, quote(substr(text,1,60)) FROM messages WHERE session_id='$SID' ORDER BY seq DESC LIMIT 1;"
echo
echo "=== load(limit=40) 之后最后一条长什么样（带日期戳？）==="
cd /root/nox-core && .venv/bin/python -c "
import sys
sys.path.insert(0, '/root/nox-core')
from data.store import Store
s = Store('/root/nox-core/data/sessions.db')
msgs = s.load('$SID', limit=40)
for m in msgs[-3:]:
    print(repr(m.role), repr(m.text[:60]))
print('--- last text from db ---')
print(repr(s._last_text('$SID')))
s.close()
"
