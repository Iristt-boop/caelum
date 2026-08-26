import paramiko, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.93.219.252', username='root', password='Tangtang980213!!', timeout=15, look_for_keys=False, allow_agent=False)

# Check if Caddy is processing any requests at all
stdin, stdout, stderr = ssh.exec_command('journalctl -u caddy --no-pager -n 20 2>&1', timeout=5)
print('=== Caddy recent logs ===')
print(stdout.read().decode())

# Try direct HTTPS to Cloudflare origin - test with curl from VPS through Cloudflare
stdin, stdout, stderr = ssh.exec_command('curl -sk --connect-timeout 5 https://noxtang.com/health 2>&1 | head -20', timeout=10)
print('=== Via Cloudflare loopback ===')
print(stdout.read().decode())

# Also check if Caddy listens on IPv4 vs IPv6
stdin, stdout, stderr = ssh.exec_command('ss -tlnp | grep caddy', timeout=5)
print('=== Caddy ports ===')
print(stdout.read().decode())

ssh.close()
