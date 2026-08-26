import paramiko
new = paramiko.SSHClient()
new.set_missing_host_key_policy(paramiko.AutoAddPolicy())
new.connect('47.84.92.71', username='root', password='Tangtang980213!!', timeout=15, look_for_keys=False, allow_agent=False)

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
