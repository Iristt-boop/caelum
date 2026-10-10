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
# 🔴 2026-10-05 补 orders.db / tasks.db：点单确认卡的待确认单 + 付款链接、长任务实体 + 进度日志。
# 它们是 nox-core 启动时按 `Path(cfg.db_path).parent / "xxx.db"` 新建的，
# 上线时（09-06 / 09-22）没人回来改这份清单，备份链于是一直没收它们。
# **新增任何 nox-core/data/*.db 都要来这里加一行**，同时 doctor.sh 的 REQUIRED_BACKUP_DBS 也要加
# （doctor 会解开最新备份包对账，漏了会红）。
FAIL=0
for db in /root/data/nox-bridge.db \
          /root/nox-core/data/sessions.db \
          /root/nox-core/data/attention.db \
          /root/nox-core/data/world.db \
          /root/nox-core/data/topics.db \
          /root/nox-core/data/orders.db \
          /root/nox-core/data/tasks.db \
          /root/nox-core/data/settings.db \
          /root/data/health.db \
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
# 书架封面（2026-10-10 补）：脚本能重抓，但不保证每本都抓得回来
[ -d /root/data/library-covers ] && cp -r /root/data/library-covers "$WORK/files/library-covers"
# 收藏的语音条（2026-09-29）：存的是她听到的那一段，重新合成就是另一个语气了，丢了找不回。
# 旁边的 tts-cache/ 是可重建的播放缓存，不进备份
[ -d /root/data/voice-favs ] && cp -r /root/data/voice-favs "$WORK/files/voice-favs"
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
# 🔴 2026-10-10 修（N150 搬家第一次真从备份包起时撞出来的）：
# 原来全拍平拷进 config/，nox-core/.env 和 netease-music-mcp/.env **同名**，
# 后拷的把前者覆盖 —— 他那份 111 行的主配置从来没进过备份，日志却一直是绿的。
# 现在按原路径放进 config/paths/（恢复：cp -a config/paths/. /），
# 并且断言 nox-core/.env 真的收到了。
for f in /root/nox-core/.env \
         /root/netease-music-mcp/.env \
         /root/eryu/server/.netease_cred \
         /root/eryu/server/.secret \
         /root/watch/cookies.txt \
         /root/ombre-brain/config.yaml \
         /root/co-reading-mcp/.env \
         /root/health-mcp/.env \
         /root/stackchan-mcp/tracker/.env \
         /root/stackchan-mcp/gateway/.env \
         /srv/noxtang-static/doh.mobileconfig \
         /etc/xray-cdn/config.json \
         /etc/x-ui/x-ui.db; do
  [ -f "$f" ] || continue
  mkdir -p "$WORK/config/paths$(dirname "$f")"
  cp -p "$f" "$WORK/config/paths$f"
done
ENV_LINES=$(grep -c . "$WORK/config/paths/root/nox-core/.env" 2>/dev/null) || true
if [ "${ENV_LINES:-0}" -lt 20 ]; then
  echo "$LOG_TAG ERROR: nox-core/.env 没收到或不完整（<20 行）—— 恢复出来的他调不了模型"
  FAIL=1
