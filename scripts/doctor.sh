#!/usr/bin/env bash
# Caelum 一键体检 —— 全部只读，不动任何服务
# 用法: /root/doctor.sh        （人看的 [✓]/[!]/[✗] 报告，退出码 0=健康 1=有问题）
set -u
OK="✓"; WARN="!"; BAD="✗"; NOTE="·"
ISSUES=0; NOTES=0

say()  { echo "[$1] $2"; }
good() { say "$OK" "$1"; }
warn() { say "$WARN" "$1"; ISSUES=$((ISSUES+1)); }
bad()  { say "$BAD" "$1"; ISSUES=$((ISSUES+1)); }

#: note 是**不计数**的：给「已知、已确认接受、暂时不动」的事用。
#:
#: 🔴 为什么非要有这一级（2026-09-12 加）：
#:    如果已知问题也让体检退出 1，那**真出了新问题就淹在噪音里**，
#:    报告就变成"又有那两条"然后没人看 —— 这正是这份体检要防的坏事。
#:    她明确说了触觉那条线不重要，所以它和"网易云凭据快过期"都归到这一级。
note() { say "$NOTE" "$1"; NOTES=$((NOTES+1)); }

#: 🔴 **服务清单只有这一份。**
#:
#: 审计点名过：原来 `monitor.sh` 自己抄了一份，两边慢慢走散 ——
#: 它那份**漏了 nox-core**，还留着早就退役的 `cloudflared-*`。
#: 于是"他本人挂了"这件事，监控从来看不见。
#:
#: 现在 `caelum-watch.sh` 用 `doctor.sh --list-services` 取这一份，
#: 不再自己抄。加服务只改这一行，两边同时生效。
#:
#: ⚠️ caddy 2026-09-11 才补进来：它是整套系统**唯一的入口**，
#: 它挂了 = App / MCP / touch / 所有域名全挂。原来这里居然一直没查它。
CAELUM_SERVICES="caddy bridge nox-core ombre-brain co-reading co-watching eryu netease-mcp"

if [ "${1:-}" = "--list-services" ]; then
  echo "$CAELUM_SERVICES"
  exit 0
fi

echo "════════ Caelum 体检 $(date '+%F %T') ════════"

# ── 1) systemd 服务 ──────────────────────────────
echo "-- 服务 --"
for s in $CAELUM_SERVICES; do
  st=$(systemctl is-active "$s" 2>/dev/null)
  if [ "$st" = "active" ]; then good "$s running"; else bad "$s = $st"; fi
done
if systemctl is-active nox-daily.timer >/dev/null 2>&1; then
  good "nox-daily.timer 已启用（早报）"
else
  warn "nox-daily.timer 没在跑，早报会断"
fi

# ── 1.5) 遗忘曲线到底跑没跑（排期 4.6，2026-09-16）──────
#
# 🔴 判的是**结果**，不是机制。
#
# 「ombre-brain.service active」只说明进程活着；
# 「ombre-brain-decay.timer active」只说明闹钟还在响 ——
# 闹钟响了、curl 401、衰减一轮没跑，这两个检查**全是绿的**。
#
# 所以这里判 OB 自己报的 last_decay_at：那是衰减**真的跑完**才会动的值。
# 阈值 36 小时：定时器是每天 04:00，给一天多一点的余量，
# 不至于因为一次 RandomizedDelaySec 或补跑就误报。
if systemctl is-enabled ombre-brain-decay.timer >/dev/null 2>&1; then
  good "ombre-brain-decay.timer 已启用（遗忘曲线的时钟）"
else
  warn "ombre-brain-decay.timer 没启用 —— 衰减会退回「有人调工具才跑」"
fi
# 数据保留策略（排期 4.7）。每周日 04:30 的 oneshot。
#
# ⚠️ oneshot 平时是 inactive(dead)，那是**正常状态**，不能拿 is-active 判它 ——
#    那样每天都会误报。判的是 is-failed：上一次跑砸没砸。
if systemctl is-enabled caelum-retention.timer >/dev/null 2>&1; then
  if [ "$(systemctl is-failed caelum-retention.service 2>/dev/null)" = "failed" ]; then
    bad "上次数据保留跑砸了 —— journalctl -u caelum-retention"
  else
    good "caelum-retention.timer 已启用（数据保留）"
  fi
else
  warn "caelum-retention.timer 没启用 —— 活动追踪会一直攒下去"
fi

