#!/usr/bin/env python3
"""Upload monitor.sh to VPS via SSH + set up cron"""
import paramiko, os, sys

VPS_IP = "47.93.219.252"
VPS_USER = "root"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(SCRIPT_DIR, "monitor.sh")
VPS_SCRIPT_PATH = "/root/vps-scripts/monitor.sh"

def ssh_exec(ssh, cmd):
    """Execute a command on VPS and return stdout"""
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if err:
        print(f"  stderr: {err}")
    return out

def main():
    print(f"Connecting to {VPS_IP}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(VPS_IP, username=VPS_USER, password="Tangtang980213!!", timeout=15, look_for_keys=False, allow_agent=False)
        print("Connected.")
    except Exception as e:
        print(f"SSH connection failed: {e}")
        print(f"\nManual steps:")
        print(f"  1. scp vps-scripts/monitor.sh root@{VPS_IP}:/root/vps-scripts/")
        print(f'  2. ssh root@{VPS_IP} "chmod +x /root/vps-scripts/monitor.sh"')
        print(f'  3. ssh root@{VPS_IP} "(crontab -l 2>/dev/null | grep -v monitor; echo ''*/5 * * * * /root/vps-scripts/monitor.sh'') | crontab -"')
        sys.exit(1)

    # Create dir
    ssh_exec(ssh, "mkdir -p /root/vps-scripts")
    print("Created /root/vps-scripts/")

    # Upload file
    sftp = ssh.open_sftp()
    sftp.put(SCRIPT_PATH, VPS_SCRIPT_PATH)
    sftp.close()
    print("Uploaded monitor.sh")

    # Make executable + test run
    ssh_exec(ssh, "chmod +x /root/vps-scripts/monitor.sh")
    test_out = ssh_exec(ssh, "/root/vps-scripts/monitor.sh && tail -20 /var/log/nox-monitor.log")
    print(f"Test run:\n{test_out}")

    # Setup cron
    cron_line = "*/5 * * * * /root/vps-scripts/monitor.sh"
    cron_cmd = f"(crontab -l 2>/dev/null | grep -v monitor.sh; echo '{cron_line}') | crontab -"
    ssh_exec(ssh, cron_cmd)

    # Verify
    current_cron = ssh_exec(ssh, "crontab -l")
    print(f"\nCrontab:\n{current_cron}")

    ssh.close()
    print("\n✅ Done — monitor runs every 5 minutes. Logs at /var/log/nox-monitor.log")

if __name__ == "__main__":
    main()
