#!/usr/bin/env bash
# 真正的异地副本：把当天的加密备份包推到 Cloudflare R2（2026-10-14，糖糖定）。
#
# 为什么要它：04:17 的包在 N150 上、她电脑每天中午拉一份 —— 两份在同一个屋檐下，
# 房子出事一起没。R2 在家外面。
#
# 规则（同本机 / 电脑那两份的轮转思路）：
#   daily/   每天一份，留最近 KEEP_DAILY 份
#   weekly/  每周日那份再放一份，留最近 KEEP_WEEKLY 份
# 包本身已经加密（caelum-backup.sh），R2 上的东西被人拿到也打不开；口令只在她手里。
#
# 🔴 钥匙不在这里、不在仓库：她自己写进 /root/.caelum-r2.env（0600），四行：
#   R2_ACCOUNT_ID=...  R2_ACCESS_KEY_ID=...  R2_SECRET_ACCESS_KEY=...  R2_BUCKET=...
# 钥匙只开这一个桶的读写（她在 Cloudflare 后台建的时候就限定）。
#
# 成功才写心跳 /root/.r2-ok —— 晨检看它（超 30 小时报✗）。判成功看的是**远端真实大小 = 本地大小**，
# 不是 rclone 的退出码（「上传成功」和「对面真有一份完整的」是两件事，pull-vps-backup.cmd 吃过这个亏）。
#
# R2_REMOTE / R2_ENV / BACKUP_DIR / R2_HEARTBEAT 可覆盖（测试用：指到本地目录当远端）。
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/root/backups/auto}
R2_ENV=${R2_ENV:-/root/.caelum-r2.env}
R2_HEARTBEAT=${R2_HEARTBEAT:-/root/.r2-ok}
KEEP_DAILY=${KEEP_DAILY:-7}
KEEP_WEEKLY=${KEEP_WEEKLY:-4}
TAG="[r2 $(date '+%m-%d %H:%M')]"

if [ -z "${R2_REMOTE:-}" ]; then
  if [ ! -r "$R2_ENV" ]; then
    echo "$TAG ✗ 没有 $R2_ENV（钥匙还没填）"; exit 1
  fi
  if [ "$(stat -c %a "$R2_ENV")" != "600" ]; then
    echo "$TAG ✗ $R2_ENV 权限不是 600，不用它"; exit 1
  fi
  # shellcheck disable=SC1090
  set -a; . "$R2_ENV"; set +a
  : "${R2_ACCOUNT_ID:?}" "${R2_ACCESS_KEY_ID:?}" "${R2_SECRET_ACCESS_KEY:?}" "${R2_BUCKET:?}"
  # 远端用环境变量现配，不写 rclone.conf（钥匙只留在那一个 600 文件里）
  export RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare
  export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
  export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
  export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
  export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
  # rclone 1.60 传完会带 versionId 再 HEAD 一次，R2 不支持（501）。大小我们自己用 lsjson 核
  export RCLONE_CONFIG_R2_NO_HEAD=true
  R2_REMOTE="r2:${R2_BUCKET}"
fi

latest=$(readlink -f "$BACKUP_DIR/latest.tar.gz.enc" 2>/dev/null || true)
if [ -z "$latest" ] || [ ! -f "$latest" ]; then
  echo "$TAG ✗ 本地没有最新备份包"; exit 1
fi
name=$(basename "$latest")
want=$(stat -c %s "$latest")

remote_size() {  # 远端那个文件的真实大小；没有就空
  rclone lsjson "$1" 2>/dev/null | python3 -c 'import json,sys
d=json.load(sys.stdin); print(d[0]["Size"] if d else "")' 2>/dev/null || true
}

push() {  # push <目录>：传上去，再按真实大小核对，最多试 3 次
  local dir=$1 try got
  for try in 1 2 3; do
    rclone copyto "$latest" "$R2_REMOTE/$dir/$name" --retries 1 --low-level-retries 3 -q || true
    got=$(remote_size "$R2_REMOTE/$dir/$name")
    if [ "$got" = "$want" ]; then
      echo "$TAG ✓ $dir/$name（$((want / 1000000))M）"; return 0
    fi
    echo "$TAG 第 $try 次没传完整（远端 ${got:-无} / 本地 $want）"; sleep 30
  done
  return 1
}

rotate() {  # rotate <目录> <留几份>：按文件名（带日期）排序，删最老的。最新那份永远不删
  local dir=$1 keep=$2
  rclone lsf "$R2_REMOTE/$dir" --files-only 2>/dev/null | grep -E '^caelum-.*\.tar\.gz\.enc$' | sort -r \
    | tail -n +$((keep + 1)) | while read -r old; do
        [ "$old" = "$name" ] && continue
        rclone deletefile "$R2_REMOTE/$dir/$old" -q && echo "$TAG 删掉超期的 $dir/$old"
      done
}

push daily || { echo "$TAG ✗ daily 传了 3 次都不完整，今天没有云端副本"; exit 1; }
rotate daily "$KEEP_DAILY"

if [ "$(date +%u)" = "7" ] || [ "${FORCE_WEEKLY:-}" = "1" ]; then
  push weekly || echo "$TAG ⚠️ weekly 没传成（daily 已经有了）"
  rotate weekly "$KEEP_WEEKLY"
fi

date -Iseconds > "$R2_HEARTBEAT"
echo "$TAG 完成"