fi
# 定时任务和 /root 下的运维脚本（watch / 晨检 / care 周报 / 留存 / 部署）——
# 备份脚本以前连它自己都没收
crontab -l > "$WORK/config/crontab.root" 2>/dev/null || true
mkdir -p "$WORK/config/root-scripts"
cp -p /root/*.sh /root/*.py "$WORK/config/root-scripts/" 2>/dev/null || true

# 🔴 2026-09-11 补：/etc/nox/*.env **必须**在配置包里。
#
# 今天把 15 条凭据从 systemd unit 搬进 /etc/nox/*.env，又把 9 个 Caddy 路径暗号
# 从 Caddyfile 搬进 /etc/nox/caddy.env，而那份 Caddyfile **是**被备份的、
# /etc/nox/ **不是** —— 也就是说搬完之后，VPS 一没，恢复出来的是一份
# 没有暗号的 Caddyfile 模板，加一堆指向空变量的 unit。
#
# **「把密钥挪到更安全的地方」和「备份跟着改」是同一件事的两半。**
# 只做前一半，等于给自己挖了个更深的坑 —— 而且比原来更难发现，
# 因为原来的密钥至少还躺在一个被备份的文件里。
mkdir -p "$WORK/config/nox-env"
if compgen -G "/etc/nox/*.env" > /dev/null; then
  cp /etc/nox/*.env "$WORK/config/nox-env/"
  echo "$LOG_TAG config: /etc/nox/ 下 $(ls -1 /etc/nox/*.env | wc -l) 个 env 已收"
else
  echo "$LOG_TAG WARN: /etc/nox/ 下没有 .env —— 凭据可能还在别处，检查一下"
fi

# ---------- 3b) 只存在于 VPS 上的源码 ----------
#
# 下面这些服务的代码**不在任何 git 仓库里**（审计 4.8 的发现）：
#   co-watching / ha-mcp / toy-mcp / touch-mcp / touch-server / health-mcp / app-tracker
# 加上 co-reading-mcp 的代码（它的 data/ 已在上面单独收过）。
#
# 全加起来不到 5MB。相对于「VPS 一坏就永久丢失」，这点体积不值一提。
# 正确的长期修法是把它们纳入版本控制（排期 4.8）；在那之前，
# **至少别让它们只存在于一个地方**。
mkdir -p "$WORK/code"
# 🔴 2026-10-10 补：ombre-brain / eryu / netease-music-mcp 虽是 git 仓库，但线上
# 工作区都有没提交的改动（「走 git 拉」拉回来的不是在跑的那份）；co-listening /
# stackchan-mcp 不在任何仓库里。xiaozhi 的 data（小智 docker 的配置）同理。
# 另：co-watching / touch-* 的 code 是指向 releases 的软链接，原来 `tar cf` 只收到
# 一个悬空链接 —— 下面改 `tar chf` 跟随链接。
for d in /root/co-watching /root/co-reading-mcp /root/ha-mcp /root/toy-mcp \
         /root/touch-mcp /root/touch-server /root/health-mcp /root/app-tracker \
         /root/ombre-brain /root/eryu /root/netease-music-mcp \
         /root/co-listening /root/stackchan-mcp /opt/xiaozhi-server; do
  [ -d "$d" ] || continue
  n=$(basename "$d")
  mkdir -p "$WORK/code/$n"
  # buckets / music_cache 是数据不是代码（前者 2) 已收，后者可再生）；*.bak-* 是手工备份堆；
  # xiaozhi 的 models（893M 语音模型，可重下）/ mysql（206M，compose 里没起）不收 —— 收了包会从 50M 涨到 886M
  tar chf - -C "$d" \
      --exclude=node_modules --exclude=.venv --exclude=venv \
      --exclude=__pycache__ --exclude='*.pyc' --exclude=.git \
      --exclude=.pytest_cache --exclude=.ruff_cache --exclude='*.log' \
      --exclude=./buckets --exclude=music_cache --exclude='*.bak*' \
      --exclude=./models --exclude=./mysql \
      . 2>/dev/null | tar xf - -C "$WORK/code/$n" || true
done
echo "$LOG_TAG code: 收了 $(ls -1 "$WORK/code" 2>/dev/null | wc -l) 个只存在于 VPS 的服务源码"

# 🔴 2026-10-10 补：nox-core / bridge **正在跑的那个 release**。
# 线上是逐文件部署（deploy-vps.sh），跑的版本 ≠ 任何一个 git commit，
# 「从 git 拉」装出来的不是同一个他。原来以为「走 git 就不用收」，N150 搬家时验出来不成立。
mkdir -p "$WORK/releases"
for s in nox-core bridge; do
  r=$(readlink -f "/root/$s/code" 2>/dev/null) || continue
  [ -d "$r" ] || { echo "$LOG_TAG ERROR: /root/$s/code 不是有效 release"; FAIL=1; continue; }
  mkdir -p "$WORK/releases/$s"
  basename "$r" > "$WORK/releases/$s.CURRENT"
  tar cf - -C "$r" --exclude=node_modules --exclude=.venv --exclude=__pycache__ \
      --exclude='*.pyc' --exclude=.pytest_cache . \
    | tar xf - -C "$WORK/releases/$s"
done
# PWA 前端本身（bridge 的 FRONTEND_DIST）。不收的话恢复出来他在、她手机打开是 404
if [ -f /root/frontend/dist/index.html ]; then
  mkdir -p "$WORK/releases/frontend-dist"
  cp -a /root/frontend/dist/. "$WORK/releases/frontend-dist/"
else
  echo "$LOG_TAG ERROR: /root/frontend/dist/index.html 不在 —— PWA 前端没收到"; FAIL=1
fi
# nox-core/data 下的非 sqlite 状态（梦的影子日志 / 日志摘要）
mkdir -p "$WORK/files/nox-core-data"
cp -p /root/nox-core/data/*.json /root/nox-core/data/*.jsonl "$WORK/files/nox-core-data/" 2>/dev/null || true

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
# Linux:  openssl enc -d -aes-256-cbc -pbkdf2 -pass file:/root/.backup-pass \
#           -in caelum-XXXX.tar.gz.enc | tar xzf - -C /tmp/restore
# Windows openssl 不认 -pass file:/d/... 路径，改走 stdin：
#   cat BACKUP-PASS.txt | openssl enc -d -aes-256-cbc -pbkdf2 -pass stdin \
#     -in caelum-XXXX.tar.gz.enc | tar xzf - -C /tmp/restore
# sqlite: 直接放回原路径；文件：放回对应目录；config：对照 config/ 里的清单归位。
#   config/nox-env/  → 放回 /etc/nox/，目录 700、文件 600。**没有它服务起不来**：
#                      systemd 的 EnvironmentFile 全指这儿，包括 Caddy 的 9 个路径暗号。
#   config/paths/    → 按原路径放的散落配置（nox-core/.env 等）：cp -a config/paths/. /
#   config/crontab.root、config/root-scripts/ → crontab 和 /root 下的运维脚本
#   releases/<svc>/  → nox-core / bridge 正在跑的 release（<svc>.CURRENT 是目录名），
#                      放到 /root/releases/<svc>/<CURRENT>/，再把 /root/<svc>/code 链过去
#   code/            → 线上在跑的服务源码（含 git 仓库里没提交的改动），放回各自目录（见 3b）。
# 密码另有一份在糖糖的 Windows（D:\claude-code\backups\BACKUP-PASS.txt），VPS 被删时用它。
