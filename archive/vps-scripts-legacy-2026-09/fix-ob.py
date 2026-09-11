# 🔴 root 密码从环境变量读，不写在文件里。
# 2026-08-26 之前这一批脚本里有 14 个把它硬编码了 ——
# 而这种一次性运维脚本最容易被顺手 commit、顺手贴出去。
#   用法：set VPS_ROOT_PASSWORD=...  然后再跑
import os
import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.84.92.71', username='root', password=os.environ['VPS_ROOT_PASSWORD'], timeout=15, look_for_keys=False, allow_agent=False)

# Check config.yaml for transport settings
stdin, stdout, stderr = ssh.exec_command('grep -i transport /root/ombre-brain/config.yaml', timeout=5)
print('Config transport:', stdout.read().decode().strip())

# Check server.py for transport
stdin, stdout, stderr = ssh.exec_command('grep -i -A2 "streamable\|stdio\|transport" /root/ombre-brain/server.py | head -20', timeout=5)
print('server.py transport:', stdout.read().decode().strip())

# Update OB service to add streamable-http transport env
ob_service = """[Unit]
Description=ombre-brain
After=network.target
[Service]
Type=simple
WorkingDirectory=/root/ombre-brain
ExecStart=python3 /root/ombre-brain/server.py
Restart=always
RestartSec=5
Environment=PYTHONUTF8=1
Environment=OMBRE_TRANSPORT=streamable-http
Environment=OMBRE_PORT=8002
[Install]
WantedBy=multi-user.target"""
ssh.exec_command('cat > /etc/systemd/system/ombre-brain.service <<ENDOFUNIT\n' + ob_service + '\nENDOFUNIT', timeout=5)
ssh.exec_command('systemctl daemon-reload', timeout=5)
ssh.exec_command('systemctl restart ombre-brain', timeout=5)

import time; time.sleep(5)
stdin, stdout, stderr = ssh.exec_command('systemctl is-active ombre-brain', timeout=5)
print('OB status:', stdout.read().decode().strip())
stdin, stdout, stderr = ssh.exec_command('curl -s http://localhost:8002/health', timeout=5)
print('OB health:', stdout.read().decode().strip())

ssh.close()
