#!/usr/bin/env bash
# Caelum 看门狗 —— 每 5 分钟一次，坏了推她手机（排期 1.5）
#
# ════════════════════════════════════════════════════════════════
# ## 为什么要重写，而不是修 monitor.sh
#
# 2026-09-13 查线上才发现：**`monitor.sh` 从来没部署过。**
# 没有 `/root/monitor.sh`、cron 里没有它、连它要写的
# `/var/log/nox-monitor.log` 都不存在。
#
# 审计说的是「monitor.sh 漏掉 nox-core，告警写进没人读的文件」——
# **真实情况更糟：根本没有任何自动监控。** `doctor.sh` 写得很好，
# 但它也没排期，只能手动跑。
#
# 那份旧脚本还带着三个不能要的东西，所以直接删了重写：
#   · `echo 1 > /proc/sys/vm/drop_caches` —— 4GB 无 swap 的机器上主动丢
#     page cache 会造成读写风暴，**反而推高 OOM 概率**（排期 1.7）
#   · `ps aux >> /var/log/nox-monitor.log` —— 命令行里带着主 token，
#     写进一个世界可读的文件（审计 High）
#   · 自己 `systemctl restart` —— 和 systemd 的 `Restart=always` 抢着重启，
#     等于每 5 分钟给一个正在退避的服务补一刀
#
# ## 这个脚本的三条规矩
#
# 1. **不修东西，只看和喊。** 重启交给 systemd（策略见
#    `scratch/apply-restart-policy.sh`）。两个东西抢着重启只会更乱。
# 2. **喊到她能看见的地方。** 走 bridge 的推送口 → 她手机锁屏。
#    写进日志文件那种"告警"，这个项目已经证明过没人读。
# 3. **不吵。** 同一个问题最多 6 小时提醒一次；要连着坏两次才算数
#    （一次部署的重启只有几秒，不该惊动她）。
#
# ## ⚠️ 它看不见的那件事
#
# **bridge 自己挂了的话，推不出去** —— 通道就是它。
# 那种情况只会留在 journald 里。可接受：bridge 挂了她的 App 直接打不开，
# 她自己会发现。真正危险的是「bridge 活着、他挂了」——
# 那时候 App 一切正常，只是他不说话了。这个脚本治的正是这一种。
#
# 用法：
#   bash caelum-watch.sh          正常跑（给 systemd timer 用）
#   bash caelum-watch.sh --dry    只检查不推送，把要推的内容打出来
# ════════════════════════════════════════════════════════════════
set -u

DRY=0
[ "${1:-}" = "--dry" ] && DRY=1

DOCTOR=${DOCTOR:-/root/doctor.sh}
BRIDGE=${BRIDGE:-http://127.0.0.1:3003}
BRIDGE_ENV=${BRIDGE_ENV:-/etc/nox/bridge.env}
STATE_DIR=${STATE_DIR:-/var/lib/caelum-watch}

#: 连着坏几次才推。5 分钟一轮 → 2 次 ≈ 持续 5 分钟。
#: 为什么不是 1：部署会重启服务，那几秒不该把她叫起来。
FLAP_COUNT=${FLAP_COUNT:-2}
#: 同一个问题的提醒间隔（秒）。6 小时 —— 够久到不烦人，
#: 又不至于她睡一觉起来完全没被提醒过。
COOLDOWN=${COOLDOWN:-21600}

mkdir -p "$STATE_DIR"
STREAK_F="$STATE_DIR/streak"
SIG_F="$STATE_DIR/last-sig"
SENT_F="$STATE_DIR/last-sent"

# 日志直接 echo —— 作为 systemd service 跑时 stdout 就进 journald，
# 带着 unit 名字，`journalctl -u caelum-watch` 能查。
# **不再写私有日志文件**：这个项目已经证明过那种文件没人读。
log() { echo "$(date '+%F %T') $*"; }

PROBLEMS=()
add() { PROBLEMS+=("$1"); }

# ── 1) 服务 ────────────────────────────────────────────────────
# 🔴 清单向 doctor.sh 要，**不自己抄一份**。
#    原来两边各抄各的，monitor 那份漏了 nox-core —— 也就是说
#    "他本人挂了"这件事，监控从来看不见。
if [ -x "$DOCTOR" ] || [ -f "$DOCTOR" ]; then
  SERVICES=$(bash "$DOCTOR" --list-services 2>/dev/null)
else
  SERVICES=""
fi
if [ -z "$SERVICES" ]; then
  # 取不到清单就明说，不要默默用一份硬编码的 —— 那正是走散的起点
  add "拿不到服务清单（$DOCTOR 不在或不支持 --list-services），这一轮只查了系统资源"
else
  for s in $SERVICES; do
    st=$(systemctl is-active "$s" 2>/dev/null)
    case "$st" in
      active) ;;
      activating|deactivating)
        # 正在起/正在停：这一轮先不算问题，下一轮还这样才算
        log "note: $s = $st（过渡态，这轮不计）" ;;
      *)
        # failed 和 inactive 要分开说：前者是它自己放弃了（重启限流触发，
        # 见 apply-restart-policy.sh），后者是没人启动它。修法不一样。
        if [ "$(systemctl is-failed "$s" 2>/dev/null)" = "failed" ]; then
          add "$s 起不来了（试了多次后放弃）"
        else
          add "$s 没在跑（$st）"
        fi ;;
    esac
  done
