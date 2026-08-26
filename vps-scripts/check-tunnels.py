"""Get current tunnel URLs for all services"""
import paramiko, re

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.93.219.252', username='root', password='Tangtang980213!!',
            timeout=15, look_for_keys=False, allow_agent=False)

for svc in ['cloudflared-ombre', 'cloudflared-tracker', 'cloudflared-bridge']:
    cmd = "journalctl -u {} --no-pager -n 100 2>&1 | grep 'trycloudflare.com' | tail -1".format(svc)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
    out = stdout.read().decode().strip()
    m = re.search(r'(https://[a-z0-9-]+\.trycloudflare\.com)', out)
    if m:
        print("{}: {}".format(svc, m.group(1)))

ssh.close()
