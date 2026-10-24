#!/usr/bin/env bash
# 网易云 cookie 探活 —— 死了推她扫码（2026-10-24，她拍板「1和2可以做一下」的 1）
#
# ════════════════════════════════════════════════════════════════
# ## 背景
#
# MUSIC_U 7~30 天就死、官方不回过期时间戳、没有第三方续期（10-24 实锤：
# 09-23 换的 cookie 满月失效，account/get 回 account:null）。以前要等她
# 发现点歌哑了才知道。现在配着 co-listening 的 /music/qr/* 扫码页：
# 死了推一条带签名链接的消息，她点开扫一下就续上。
#
# ## 规矩（照 caelum-watch 的三条）
#
# 1. 只看和喊，不修东西（扫码也只能她来）。
# 2. 喊到她能看见的地方：走 bridge 推送 → 手机锁屏。
# 3. 不吵：死了推一条就记账（FLAG），不复读；活了推一次平安闭环。
#    -460 风控文案另说，不催扫码（那是 like_song 同款风控，扫码救不了）。
#
# 用法：
#   cookie-watch.sh          正常跑（cron 每 6 小时）
#   cookie-watch.sh --dry    只探不推，打出会推什么
#   cookie-watch.sh --state  只探，打一行 alive|dead|risk|error:…（doctor 用）
# ════════════════════════════════════════════════════════════════
set -u

DRY=0; STATE_ONLY=0
case "${1:-}" in
  --dry) DRY=1 ;;
  --state) STATE_ONLY=1 ;;
esac

CRED=/root/co-listening/.netease_cred
BRIDGE=${BRIDGE:-http://127.0.0.1:3003}
BRIDGE_ENV=${BRIDGE_ENV:-/etc/nox/bridge.env}
QR_PAGE=${QR_PAGE:-https://noxtang.com/api/music/proxy/music/qr/page}
FLAG=/tmp/cookie-watch.nagged   # 抱过丧了，别复读
TAG="[cookie-watch]"

log() { echo "$(date '+%m-%d %H:%M:%S') $TAG $*"; }

# ── 1) 探活：真打 account/get，不是看文件几岁 ──────────────────────
probe() {
  if [ ! -f "$CRED" ]; then
    echo "dead:凭据文件不存在"
    return
  fi
  python3 - "$CRED" <<'PY' 2>/dev/null || echo "error:探活脚本没跑起来"
import json, sys, urllib.request
parts = []
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        parts.append(line)
req = urllib.request.Request(
    "https://music.163.com/api/nuser/account/get",
    headers={"User-Agent": "Mozilla/5.0",
             "Referer": "https://music.163.com/",
             "Cookie": "; ".join(parts)})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.load(r)
except Exception as e:
    print("error:" + str(e)[:80]); raise SystemExit
msg = str(d.get("message") or d.get("error") or "")
if d.get("profile"):
    print("alive")
elif "风险" in msg or str(d.get("code")) in ("460", "-460"):
    print("risk:" + msg[:60])
elif d.get("code") == 200:
    print("dead")  # 200 + profile null = 登录态没了（10-24 实测，不带 cookie 也长这样）
else:
    print("error:" + msg[:80])
PY
}

VERDICT=$(probe)

[ "$STATE_ONLY" = 1 ] && { echo "$VERDICT"; exit 0; }

# ── 2) 推送（照 caelum-watch：token 绝不进 argv，curl -K 读配置）────
push() {  # $1=标题 $2=正文
  if [ "$DRY" = 1 ]; then
    log "DRY: 会推 → [$1] $2"
    return 0
  fi
  command -v python3 >/dev/null 2>&1 || { log "🔴 没有 python3，拼不出推送内容"; return 1; }
  local tok
  tok=$(grep -m1 '^NOX_TOKEN=' "$BRIDGE_ENV" 2>/dev/null | cut -d= -f2- | tr -d "\"'")
  [ -z "$tok" ] && { log "🔴 $BRIDGE_ENV 里读不到 NOX_TOKEN，推不出去"; return 1; }
  local cfg body code
  cfg=$(umask 077; mktemp)
  body=$(umask 077; mktemp)
  trap "rm -f '$cfg' '$body'" RETURN
  python3 -c '
import json, sys
sys.stdout.buffer.write(
    json.dumps({"title": sys.argv[1], "body": sys.argv[2]}, ensure_ascii=False).encode("utf-8")
)' "$1" "$2" > "$body"
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
  if [ "$code" = 200 ]; then
    log "已推送：$1"
    return 0
  fi
  log "🔴 推送失败（HTTP ${code:-无响应}）—— 这一轮不记账，下一轮还会再试"
  return 1
}

qr_link() {
  python3 - "$BRIDGE_ENV" "$QR_PAGE" <<'PY' 2>/dev/null
import hmac, hashlib, sys, time
tok = ""
for line in open(sys.argv[1]):
    if line.startswith("NOX_TOKEN="):
        tok = line.split("=", 1)[1].strip().strip("\"'")
if not tok:
    raise SystemExit(1)
exp = int(time.time() * 1000) + 7 * 24 * 3600 * 1000
sig = hmac.new(tok.encode(), f"music:qr-login:{exp}".encode(),
               hashlib.sha256).hexdigest()[:24]
print(f"{sys.argv[2]}?id=qr-login&exp={exp}&sig={sig}")
PY
}

# ── 3) 决定说不说 ──────────────────────────────────────────────────
case "$VERDICT" in
  alive)
    log "登录活着"
    if [ -f "$FLAG" ]; then
      rc=0; push "音乐续上了" "网易云登录活了，点歌恢复正常。" || rc=1
      [ "$DRY" = 0 ] && [ "$rc" = 0 ] && rm -f "$FLAG"
    fi
    ;;
  dead*)
    DETAIL=${VERDICT#dead}
    if [ -f "$FLAG" ]; then
      log "还是死的（$DETAIL），已经提醒过了，不复读"
      exit 0
    fi
    LINK=$(qr_link)
    if [ -z "$LINK" ]; then
      log "🔴 签名链接算不出来（$BRIDGE_ENV 里没 NOX_TOKEN？），只报不给链接"
      push "网易云登录过期了" "点歌会哑。服务器上跑 /root/cookie-watch.sh 修一下签名链接。"
      exit 0
    fi
    # --dry 不许留副作用：记账只在真推成功后落（dry 复读会吞掉真推送）
    rc=0
    push "该给 Nox 续音乐登录了" "网易云 cookie 过期了（它 7~30 天就失效一次，没法自动续）。点开扫一下就好，扫完我自己会闭嘴：
$LINK" || rc=1
    [ "$DRY" = 0 ] && [ "$rc" = 0 ] && : > "$FLAG"
    ;;
  risk*)
    log "网易云风控文案（${VERDICT#risk}）—— 不是过期，扫码救不了，不推"
    ;;
  *)
    log "探活没结论：$VERDICT —— 网络抖动就先不动，连着坏会有别的看门狗吠"
    ;;
esac
