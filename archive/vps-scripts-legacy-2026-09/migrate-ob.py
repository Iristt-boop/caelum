# 🔴 root 密码从环境变量读，不写在文件里。
# 2026-08-26 之前这一批脚本里有 14 个把它硬编码了 ——
# 而这种一次性运维脚本最容易被顺手 commit、顺手贴出去。
#   用法：set VPS_ROOT_PASSWORD=...  然后再跑
import os
import paramiko, time

# 1. Stop OB on old VPS
old = paramiko.SSHClient()
old.set_missing_host_key_policy(paramiko.AutoAddPolicy())
old.connect('47.93.219.252', username='root', password=os.environ['VPS_ROOT_PASSWORD'], timeout=15, look_for_keys=False, allow_agent=False)

old.exec_command('systemctl stop ombre-brain', timeout=5)
time.sleep(3)
print('Old OB stopped')

# 2. Check what's in the DB now
stdin, stdout, stderr = old.exec_command('ls -la /root/ombre-brain/buckets/', timeout=5)
print(stdout.read().decode())

stdin, stdout, stderr = old.exec_command('find /root/ombre-brain/buckets -type f -exec ls -la {} \;', timeout=5)
print(stdout.read().decode())

# 3. Tar everything properly
stdin, stdout, stderr = old.exec_command('cd /root && tar czf /tmp/full-ob.tar.gz ombre-brain/ 2>&1', timeout=30)
print('Tar done, size:')
stdin, stdout, stderr = old.exec_command('ls -lh /tmp/full-ob.tar.gz', timeout=5)
print(stdout.read().decode().strip())

old.close()

# 4. Download
t = paramiko.Transport(('47.93.219.252', 22))
t.connect(username='root', password=os.environ['VPS_ROOT_PASSWORD'])
sftp = paramiko.SFTPClient.from_transport(t)
sftp.get('/tmp/full-ob.tar.gz', r'd:\claude-code\vps-scripts\full-ob.tar.gz')
sftp.close()
t.close()
print('Downloaded')

# 5. Upload to new VPS
t2 = paramiko.Transport(('47.84.92.71', 22))
t2.connect(username='root', password=os.environ['VPS_ROOT_PASSWORD'])
sftp2 = paramiko.SFTPClient.from_transport(t2)
sftp2.put(r'd:\claude-code\vps-scripts\full-ob.tar.gz', '/tmp/full-ob.tar.gz')
sftp2.close()
t2.close()
print('Uploaded to new VPS')

# 6. Replace OB on new VPS
new = paramiko.SSHClient()
new.set_missing_host_key_policy(paramiko.AutoAddPolicy())
new.connect('47.84.92.71', username='root', password=os.environ['VPS_ROOT_PASSWORD'], timeout=15, look_for_keys=False, allow_agent=False)

new.exec_command('systemctl stop ombre-brain', timeout=5)
time.sleep(2)

# Backup and replace
new.exec_command('rm -rf /root/ombre-brain.old; mv /root/ombre-brain /root/ombre-brain.old', timeout=5)
new.exec_command('cd /root && tar xzf /tmp/full-ob.tar.gz', timeout=10)
time.sleep(2)

# Restore config (use streamable-http)
stdin, stdout, stderr = new.exec_command('cat /root/ombre-brain/config.yaml', timeout=5)
config = stdout.read().decode()
config = config.replace('transport: "stdio"', 'transport: "streamable-http"')
with open(r'd:\claude-code\vps-scripts\config-tmp.yaml', 'w') as f:
    f.write(config)

# Upload modified config
t3 = paramiko.Transport(('47.84.92.71', 22))
t3.connect(username='root', password=os.environ['VPS_ROOT_PASSWORD'])
sftp3 = paramiko.SFTPClient.from_transport(t3)
sftp3.put(r'd:\claude-code\vps-scripts\config-tmp.yaml', '/root/ombre-brain/config.yaml')
sftp3.close()
t3.close()

# Start OB
new.exec_command('systemctl start ombre-brain', timeout=5)
time.sleep(8)

stdin, stdout, stderr = new.exec_command('curl -s http://localhost:8002/health', timeout=5)
print('OB health:', stdout.read().decode().strip())

new.close()

# Restart old OB
old2 = paramiko.SSHClient()
old2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
old2.connect('47.93.219.252', username='root', password=os.environ['VPS_ROOT_PASSWORD'], timeout=15, look_for_keys=False, allow_agent=False)
old2.exec_command('systemctl start ombre-brain', timeout=5)
old2.close()
print('Old OB restarted')