DH=$(curl -s -m 10 http://127.0.0.1:8002/health 2>/dev/null)
if [ -z "$DH" ]; then
  bad "OB /health 无响应，查不了衰减跑没跑"
else
  # 退出码：0=新鲜 1=太久没跑 2=从来没跑过 3=读不出来
  # ⚠️ 判据走退出码，不匹配文本（编码一变就永远为假）
  DAGE=$(python3 - "$DH" <<'PYEOF'
import json, sys
from datetime import datetime
try:
    at = json.loads(sys.argv[1]).get("last_decay_at")
except Exception:
    sys.exit(3)
if not at:
    sys.exit(2)
try:
    hours = (datetime.now() - datetime.fromisoformat(at)).total_seconds() / 3600
except Exception:
    sys.exit(3)
print(f"{hours:.1f}")
sys.exit(0 if hours <= 36 else 1)
PYEOF
)
  case $? in
    0) good "衰减 ${DAGE}h 前跑过" ;;
    1) bad  "衰减 ${DAGE}h 没跑了 —— 定时器响了但没干成事，查 journalctl -u ombre-brain-decay" ;;
    2) warn "OB 重启后还没跑过一轮衰减（重启当天正常，连着两天就不正常）" ;;
    *) bad  "读不出 last_decay_at —— OB 是不是回退到没有 4.6 的版本了" ;;
  esac
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
  if [ "$d" -gt 45 ]; then note "网易云凭据文件已 $d 天没更新——cookie 大概率过期，点歌会哑（重新扫码提取）"
  elif [ "$d" -gt 30 ]; then note "网易云凭据 $d 天了，留意过期"
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
  good "本机保留 $n 份"
  # ── 异地副本（2026-09-11 新增）─────────────────────────────
  # 盲区就在这儿：原来只报"本机保留 n 份"，**异地那一跳死没死没人管**。
  # 2026-09-06 起 scp 连续 5 天失败（引号 bug），异地副本一直停在 09-05，
  # 而 doctor.sh 全绿、晨检卡也全绿 —— 因为没人检查最后那一公里。
  # 心跳由 scripts/pull-vps-backup.cmd 在拉成功后回写 /root/.offsite-ok。
  # 容忍 30 小时：漏一次还能忍（她那台 Windows 可能休眠/关机），
  # 连着两次不成功就该有人知道。
  if [ -f /root/.offsite-ok ]; then
    od=$(( (NOW - $(stat -c %Y /root/.offsite-ok)) / 3600 ))
    if [ "$od" -lt 30 ]; then good "异地副本心跳 $(( od )) 小时前（Windows → D:\\claude-code\\backups\\vps\\）"
    else warn "异地副本已经 $(( od / 24 )) 天没拉走了——去 Windows 看 backups\\vps\\pull.log"; fi
  else
    warn "从没见过异地副本心跳（/root/.offsite-ok 不存在）——Windows 那个 12:30 的任务成功过吗？"
  fi
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

# ── 8) Caddy 路径暗号（空值是最难查的坏法）────────
#
# 2026-09-11：9 个路径暗号从 Caddyfile 搬进 /etc/nox/caddy.env（0600），
# systemd 通过 drop-in 注入给 ExecStart 和 ExecReload。
#
# ⚠️ 这个改动有一种**极难查**的坏法：env 文件丢了 / 变量名打错了 / 权限不对，
#    {$CADDY_TOKEN_*} 会解析成**空串** —— 路径变成 /ombre//。而 Caddy 照常启动、
#    照常 reload，日志里一个字都没有，只有那几条路径**静默变成 404**。
#    所以这里查两件事：env 文件本身，以及**运行中配置里有没有空值**。
echo "-- Caddy 暗号 --"
if [ ! -f /etc/nox/caddy.env ]; then
  bad "/etc/nox/caddy.env 不存在 —— Caddyfile 里的 {\$CADDY_TOKEN_*} 会解析成空串"
else
  CPERM=$(stat -c %a /etc/nox/caddy.env)
  if [ "$CPERM" = "600" ]; then good "caddy.env 权限 600"; else bad "caddy.env 权限是 $CPERM，应为 600"; fi
  # ⚠️ 这里不能用 `|| echo 0`：`grep -c` 在**没有匹配时也会打印 0**，只是退出码为 1。
  #    加上 `|| echo 0` 就变成两行 "0\n0"，`[ "$CEMPTY" = "0" ]` 永远不成立 ——
  #    第一版就踩了，doctor 一直误报「有 2 处要注意」。用 `|| true` 只吞退出码。
  CEMPTY=$(grep -cE '^[A-Za-z_][A-Za-z0-9_]*=$' /etc/nox/caddy.env 2>/dev/null || true)
  CFILLED=$(grep -cE '^CADDY_TOKEN_[A-Za-z0-9_]+=.+' /etc/nox/caddy.env 2>/dev/null || true)
  if [ "$CEMPTY" = "0" ]; then good "caddy.env 里 $CFILLED 个暗号都已填值"
  else bad "caddy.env 里有 $CEMPTY 个空值 —— 对应的路径会静默 404"; fi
