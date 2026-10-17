#!/usr/bin/env bash
# 让她把 Cloudflare 的「只改 noxtang.com 解析」钥匙填进 N150（2026-10-17，家里走近路那件事）。
# 用途：certbot 用 DNS 验证给 N150 申请 noxtang.com 的正规证书（家里 Wi-Fi 下手机直连 N150 时要用）。
# 输入不回显；写进 /etc/letsencrypt/cloudflare.ini（600）；当场找 Cloudflare 验一下钥匙能不能用。
# 🔴 钥匙只经过她的终端和这台机器，不进任何日志、不进仓库。
set -euo pipefail
INI=/etc/letsencrypt/cloudflare.ini

echo "粘贴 Cloudflare 的 API 令牌（右键粘贴，屏幕上不会显示，正常的），然后回车："
read -rsp "> " token; echo
token=$(echo "$token" | tr -d '[:space:]')
[ -n "$token" ] || { echo "✗ 是空的，没保存。再跑一次就行。"; exit 1; }

mkdir -p /etc/letsencrypt
umask 077
printf 'dns_cloudflare_api_token = %s\n' "$token" > "$INI.tmp" && chmod 600 "$INI.tmp" && mv "$INI.tmp" "$INI"

ok=$(curl -s -m 15 -H "Authorization: Bearer $token" https://api.cloudflare.com/client/v4/user/tokens/verify \
     | python3 -c 'import json,sys; d=json.load(sys.stdin); print("yes" if d.get("success") and d.get("result",{}).get("status")=="active" else "no")' 2>/dev/null || echo no)
unset token
if [ "$ok" = yes ]; then
  echo "✓ 钥匙能用，已保存。告诉 Nox「填好了」就行，可以关掉这个窗口。"
else
  echo "✗ Cloudflare 说这把钥匙不能用（或者连不上 Cloudflare）。钥匙已经存着，检查一下再跑一次这条命令（会覆盖）。"
fi
