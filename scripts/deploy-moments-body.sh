#!/usr/bin/env bash
# ============================================================
# deploy-moments-body.sh —— 把 173e451（concern 不进 inner / evidence 进提示词 /
# shadow 真生成正文）送上线，并**真验它上线了**。
#
# 2026-09-21 立。为什么要一个专门的脚本：
# 这次的故障形状就是「汇总脚本在找 `body=`，线上代码不写 `body=`」——
# 报告不报错、退出码 0、看着一切正常，只是那一段永远空着。
# 所以这个脚本的判据不是「systemctl 说 active」，而是
# **线上那个文件里真有 `body=%s`** + **journalctl 真吐出一条带 body= 的 tick**。
#
# 用法：
#   bash scripts/deploy-moments-body.sh          # 部署 + 重启 + 验文件
#   bash scripts/deploy-moments-body.sh --wait   # 再多等一个 tick，验日志真带 body=
#
# 退出码：
#   0  上线并验到了
#   1  部署/重启失败
#   2  线上文件里没有 body=%s —— 传上去的不是新代码（或传进了野目录）
#   3  等满了也没看到带 body= 的 tick（--wait 才可能）
# ============================================================
set -uo pipefail

HOST="noxtang.com"            # 🔴 认名字不认 IP（2026-09-15 踩过一次）
KEY="$HOME/.ssh/id_ed25519"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="nox-core"

FILES=(moments/impulse.py moments/loop.py moments/record.py moments/writer.py)

ssh_vps() { ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "root@${HOST}" "$@"; }

say() { printf '%s\n' "$*"; }

# ---------------------------------------------------------------- 0. 先看清本地
say "── 本地这四个文件的指纹 ──"
for rel in "${FILES[@]}"; do
  sha=$(git -C "$REPO/nox-core" log -1 --format=%h -- "$rel" 2>/dev/null || echo unknown)
  dirty=$(git -C "$REPO/nox-core" status --porcelain -- "$rel" 2>/dev/null)
  say "  $rel  ${sha}${dirty:+  ⚠️ dirty（提交后又改过，别这么部署）}"
done
say ""

# ---------------------------------------------------------------- 1. 部署
say "── 部署（走 deploy-vps.sh，指纹会记到 /root/DEPLOY_INFO）──"
bash "$REPO/scripts/deploy-vps.sh" deploy "$UNIT" "${FILES[@]}"
rc=$?
[ $rc -ne 0 ] && { say "🔴 部署失败（退出码 $rc），没重启，线上还是旧的"; exit 1; }
say ""

# ---------------------------------------------------------------- 2. 验文件真到了
# 🔴 这一步是整个脚本的重点：2026-09-19 bridge 就是被 scp 传进了野目录，
# 服务根本不读那份，而部署脚本一切正常。所以要回读线上那个文件。
say "── 验线上文件里真有 body=（不是「传完了」，是「读得到」）──"
remote_hit=$(ssh_vps "grep -c 'body=%s' /root/nox-core/code/moments/record.py 2>/dev/null || echo 0")
say "  线上 record.py 里 'body=%s' 出现 ${remote_hit} 次"
if [ "${remote_hit:-0}" -lt 1 ]; then
  say "🔴 线上那份 record.py 里没有 body=%s —— 传上去的不是新代码，或者传进了野目录。"
  say "   去核对 deploy-vps.sh 的 service_dir 映射（/root/nox-core/code 是 symlink → release）"
  exit 2
fi
say ""

# ---------------------------------------------------------------- 3. 重启
say "── 重启 $UNIT ──"
ssh_vps "systemctl restart $UNIT" || { say "🔴 重启失败"; exit 1; }
sleep 6
active=$(ssh_vps "systemctl is-active $UNIT" 2>&1)
say "  systemctl is-active → $active"
[ "$active" != "active" ] && { say "🔴 起不来了，去看 journalctl -u $UNIT -n 50"; exit 1; }
say ""

# ---------------------------------------------------------------- 4. 等一个真 tick
# `systemctl is-active` 只证明进程活着 —— 而这次要治的毛病恰恰是
# 「进程活着、日志在打、而那件事没发生」。所以要等一条真的 Moments 记录。
if [ "${1:-}" != "--wait" ]; then
  say "✅ 上线了，线上文件已验。"
  say "   一天 96 个 tick ≈ 每 15 分钟一条。想当场看到带正文的那一条，重跑一次带 --wait；"
  say "   或者过 15 分钟跑 scripts/moments-shadow-pull.ps1，「正文」那一段就该有东西了。"
  exit 0
fi

say "── 等一条真的 Moments tick（最多 20 分钟，每 60 秒看一次）──"
for i in $(seq 1 20); do
  line=$(ssh_vps "journalctl -u $UNIT --since '-25 min' --no-pager -o cat 2>/dev/null | grep 'Moments｜' | tail -1")
  if [ -n "$line" ]; then
    say "  抓到一条 tick："
    say "    ${line:0:300}"
    case "$line" in
      *"｜body="*)
        say ""
        say "✅ 日志里真带 body= 了 —— 「他会写什么」从这一刻起看得到。"
        say "   （骰子没中的 tick 里 body 是 '-'，那是对的：没走到生成那一步就没有正文）"
        exit 0
        ;;
      *)
        say "🔴 抓到 tick 了，但这一行**没有** body= —— 跑着的还是旧代码。"
        say "   重启可能没真换掉进程，或者 release symlink 指着别的目录。"
        exit 3
        ;;
    esac
  fi
  say "  第 $i 分钟：还没有新 tick，继续等"
  sleep 60
done

say "🔴 20 分钟没等到任何一条 Moments tick —— 循环可能根本没在跑。"
say "   去看：curl -s localhost:8000/health | python3 -m json.tool | grep -A3 post_tick"
exit 3
