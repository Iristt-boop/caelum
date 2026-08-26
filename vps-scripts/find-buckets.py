# 🔴 root 密码从环境变量读，不写在文件里。
# 2026-08-26 之前这一批脚本里有 14 个把它硬编码了 ——
# 而这种一次性运维脚本最容易被顺手 commit、顺手贴出去。
#   用法：set VPS_ROOT_PASSWORD=...  然后再跑
import os
import paramiko
new = paramiko.SSHClient()
new.set_missing_host_key_policy(paramiko.AutoAddPolicy())
new.connect('47.84.92.71', username='root', password=os.environ['VPS_ROOT_PASSWORD'], timeout=15, look_for_keys=False, allow_agent=False)

# Check new VPS OB files
stdin, stdout, stderr = new.exec_command('ls -la /root/ombre-brain/buckets/', timeout=5)
print('New buckets:', stdout.read().decode())

stdin, stdout, stderr = new.exec_command('ls -la /root/ombre-brain/buckets.bak/ 2>/dev/null', timeout=5)
print('Backup:', stdout.read().decode())

# Check SQLite integrity
stdin, stdout, stderr = new.exec_command('python3 -c "import sqlite3; db=sqlite3.connect(\"/root/ombre-brain/buckets/dehydration_cache.db\"); cur=db.execute(\"SELECT name FROM sqlite_master WHERE type=\\\"table\\\"\"); print([r[0] for r in cur.fetchall()])" 2>&1', timeout=5)
print('Tables:', stdout.read().decode().strip())

# Row counts
stdin, stdout, stderr = new.exec_command('python3 -c "import sqlite3; db=sqlite3.connect(\"/root/ombre-brain/buckets/dehydration_cache.db\"); tables=db.execute(\"SELECT name FROM sqlite_master WHERE type=\\\"table\\\"\").fetchall(); [print(t[0], db.execute(f\\\"SELECT count(*) FROM [{t[0]}]\\\").fetchone()[0]) for t in tables]" 2>&1', timeout=5)
print('Row counts:', stdout.read().decode().strip())

new.close()
