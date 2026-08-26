#!/bin/bash
# VPS 一键部署脚本
set -e

echo "=== 安装 Node.js 22 ==="
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs

echo "=== 安装 Claude Code ==="
npm install -g @anthropic-ai/claude-code

echo "=== Bridge 依赖 ==="
cd /root/bridge
npm install

echo "=== Bridge 服务 ==="
cp bridge.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable bridge
systemctl start bridge

echo "=== 登录 Claude Code ==="
claude login

echo "=== 部署完成 ==="
echo "Bridge: http://$(hostname -I | awk '{print $1}'):3003"
