#!/usr/bin/env bash
# ============================================================
# caelum-restore.sh —— 从 caelum-backup.sh 的加密包把他整个放回一台机器
#
# 2026-10-10 N150 搬家时写的（排期 5.2：备份从没真正恢复过）。
# 规矩：新机器只许走这条路起，不许 rsync —— 缺什么就是备份的 bug，
# 回去改 caelum-backup.sh，别在这里手工补。
#
# 用法（在目标机上，root）：
#   caelum-restore.sh <caelum-XXXX.tar.gz.enc> <密码文件>
#
# 只放文件，不起服务：起服务、装依赖、改 Caddyfile 是另外的步骤
# （见 docs/research/CAELUM-Nox搬家调研-VPS转N150-2026-09-21.md §七）。
# 可以重复跑：切换日用最新包再跑一次，数据整个覆盖。
# ============================================================
set -euo pipefail

PKG="${1:?缺备份包路径}"
PASS="${2:?缺密码文件路径}"

# 🔴 保险：nox-core 正在这台机器上跑，就不许覆盖 —— 防止在线上机误跑
if systemctl is-active --quiet nox-core 2>/dev/null; then
  echo "ERROR: 这台机器上 nox-core 正在运行。先 systemctl stop 所有 Caelum 服务再恢复。" >&2
  exit 1
fi

X=$(mktemp -d /root/restore-XXXXXX)
trap 'rm -rf "$X"' EXIT
openssl enc -d -aes-256-cbc -pbkdf2 -pass file:"$PASS" -in "$PKG" | tar xzf - --no-same-owner -C "$X"
# 判据是解出来的东西，不是管道退出码
[ -d "$X/sqlite" ] && [ -d "$X/config" ] || { echo "ERROR: 包解不开或结构不对" >&2; exit 2; }

# ---------- 1) 代码（先放代码，数据随后覆盖在上面）----------
for d in "$X"/code/*; do
  n=$(basename "$d")
  case "$n" in
    xiaozhi-server) dst=/opt/xiaozhi-server ;;
    *)              dst=/root/$n ;;
  esac
  mkdir -p "$dst"
  cp -a "$d"/. "$dst"/
done
for s in nox-core bridge; do
  [ -f "$X/releases/$s.CURRENT" ] || { echo "ERROR: 包里没有 $s 的 release" >&2; exit 2; }
  cur=$(cat "$X/releases/$s.CURRENT")
  mkdir -p "/root/releases/$s/$cur" "/root/$s"
  cp -a "$X/releases/$s"/. "/root/releases/$s/$cur"/
  ln -sfn "/root/releases/$s/$cur" "/root/$s/code"
  echo "$cur" > "/root/$s/CURRENT"
done
[ -f "$X/releases/frontend-dist/index.html" ] || { echo "ERROR: 包里没有 PWA 前端" >&2; exit 2; }
rm -rf /root/frontend/dist && mkdir -p /root/frontend/dist
cp -a "$X/releases/frontend-dist"/. /root/frontend/dist/

# ---------- 2) SQLite ----------
put_db() {  # $1=库名 $2=目标目录
  mkdir -p "$2"
  rm -f "$2/$1-wal" "$2/$1-shm"   # 旧 WAL 留着会被重放到新库上
  cp "$X/sqlite/$1" "$2/$1"
}
put_db nox-bridge.db /root/data
put_db health.db     /root/data
for db in sessions.db attention.db world.db topics.db orders.db tasks.db settings.db; do
  put_db "$db" /root/nox-core/data
done

# ---------- 3) 文件型数据 ----------
mkdir -p /root/ombre-brain/buckets /root/data /root/co-reading-mcp /root/eryu/server/data /root/nox-core/data
cp -a "$X/files/ombre-buckets"/. /root/ombre-brain/buckets/
put_db embeddings.db /root/ombre-brain/buckets
cp -a "$X/files/uploads" /root/data/
[ -d "$X/files/library-covers" ] && cp -a "$X/files/library-covers" /root/data/
[ -d "$X/files/voice-favs" ]     && cp -a "$X/files/voice-favs" /root/data/
rm -rf /root/co-reading-mcp/data && cp -a "$X/files/coreading-data" /root/co-reading-mcp/data
cp -a "$X/files/eryu-data"/. /root/eryu/server/data/
[ -d "$X/files/nox-core-data" ] && cp -a "$X/files/nox-core-data"/. /root/nox-core/data/

# ---------- 4) 配置 ----------
mkdir -p /etc/nox && chmod 700 /etc/nox
cp "$X/config/nox-env"/*.env /etc/nox/ && chmod 600 /etc/nox/*.env
cp -a "$X/config/paths"/. /
mkdir -p /root/restored-config
cp "$X/config/Caddyfile" /root/restored-config/Caddyfile.from-backup   # 原样留档，目标机的 Caddyfile 另行适配
[ -f "$X/config/crontab.root" ] && cp "$X/config/crontab.root" /root/restored-config/
[ -d "$X/config/root-scripts" ] && cp -a "$X/config/root-scripts"/. /root/

# systemd：只放 Caelum 自己的单元，系统/云厂商/翻墙的不要
CAELUM_UNITS="app-tracker bridge caelum-retention co-listening co-reading co-watching eryu
  ha-mcp health-mcp health-sync mcp-train mcp-trends netease-mcp nox-core nox-daily
  ombre-brain ombre-brain-consolidate ombre-brain-decay stackchan-gateway stackchan-tracker
  touch-mcp touch-server toy-mcp"
for u in $CAELUM_UNITS; do
  for f in "$X/config/systemd/$u.service" "$X/config/systemd/$u.timer"; do
    [ -f "$f" ] && cp "$f" /etc/systemd/system/
  done
  [ -d "$X/config/systemd/$u.service.d" ] && cp -a "$X/config/systemd/$u.service.d" /etc/systemd/system/
done
[ -d "$X/config/systemd/caddy.service.d" ] && cp -a "$X/config/systemd/caddy.service.d" /etc/systemd/system/
find /etc/systemd/system -name '*.bak*' -delete
systemctl daemon-reload

# ---------- 5) 自检：放回去的东西能不能用 ----------
FAIL=0
for db in /root/data/nox-bridge.db /root/data/health.db /root/nox-core/data/sessions.db \
          /root/nox-core/data/attention.db /root/nox-core/data/world.db \
          /root/ombre-brain/buckets/embeddings.db; do
  sqlite3 "$db" "PRAGMA integrity_check;" 2>/dev/null | grep -qx ok || { echo "BAD: $db"; FAIL=1; }
done
n=$(grep -c . /root/nox-core/.env 2>/dev/null) || true
[ "${n:-0}" -ge 20 ] || { echo "BAD: /root/nox-core/.env 只有 ${n:-0} 行"; FAIL=1; }
[ -f /root/nox-core/code/api/server.py ] || { echo "BAD: nox-core release 不完整"; FAIL=1; }
[ -f /root/bridge/code/server.js ]       || { echo "BAD: bridge release 不完整"; FAIL=1; }
[ -f /root/frontend/dist/index.html ]    || { echo "BAD: PWA 前端不在"; FAIL=1; }
b=$(find /root/ombre-brain/buckets -name '*.md' | wc -l)
echo "记忆桶 $b 个 .md；nox-core $(cat /root/nox-core/CURRENT)；bridge $(cat /root/bridge/CURRENT)"
[ "$FAIL" -eq 0 ] && echo "恢复完成" || { echo "恢复有问题，见上"; exit 3; }
