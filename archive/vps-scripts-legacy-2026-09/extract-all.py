# 🔴 root 密码从环境变量读，不写在文件里。
# 2026-08-26 之前这一批脚本里有 14 个把它硬编码了 ——
# 而这种一次性运维脚本最容易被顺手 commit、顺手贴出去。
#   用法：set VPS_ROOT_PASSWORD=...  然后再跑
import os
"""Extract ALL bucket content from old OB SQLite + hold on new OB"""
import paramiko, json, time, urllib.request, ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

# Connect to old VPS, dump SQLite
old = paramiko.SSHClient()
old.set_missing_host_key_policy(paramiko.AutoAddPolicy())
old.connect('47.93.219.252', username='root', password=os.environ['VPS_ROOT_PASSWORD'], timeout=15, look_for_keys=False, allow_agent=False)

# Read the full SQLite DB
stdin, stdout, stderr = old.exec_command('python3 -c "
import sqlite3, json
db = sqlite3.connect(\"/root/ombre-brain/buckets/dehydration_cache.db\")
cur = db.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")
tables = [r[0] for r in cur.fetchall()]
print(\"Tables:\", tables)
for t in tables:
    try:
        cur2 = db.execute(f\"SELECT * FROM [{t}] LIMIT 1\")
        cols = [d[0] for d in cur2.description]
        print(f\"{t} columns:\", cols)
    except: pass
" 2>&1', timeout=10)
print(stdout.read().decode().strip())

old.close()
