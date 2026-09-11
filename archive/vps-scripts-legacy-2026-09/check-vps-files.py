# 🔴 root 密码从环境变量读，不写在文件里。
# 2026-08-26 之前这一批脚本里有 14 个把它硬编码了 ——
# 而这种一次性运维脚本最容易被顺手 commit、顺手贴出去。
#   用法：set VPS_ROOT_PASSWORD=...  然后再跑
import os
import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.84.92.71', username='root', password=os.environ['VPS_ROOT_PASSWORD'], timeout=15, look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = ssh.exec_command('ls -la /root/frontend/dist/assets/ 2>&1', timeout=5)
print('assets dir:')
print(stdout.read().decode())

stdin, stdout, stderr = ssh.exec_command('find /root/frontend/dist -name "*.js" 2>&1', timeout=5)
print('js files:')
print(stdout.read().decode())

# Check if index.html has the right reference
stdin, stdout, stderr = ssh.exec_command('head -15 /root/frontend/dist/index.html 2>&1', timeout=5)
print('index.html:')
print(stdout.read().decode())

ssh.close()
