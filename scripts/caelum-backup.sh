#!/usr/bin/env bash
# Caelum 全量备份 —— 数据 + 配置 → 加密包 → /root/backups/auto/
# 每天 cron 跑；恢复方法见文件末尾注释。
set -euo pipefail

STAMP=$(date +%Y-%m-%d-%H%M)
WORK=$(mktemp -d /tmp/caelum-bak-XXXXXX)
DEST=/root/backups/auto
KEEP=14
PASS_FILE=/root/.backup-pass
LOG_TAG="[backup $STAMP]"

mkdir -p "$DEST"
[ -f "$PASS_FILE" ] || { echo "$LOG_TAG ERROR: $PASS_FILE 不存在，无法加密"; exit 1; }

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# ---------- 1) SQLite 热备 ----------
# 全是 WAL 库，直接 cp 可能拿到 torn 副本；.backup 在线备份保证一致性。
# bridge 的 nox-bridge.db 是 sql.js 全量覆写（非原子），理论上可能撞上写一半，
# 所以下面每份都做 integrity_check，失败重试一次。
backup_sqlite() {
  local src="$1" out="$2" try
  for try in 1 2; do
    if sqlite3 "$src" ".backup '$out'" 2>/dev/null \
       && sqlite3 "$out" "PRAGMA integrity_check;" 2>/dev/null | grep -q "^ok$"; then
      return 0
    fi
    echo "$LOG_TAG WARN: $src 第 $try 次备份校验失败，重试"
    rm -f "$out"; sleep 3
  done
  echo "$LOG_TAG ERROR: $src 备份两次校验都失败"; return 1
}

mkdir -p "$WORK/sqlite"
FAIL=0
for db in /root/data/nox-bridge.db \
          /root/nox-core/data/sessions.db \
          /root/nox-core/data/attention.db \
          /root/nox-core/data/world.db \
          /root/nox-core/data/topics.db \
          /root/ombre-brain/buckets/embeddings.db; do
  if [ -f "$db" ]; then
    backup_sqlite "$db" "$WORK/sqlite/$(basename "$db")" || FAIL=1
  else
    echo "$LOG_TAG WARN: 缺 $db，跳过"
  fi
done

# ---------- 2) 文件型数据 ----------
mkdir -p "$WORK/files"
cp -r /root/ombre-brain/buckets "$WORK/files/ombre-buckets"   # 记忆 .md + dehydration_cache.db
rm -f "$WORK/files/ombre-buckets/embeddings.db"               # 已单独热备，去重
cp -r /root/data/uploads      "$WORK/files/uploads"           # 照片
cp -r /root/co-reading-mcp/data "$WORK/files/coreading-data"  # 批注/进度/书
# eryu：只要记忆/歌单 JSON；music_cache(260M 音频缓存) 可从网易云再生，不进备份
mkdir -p "$WORK/files/eryu-data"
find /root/eryu/server/data -maxdepth 1 -type f -name "*.json*" \
  -exec cp {} "$WORK/files/eryu-data/" \;

# ---------- 3) 配置包（没有它恢复不了：密钥/反代/服务定义）----------
mkdir -p "$WORK/config/systemd"
cp /etc/caddy/Caddyfile "$WORK/config/" 2>/dev/null || echo "$LOG_TAG WARN: 无 Caddyfile"
cp /etc/systemd/system/*.service "$WORK/config/systemd/" 2>/dev/null || true
cp /etc/systemd/system/*.timer   "$WORK/config/systemd/" 2>/dev/null || true
find /etc/systemd/system -maxdepth 1 -type d -name "*.d" \
  -exec cp -r {} "$WORK/config/systemd/" \; 2>/dev/null || true
for f in /root/nox-core/.env \
         /root/netease-music-mcp/.env \
         /root/eryu/server/.netease_cred \
         /root/eryu/server/.secret \
         /root/watch/cookies.txt \
         /root/ombre-brain/config.yaml; do
  [ -f "$f" ] && cp "$f" "$WORK/config/"
done

# ---------- 4) 打包 + 加密 + 轮转 ----------
OUT="$DEST/caelum-$STAMP.tar.gz.enc"
tar czf - -C "$WORK" . \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass file:"$PASS_FILE" -out "$OUT"
chmod 600 "$OUT"
ln -sfn "$(basename "$OUT")" "$DEST/latest.tar.gz.enc"

ls -1t "$DEST"/caelum-*.tar.gz.enc | tail -n +$((KEEP + 1)) | xargs -r rm -f

SIZE=$(du -h "$OUT" | cut -f1)
if [ "$FAIL" -ne 0 ]; then
  echo "$LOG_TAG 完成（有库校验失败，见上）：$OUT ($SIZE)"
  exit 2
fi
echo "$LOG_TAG 完成：$OUT ($SIZE)"

# ---------- 恢复方法 ----------
# openssl enc -d -aes-256-cbc -pbkdf2 -pass file:/root/.backup-pass \
#   -in caelum-XXXX.tar.gz.enc | tar xzf - -C /tmp/restore
# sqlite: 直接放回原路径；文件：放回对应目录；config：对照 config/ 里的清单归位。
# 密码另有一份在糖糖的 Windows（D:\claude-code\backups\BACKUP-PASS.txt），VPS 被删时用它。