fi
CCFG=$(curl -s --max-time 5 http://localhost:2019/config/ 2>/dev/null || true)
if [ -z "$CCFG" ]; then
  warn "拿不到 Caddy 管理接口（localhost:2019），跳过运行时空值检查"
elif printf '%s' "$CCFG" | python3 -c '
import json, re, sys
cfg = json.load(sys.stdin)
paths = []
def w(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "path" and isinstance(v, list): paths.extend(v)
            w(v)
    elif isinstance(o, list):
        for i in o: w(i)
w(cfg)
uniq = set(paths)
bad = sorted(p for p in uniq if "//" in p or "{$" in p)
if bad:
    print("BAD " + " | ".join(bad[:5]))
    sys.exit(1)
n = len([p for p in uniq if re.search(r"/[0-9a-f]{16,}|/[A-Za-z0-9]{24}", p)])
print("OK %d" % n)
' > /tmp/.caddy-chk 2>/dev/null; then
  good "Caddy 运行时 $(cut -d' ' -f2 < /tmp/.caddy-chk) 条受保护路径解析正常"
else
  bad "Caddy 运行时有空变量/未解析占位符：$(cut -c1-110 < /tmp/.caddy-chk 2>/dev/null) —— 那几条路径正在静默 404"
fi
rm -f /tmp/.caddy-chk

# ── 9) 部署软链自检（断链 = 下次重启就起不来）────
#
# 2026-09-11 铺 release 布局时自己撞到的：release 目录被删掉、而软链还指着它。
# 表现是「服务现在好好的」—— 因为代码已经在内存里了 —— **但下次重启就起不来**。
# 这是最阴的一类故障：在你重启之前它一个字都不表现。
#
# ⚠️ deploy-remote.sh 自己的 KEEP 清理是**保护当前指向那份**的
#    （`case "$(readlink -f "$LINK")"`），所以正常流程不会产生断链；
#    会断的是人手动删 release 目录。这条就是给那种时刻兜底的。
echo "-- 部署软链 --"
BROKEN=0
for pair in nox-core:/root/nox-core/code \
            bridge:/root/bridge/code \
            touch-server:/root/touch-server/code \
            co-watching:/root/co-watching/code \
            touch-mcp:/root/touch-mcp/code; do
  svc=${pair%%:*}; link=${pair#*:}
  # -L 判「它是软链」，-e 会跟随软链（断链时为假）
  if [ -L "$link" ] && [ ! -e "$link" ]; then
    bad "$svc 的 code 软链是断的 → $(readlink "$link") —— 现在跑着没事，重启就起不来"
    BROKEN=$((BROKEN + 1))
  fi
done
[ "$BROKEN" = "0" ] && good "release 布局的服务软链都有效"

# ── 10) 触觉那条线还活着吗 ────────────────────────
#
# 2026-09-12 发现：`touch_moments.jsonl` 最后一条记录是 **2026-08-24** ——
# 「她摸了我」这条线已经死了约 2.5 周，而当时所有检查都是绿的。
# 没有任何东西在盯「上一次收到上报是什么时候」。又一个静默失败。
#
# ⚠️ 判据用**最后一条记录里的时间**，不是文件 mtime：
#    mtime 会被编辑动到（2026-09-11 我手动清过两条测试记录，
#    mtime 变成当天，看起来像"刚刚有上报"）。
echo "-- 触觉上报 --"
TCHF=/root/touch-server/data/touch_moments.jsonl
if [ ! -f "$TCHF" ]; then
  warn "找不到 $TCHF"
else
  TCHLAST=$(python3 -c "
import json
last = ''
for line in open('$TCHF', encoding='utf-8'):
    line = line.strip()
    if not line:
        continue
    try:
        o = json.loads(line)
    except Exception:
        continue
    t = o.get('received_at') or o.get('at') or ''
    if t > last:
        last = t
print(last)
" 2>/dev/null)
  if [ -z "$TCHLAST" ]; then
    warn "触觉记录里没有带时间的条目"
  else
    TCHDAYS=$(python3 -c "
import datetime
t = datetime.datetime.fromisoformat('$TCHLAST')
print(int((datetime.datetime.now() - t).total_seconds() // 86400))
" 2>/dev/null)
    if [ "${TCHDAYS:-0}" -gt 7 ]; then
      # ⚠️ 用 note 不用 bad：她 2026-09-12 明确说了**触觉那条线不重要**。
      #    如果它让体检永远退 1，真出了新问题就淹在噪音里 —— 报告就没人看了。
      #    要恢复得三件事同时成立：设备重烧（带 token）+ 安全组放行 9333 + TOUCH_TOKEN 已在生效。
      note "最后一次触觉上报是 ${TCHLAST}（${TCHDAYS} 天前）—— 已知，暂不处理（设备 / WiFi / 安全组）"
    else
      good "触觉上报正常（最后 ${TCHLAST}）"
    fi
  fi
fi

echo "════════"
if [ "$ISSUES" -eq 0 ]; then
  if [ "$NOTES" -gt 0 ]; then
    echo "没有需要现在处理的事 ✓（另有 $NOTES 条已知项，带 [·]，不计数）"
  else
    echo "全部正常 ✓"
  fi
  exit 0
else
  echo "有 $ISSUES 处要注意（上面带 [!] / [✗] 的行）"
  [ "$NOTES" -gt 0 ] && echo "（另有 $NOTES 条已知项，带 [·]，不计数）"
  exit 1
fi
