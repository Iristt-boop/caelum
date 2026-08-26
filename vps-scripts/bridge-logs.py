import paramiko, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.93.219.252', username='root', password='Tangtang980213!!', timeout=15, look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = ssh.exec_command('journalctl -u bridge --no-pager -n 40 2>&1', timeout=10)
print(stdout.read().decode('utf-8', errors='replace'))
ssh.close()
