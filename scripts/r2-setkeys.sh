#!/usr/bin/env bash
# 让她把 R2 的钥匙填进 N150（2026-10-14）。她不熟 ssh / nano —— 跑一条命令，按提示粘贴就行。
# 输入不回显；写进 /root/.caelum-r2.env（600），然后立刻真连一次 R2 验证。
# 🔴 钥匙只经过她的终端和这台机器，不进任何日志、不进仓库。
set -euo pipefail
ENV=/root/.caelum-r2.env

echo "填 R2 的钥匙（粘贴后按回车；钥匙那两项粘贴时屏幕上不会显示，正常的）"
read -rp  "1/4 账户 ID（S3 API 地址里 https:// 后面那串）: " acct
read -rp  "2/4 Access Key ID: " akid
read -rsp "3/4 Secret Access Key（不显示）: " secret; echo
read -rp  "4/4 桶的名字 [caelum-backup]: " bucket
bucket=${bucket:-caelum-backup}

acct=$(echo "$acct" | tr -d '[:space:]'); akid=$(echo "$akid" | tr -d '[:space:]')
secret=$(echo "$secret" | tr -d '[:space:]')
if [ -z "$acct" ] || [ -z "$akid" ] || [ -z "$secret" ]; then
  echo "✗ 有一项是空的，没保存。再跑一次就行。"; exit 1
fi

umask 077
tmp=$(mktemp /root/.caelum-r2.env.XXXX)
printf 'R2_ACCOUNT_ID=%s\nR2_ACCESS_KEY_ID=%s\nR2_SECRET_ACCESS_KEY=%s\nR2_BUCKET=%s\n' \
  "$acct" "$akid" "$secret" "$bucket" > "$tmp"
chmod 600 "$tmp" && mv "$tmp" "$ENV"
unset secret

echo "已保存。现在试连一下 R2……"
set -a; . "$ENV"; set +a
export RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare \
  RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
  RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
probe="r2:${R2_BUCKET}/.caelum-probe"
if echo "hi $(date -Iseconds)" | rclone rcat "$probe" 2>/tmp/r2probe.err && rclone deletefile "$probe" 2>>/tmp/r2probe.err; then
  echo "✓ 连上了，能写也能删。告诉 Nox「填好了」就行，可以关掉这个窗口。"
else
  echo "✗ 没连上。最后几行原因（不含钥匙）："; tail -3 /tmp/r2probe.err | sed -E 's/[A-Za-z0-9]{32,}/****/g'
  echo "钥匙已经存着，检查一下哪项填错了再跑一次这条命令（会覆盖）。"
fi
rm -f /tmp/r2probe.err
