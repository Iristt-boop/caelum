#!/usr/bin/env bash
# CAELUM 部署状态：线上每个服务现在跑的是哪个版本
#
# 由 scripts/deploy.ps1 -Status 上传并调用。
# 单独跑：ssh root@VPS 'bash /root/deploy-status.sh'
#
# ⚠️ 这个文件用「上传成文件再跑」而不是「从 stdin 喂」——
#    PowerShell 5.1 往 native stdin 写时会给最后一行补 CRLF，
#    会污染最后一个命令的参数（2026-09-11 被 `head -n 20` 坑过一次）。
set -u

echo "服务          当前软链指向          unit 状态    健康检查"
echo "────────────────────────────────────────────────────────────"

check() {
  local svc="$1" link="$2" unit="$3" health="$4"
  local ver st code
  if [ -L "$link" ]; then
    ver=$(basename "$(readlink "$link")")
  elif [ -d "$link" ]; then
    ver="（真目录·未迁移）"
  else
    ver="（不存在）"
  fi
  st=$(systemctl is-active "$unit" 2>/dev/null || echo "-")
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 "$health" 2>/dev/null || echo 000)
  printf '%-13s %-21s %-12s %s\n' "$svc" "$ver" "$st" "$code"
}

check nox-core     /root/nox-core/code     nox-core     http://127.0.0.1:8100/health
check bridge       /root/bridge/code       bridge       http://127.0.0.1:3003/api/health
check touch-server /root/touch-server/code touch-server http://127.0.0.1:9333/health

echo
echo "── release 目录（新的在上）────────────────────────────────"
if compgen -G "/root/releases/*/*/" > /dev/null; then
  ls -1dt /root/releases/*/*/ 2>/dev/null | awk 'NR<=20 {printf "  %s\n", $0}'
else
  echo "  （还没有任何 release）"
fi

echo
echo "── 最近一次部署日志 ──────────────────────────────────────"
for f in /root/releases/nox-core.deploy.log /root/releases/bridge.deploy.log \
         /root/releases/touch-server.deploy.log; do
  [ -f "$f" ] || continue
  echo "  [$f]"
  tail -n 4 "$f" | sed 's/^/    /'
done

echo
echo "── release 占用的磁盘 ────────────────────────────────────"
du -sh /root/releases 2>/dev/null | sed 's/^/  /'
