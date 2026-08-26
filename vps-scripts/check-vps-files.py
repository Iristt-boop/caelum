import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.84.92.71', username='root', password='Tangtang980213!!', timeout=15, look_for_keys=False, allow_agent=False)
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