fi

# ── 2) 探活 ────────────────────────────────────────────────────
# bridge 的 /api/health 是全家桶聚合口，一次拿到所有后端的死活。
H=$(curl -s -m 20 --noproxy '*' "$BRIDGE/api/health" 2>/dev/null)
if [ -z "$H" ]; then
  add "bridge 的 /api/health 没有响应"
elif ! echo "$H" | grep -q '"status":"ok"'; then
  # 把具体哪一项坏了摘出来，别只说"不健康" ——
  # 一条她看不懂下一步该干嘛的告警，等于没有告警（见 docs/LOGGING.md 规则 4）
  BADS=$(echo "$H" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("解析不了"); sys.exit()
print(", ".join(k for k, v in (d.get("checks") or {}).items() if not v.get("ok")) or "说不清是哪一项")
' 2>/dev/null)
  add "探活不通过：$BADS"
fi

# ── 3) 系统资源 ────────────────────────────────────────────────
# ⚠️ 只看不动手。旧脚本在这里 drop_caches —— 4GB 无 swap 的机器上
#    那么干会造成读写风暴，**把 OOM 概率推得更高**（排期 1.7）。
DISK=$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')
[ "${DISK:-0}" -ge 90 ] && add "磁盘用到 ${DISK}% 了"
MEM=$(free -m | awk '/Mem:/ {print $7}')
[ "${MEM:-9999}" -lt 150 ] && add "可用内存只剩 ${MEM}MB（这台机器没有交换区，再少就要 OOM）"

# ── 4) 决定要不要吵她 ──────────────────────────────────────────
PREV_SIG=$(cat "$SIG_F" 2>/dev/null || echo "")
STREAK=$(cat "$STREAK_F" 2>/dev/null || echo 0)
NOW=$(date +%s)

push() {  # $1=标题 $2=正文
  if [ "$DRY" = "1" ]; then
    log "DRY: 会推 → [$1] $2"
    return 0
  fi

  # 🔴 **token 绝不进命令行。**
  #    审计点名过 `morning-check.sh:200`：`curl -H "X-Nox-Token: $TOKEN"`
  #    —— 本机随便一个 `ps aux` 就能看到主令牌。
  #    这里把 header 写进一份 600 的临时配置，`curl -K` 读它，argv 上干干净净。
  local tok
  tok=$(grep -m1 '^NOX_TOKEN=' "$BRIDGE_ENV" 2>/dev/null | cut -d= -f2- | tr -d "\"'")
  if [ -z "$tok" ]; then
    log "🔴 $BRIDGE_ENV 里读不到 NOX_TOKEN，推不出去"
    return 1
  fi

  # ⚠️ 用 python3 拼 JSON 是因为正文里全是中文和标点，手搓转义迟早出事。
  #    但它必须**在场检查**：不检查的话，python3 缺席时 body 会是空文件，
  #    curl 照发、服务端回 400，而我们只会看到一行"推送失败"，
  #    根本想不到是缺个解释器。（2026-09-13 在本机测试时就栽了一次。）
  command -v python3 >/dev/null 2>&1 || {
    log "🔴 没有 python3，拼不出推送内容 —— 装一个，或者改掉这一段"
    return 1
  }

  local cfg body code
  cfg=$(umask 077; mktemp)
  body=$(umask 077; mktemp)
  # shellcheck disable=SC2064
  trap "rm -f '$cfg' '$body'" RETURN

  # ensure_ascii=False —— 正文全是中文，转义成 \uXXXX 之后
  # 日志和抓包里都成了天书，出事时最需要读的就是这句话。
  # 直接写 bytes，不受这台机器 stdout 编码的影响。
  python3 -c '
import json, sys
sys.stdout.buffer.write(
    json.dumps({"title": sys.argv[1], "body": sys.argv[2]}, ensure_ascii=False).encode("utf-8")
)
' "$1" "$2" > "$body"

  # 空 body 一定是上面那句没跑成。宁可这里就停，也不要发一条空推送 ——
  # 她看到一条没有内容的通知，比没收到通知更让人慌
  [ -s "$body" ] || { log "🔴 推送内容是空的，不发了"; return 1; }

  {
    echo "url = \"$BRIDGE/api/push/send\""
    echo "header = \"X-Nox-Token: $tok\""
    echo "header = \"Content-Type: application/json\""
    echo "data = @$body"
    echo "silent"
    echo "output = /dev/null"
    echo "write-out = \"%{http_code}\""
  } > "$cfg"

  code=$(curl -K "$cfg" -m 20 --noproxy '*' 2>/dev/null)
  if [ "$code" = "200" ]; then
    log "已推送：$2"
    return 0
  fi
  log "🔴 推送失败（HTTP ${code:-无响应}）—— 这一轮不记账，下一轮还会再试"
  return 1
}

if [ "${#PROBLEMS[@]}" -eq 0 ]; then
  echo 0 > "$STREAK_F"
  if [ -n "$PREV_SIG" ]; then
    # 从坏到好：报一次平安，把这条线索闭环。
    # 只有真推过告警才报恢复 —— 否则她会收到一条没头没尾的"恢复了"。
    if [ -f "$SENT_F" ]; then
      push "Caelum 恢复了" "刚才那个问题没有了，现在全部正常。" && rm -f "$SENT_F"
    fi
    : > "$SIG_F"
  fi
  log "全部正常"
  exit 0
fi

# 有问题
log "发现 ${#PROBLEMS[@]} 个问题："
for p in "${PROBLEMS[@]}"; do log "  - $p"; done

# 问题集合的指纹。**排序之后再算** —— 服务清单的顺序不该让同一组问题
# 看起来像是新的一组，那会把冷却绕过去、变成每 5 分钟吵一次。
SIG=$(printf '%s\n' "${PROBLEMS[@]}" | sort | md5sum | cut -c1-12)

if [ "$SIG" = "$PREV_SIG" ]; then
  STREAK=$((STREAK + 1))
else
  STREAK=1
  echo "$SIG" > "$SIG_F"
fi
echo "$STREAK" > "$STREAK_F"

if [ "$STREAK" -lt "$FLAP_COUNT" ]; then
  log "第 $STREAK 次（要连着 $FLAP_COUNT 次才提醒，部署时的几秒重启不算）"
  exit 1
fi

LAST_SENT=0
[ -f "$SENT_F" ] && LAST_SENT=$(cat "$SENT_F" 2>/dev/null || echo 0)
LAST_SIG=$(cat "$STATE_DIR/last-sent-sig" 2>/dev/null || echo "")
if [ "$SIG" = "$LAST_SIG" ] && [ $((NOW - LAST_SENT)) -lt "$COOLDOWN" ]; then
  log "同一个问题 $(( (NOW - LAST_SENT) / 60 )) 分钟前提醒过了，先不重复（冷却 $((COOLDOWN/3600)) 小时）"
  exit 1
fi

# 正文写成她能直接看懂的一句话 + 具体项
BODY=$(printf '%s；' "${PROBLEMS[@]}")
BODY="${BODY%；}"
if push "Caelum 有点不对劲" "$BODY"; then
  echo "$NOW" > "$SENT_F"
  echo "$SIG"  > "$STATE_DIR/last-sent-sig"
fi
exit 1
