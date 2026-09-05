#!/usr/bin/env bash
# Caelum 一键体检 —— 全部只读，不动任何服务
# 用法: /root/doctor.sh        （人看的 [✓]/[!]/[✗] 报告，退出码 0=健康 1=有问题）
set -u
OK="✓"; WARN="!"; BAD="✗"
ISSUES=0

say()  { echo "[$1] $2"; }
good() { say "$OK" "$1"; }
warn() { say "$WARN" "$1"; ISSUES=$((ISSUES+1)); }
bad()  { say "$BAD" "$1"; ISSUES=$((ISSUES+1)); }

echo "════════ Caelum 体检 $(date '+%F %T') ════════"

# ── 1) systemd 服务 ──────────────────────────────
echo "-- 服务 --"
for s in bridge nox-core ombre-brain co-reading co-watching eryu netease-mcp; do
  st=$(systemctl is-active "$s" 2>/dev/null)
  if [ "$st" = "active" ]; then good "$s running"; else bad "$s = $st"; fi
done
if systemctl is-active nox-daily.timer >/dev/null 2>&1; then
  good "nox-daily.timer 已启用（早报）"
else
  warn "nox-daily.timer 没在跑，早报会断"
fi

# ── 2) 探活聚合（bridge /api/health）─────────────
echo "-- 探活 --"
H=$(curl -s -m 30 http://127.0.0.1:3003/api/health 2>/dev/null)
if [ -z "$H" ]; then
  bad "bridge /api/health 无响应"
else
  python3 - "$H" <<'PYEOF'
import json, sys
d = json.loads(sys.argv[1])
ok = d.get("status") == "ok"
print(("  [✓]" if ok else "  [✗]") + " 总体: " + d.get("status"))
for k, v in d.get("checks", {}).items():
    if k == "bridge": continue
    mark = "✓" if v.get("ok") else "✗"
    extra = ""
    if v.get("model"):   extra += " model=" + v["model"]
    if v.get("buckets"): extra += " buckets=" + str(v["buckets"])
    if v.get("decay"):   extra += " decay=" + v["decay"]
    if v.get("error"):   extra += " err=" + v["error"]
    print(f"  [{mark}] {k} {v.get('ms','?')}ms{extra}")
PYEOF
  echo "$H" | grep -q '"status":"ok"' || ISSUES=$((ISSUES+1))
fi

# ── 3) Attention / World 心跳（看库文件的最近写入）──
echo "-- 引擎心跳 --"
NOW=$(date +%s)
fresh() {  # $1=库路径  $2=容忍分钟数 → 0=新鲜
  local newest=0 f
  for f in "$1" "$1-wal"; do
    [ -f "$f" ] || continue
    m=$(stat -c %Y "$f" 2>/dev/null || echo 0)
    [ "$m" -gt "$newest" ] && newest=$m
  done
  [ $(( NOW - newest )) -lt $(( $2 * 60 )) ]
}
if fresh /root/nox-core/data/attention.db 30; then good "attention 引擎在跳（30 分钟内有写入）"
else warn "attention 引擎超过 30 分钟没动静——快慢循环可能死了"; fi
if fresh /root/nox-core/data/world.db 120; then good "world model 在写入（2 小时内）"
else warn "world model 超过 2 小时没写入（各事实有自己的节奏，偶尔慢是正常的）"; fi
if fresh /root/data/nox-bridge.db 1440; then good "bridge 库今天有写入"
else warn "bridge 库超过 24 小时没写入？"; fi

# ── 4) 会过期的东西 ──────────────────────────────
echo "-- 会过期的 --"
age_days() { echo $(( (NOW - $(stat -c %Y "$1" 2>/dev/null || echo NOW)) / 86400 )); }
NC_ENV=/root/netease-music-mcp/.env
if [ -f "$NC_ENV" ]; then
  d=$(age_days "$NC_ENV")
  if [ "$d" -gt 45 ]; then warn "网易云凭据文件已 $d 天没更新——cookie 大概率过期，点歌会哑（重新扫码提取）"
  elif [ "$d" -gt 30 ]; then warn "网易云凭据 $d 天了，留意过期"
  else good "网易云凭据 $d 天前更新过"; fi
else warn "找不到 $NC_ENV"; fi
WATCH_COOKIES=/root/watch/cookies.txt
[ -f "$WATCH_COOKIES" ] && good "B站 cookies 存在（$(( $(age_days "$WATCH_COOKIES") )) 天前更新）" \
                        || warn "B站 cookies 不存在，共影拉流可能被拦"

# ── 5) 备份 ─────────────────────────────────────
echo "-- 备份 --"
LATEST=$(ls -1t /root/backups/auto/caelum-*.tar.gz.enc 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
  bad "一份备份都没有"
else
  d=$(( (NOW - $(stat -c %Y "$LATEST") ) / 3600 ))
  if [ "$d" -lt 26 ]; then good "最新备份 $(( d )) 小时前：$(basename "$LATEST") ($(du -h "$LATEST" | cut -f1))"
  else warn "最新备份已经 $(( d / 24 )) 天了——cron 死了？"; fi
  n=$(ls -1 /root/backups/auto/caelum-*.tar.gz.enc 2>/dev/null | wc -l)
  good "本机保留 $n 份（异地副本在你 Windows 的 D:\\claude-code\\backups\\vps\\）"
fi

# ── 6) 系统资源 ─────────────────────────────────
echo "-- 系统 --"
DISK=$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')
if [ "$DISK" -gt 85 ]; then warn "磁盘用了 ${DISK}%"
else good "磁盘 ${DISK}%"; fi
MEM=$(free -m | awk '/Mem:/ {print $7}')
if [ "$MEM" -lt 200 ]; then warn "可用内存只剩 ${MEM}MB"
else good "可用内存 ${MEM}MB"; fi
UP=$(uptime -p | cut -d' ' -f2-)
good "已运行 $UP"

# ── 7) 仓库卫生（secrets 不许被追踪）────────────
echo "-- 仓库卫生 --"
if git -C /root/ombre-brain ls-files --error-unmatch config.yaml >/dev/null 2>&1; then
  bad "Ombre-Brain 的 config.yaml 还被 git 追踪着（里面有真实 key）"
else
  good "OB config.yaml 未被追踪"
fi

echo "════════"
if [ "$ISSUES" -eq 0 ]; then
  echo "全部正常 ✓"
  exit 0
else
  echo "有 $ISSUES 处要注意（上面带 [!] / [✗] 的行）"
  exit 1
fi
