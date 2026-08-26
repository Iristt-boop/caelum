import paramiko, os, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.84.92.71', username='root', password='Tangtang980213!!', timeout=15, look_for_keys=False, allow_agent=False)

# 1. Clean up broken dirs
ssh.exec_command('find /root/frontend/dist -type d -name "*\\\\\\\\*" -exec rm -rf {} \\; 2>/dev/null; rm -rf /root/frontend/dist/assets', timeout=5)
time.sleep(1)
ssh.exec_command('mkdir -p /root/frontend/dist/assets', timeout=3)

# 2. Upload using sftp with manual forward-slash paths
sftp = ssh.open_sftp()
local_dist = r'd:\claude-code\nox-app\frontend\dist'

# Upload index.html
sftp.put(os.path.join(local_dist, 'index.html'), '/root/frontend/dist/index.html')
# Upload assets
assets_dir = os.path.join(local_dist, 'assets')
for fname in os.listdir(assets_dir):
    local_path = os.path.join(assets_dir, fname)
    remote_path = '/root/frontend/dist/assets/' + fname
    sftp.put(local_path, remote_path)
    print('OK:', fname)
# Upload memes
memes_dir = os.path.join(local_dist, 'memes')
try: sftp.mkdir('/root/frontend/dist/memes')
except: pass
for fname in os.listdir(memes_dir):
    local_path = os.path.join(memes_dir, fname)
    remote_path = '/root/frontend/dist/memes/' + fname
    sftp.put(local_path, remote_path)
# Upload SW files
for fname in ['sw.js', 'registerSW.js', 'manifest.webmanifest', 'favicon.svg', 'icons.svg', 'pwa-192.png', 'pwa-512.png']:
    lp = os.path.join(local_dist, fname)
    if os.path.exists(lp):
        sftp.put(lp, '/root/frontend/dist/' + fname)

sftp.close()

# 3. Verify
stdin, stdout, stderr = ssh.exec_command('ls -la /root/frontend/dist/assets/; echo ---; wc -c /root/frontend/dist/assets/*.js', timeout=5)
print(stdout.read().decode())

ssh.close()
print('Done')
