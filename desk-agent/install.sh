#!/usr/bin/env bash
# 在 VM nox-desk 上装 desk-agent（2026-10-17）。幂等，可重复跑。
# 用法（在 VM 里，root）：bash install.sh   —— agent.py / nox-desk.service 和它放在同一目录
# 密钥不在这里：/etc/nox-desk/secret 由部署那一步单独写（600，属 desk）
set -euo pipefail
cd "$(dirname "$0")"

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv fonts-noto-cjk >/dev/null

id desk >/dev/null 2>&1 || useradd --system --home /var/lib/nox-desk --shell /usr/sbin/nologin desk
install -d -o root -g root -m 755 /opt/nox-desk
install -d -o desk -g desk -m 700 /var/lib/nox-desk
install -d -o root -g desk -m 750 /etc/nox-desk
install -m 644 agent.py /opt/nox-desk/agent.py

[ -x /opt/nox-desk/.venv/bin/python ] || python3 -m venv /opt/nox-desk/.venv
/opt/nox-desk/.venv/bin/pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple websockets==13.1 playwright==1.48.0
# 浏览器装到共享目录（desk 用户只读）；系统依赖用 playwright 自带的清单装
export PLAYWRIGHT_BROWSERS_PATH=/opt/nox-desk/browsers
/opt/nox-desk/.venv/bin/playwright install-deps chromium >/dev/null
/opt/nox-desk/.venv/bin/playwright install chromium
chmod -R a+rX /opt/nox-desk/browsers

install -m 644 nox-desk.service /etc/systemd/system/nox-desk.service
systemctl daemon-reload
systemctl enable nox-desk >/dev/null
echo "装好了。密钥写进 /etc/nox-desk/secret 后：systemctl restart nox-desk"
