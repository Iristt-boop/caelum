#!/usr/bin/env bash
# ============================================================
# deploy-moments-body.sh —— 把 173e451（concern 不进 inner / evidence 进提示词 /
# shadow 真生成正文）送上线，并**真验它上线了**。
#
# 2026-09-21 立。为什么要一个专门的脚本：
# 这次的故障形状就是「汇总脚本在找 `body=`，线上代码不写 `body=`」——
# 报告不报错、退出码 0、看着一切正常，只是那一段永远空着。
# 所以这个脚本的判据不是「systemctl 说 active」，而是
# **线上那个文件里真有 `body=%s`** + **重启之后的 tick 真带 body=**。
#
# 🔴 2026-09-21 第一版在这儿栽了一次，教训写死在这里：
#   它 `journalctl --since '-25 min' | tail -1` 取「最近一条 tick」，
#   而重启后下一个 tick 要等 15 分钟 —— 于是第一轮就抓到**重启之前**那条
#   旧代码的 tick，当场判「跑着的还是旧代码」。
#   部署其实可能是成的，判据取错了窗口。
#   现在窗口一律从 **systemd 的 ActiveEnterTimestamp** 起算，
#   重启前的日志一条都不看。
#
# 用法：
#   bash scripts/deploy-moments-body.sh          # 部署 + 重启 + 验文件
#   bash scripts/deploy-moments-body.sh --wait   # 再等重启后的第一条 tick
#   bash scripts/deploy-moments-body.sh --check  # 什么都不改，只复查线上现状
#
# 退出码：
#   0  上线并验到了
#   1  部署/重启失败
#   2  线上文件里没有 body=%s —— 传上去的不是新代码（或传进了野目录）
#   3  重启后的 tick **没带** body= —— 跑着的确实还是旧代码
#   4  重启后还没出现任何 tick（**不是失败**，等下一个 15 分钟再 --check）
# ============================================================
set -uo pipefail

HOST="noxtang.com"            # 🔴 认名字不认 IP（2026-09-15 踩过一次）
KEY="$HOME/.ssh/id_ed25519"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="nox-core"
REMOTE_DIR="/root/nox-core/code"

FILES=(moments/impulse.py moments/loop.py moments/record.py moments/writer.py)

MODE="${1:-}"

ssh_vps() { ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "root@${HOST}" "$@"; }
say() { printf '%s\n' "$*"; }

# ---------------------------------------------------------------- 服务起来的那一刻
# journalctl 的窗口一律从这里起算 —— 「重启前的 tick」和「重启后的 tick」
# 混在一起看，就是第一版栽的那个坑。
restart_moment() {
  ssh_vps "systemctl show -p ActiveEnterTimestamp --value $UNIT" 2>/dev/null \
    | awk '{print $2" "$3}'
}

# 重启之后的 Moments tick 里，带 body= 的有几条 / 一共几条
# 输出两个数：`<带body的条数> <总条数>`
ticks_since() {  # $1 = "YYYY-mm-dd HH:MM:SS"
  ssh_vps "journalctl -u $UNIT --since '$1' --no-pager -o cat 2>/dev/null \
           | grep 'Moments｜' \
           | awk '{ n++ } /｜body=/ { b++ } END { print (b?b:0), (n?n:0) }'"
}

# 跑不通时把「到底哪份代码在跑」摊开 —— 光说「还是旧代码」没法排障
diagnose() {
  say ""
  say "── 到底哪份代码在跑 ──"
  ssh_vps "
    echo '  code 解析到： '\$(readlink -f $REMOTE_DIR)
    echo '  ExecStart：   '\$(systemctl show -p ExecStart --value $UNIT | head -c 200)
    pid=\$(systemctl show -p MainPID --value $UNIT)
    echo '  主进程 cwd：  '\$(readlink -f /proc/\$pid/cwd 2>/dev/null || echo '(读不到)')
    echo '  它加载的 moments 包：'
    ls -l /proc/\$pid/cwd/moments/record.py 2>/dev/null || echo '    (cwd 下没有 moments/record.py)'
    echo -n '  cwd 里 record.py 的 body=%s 次数：'
    grep -c 'body=%s' /proc/\$pid/cwd/moments/record.py 2>/dev/null || echo 0
    echo '  残留字节码（会让旧逻辑继续跑）：'
    find $REMOTE_DIR/moments -name '*.pyc' -newer $REMOTE_DIR/moments/record.py 2>/dev/null | head -5 || true
  "
}

# ================================================================ --check
# 什么都不改，只回答「线上现在跑的是哪版」。
# 部署完等 15 分钟再跑这个，比重新部署一遍安全得多。
if [ "$MODE" = "--check" ]; then
  say "── 复查（不部署、不重启）──"
  remote_hit=$(ssh_vps "grep -c 'body=%s' $REMOTE_DIR/moments/record.py 2>/dev/null || echo 0")
  say "  线上 record.py 里 'body=%s'：${remote_hit} 次"
  since=$(restart_moment)
  say "  $UNIT 起来的时间：${since:-(读不到)}"
  [ -z "$since" ] && { say "🔴 读不到 ActiveEnterTimestamp，没法划窗口"; exit 1; }

  read -r withbody total <<< "$(ticks_since "$since")"
  say "  重启后的 Moments tick：${total:-0} 条，其中带 body= 的 ${withbody:-0} 条"
  say ""
  if [ "${total:-0}" -eq 0 ]; then
    say "⏳ 重启后还没出现 tick（一天 96 个 ≈ 每 15 分钟一条）。"
    say "   **这不是失败** —— 过 15 分钟再跑一次 --check。"
    exit 4
  fi
  if [ "${withbody:-0}" -eq "$total" ]; then
    say "✅ 重启后 $total 条 tick **全部**带 body= —— 新代码在跑。"
    say "   （骰子没中的 tick 里 body 是 '-'，那是对的：没走到生成那一步就没有正文）"
    exit 0
  fi
  say "🔴 重启后 $total 条 tick 里只有 ${withbody:-0} 条带 body= —— 跑着的还是旧代码。"
  diagnose
  exit 3
fi

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
# 🔴 这一步的重点：2026-09-19 bridge 就是被 scp 传进了野目录，
# 服务根本不读那份，而部署脚本一切正常。所以要回读线上那个文件。
say "── 验线上文件里真有 body=（不是「传完了」，是「读得到」）──"
remote_hit=$(ssh_vps "grep -c 'body=%s' $REMOTE_DIR/moments/record.py 2>/dev/null || echo 0")
say "  线上 record.py 里 'body=%s' 出现 ${remote_hit} 次"
if [ "${remote_hit:-0}" -lt 1 ]; then
  say "🔴 线上那份 record.py 里没有 body=%s —— 传上去的不是新代码，或者传进了野目录。"
  say "   去核对 deploy-vps.sh 的 service_dir 映射（$REMOTE_DIR 是 symlink → release）"
  exit 2
fi
say ""

# ---------------------------------------------------------------- 3. 重启
# 🔴 顺手清掉残留字节码：源码换了而 .pyc 更新，Python 会继续跑旧的
# （memory: stale-pyc-poisons-mutation-tests 是同一个病在本地的版本）
say "── 重启 $UNIT（先清 moments 的 __pycache__）──"
ssh_vps "find $REMOTE_DIR/moments -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; systemctl restart $UNIT" \
  || { say "🔴 重启失败"; exit 1; }
sleep 6
active=$(ssh_vps "systemctl is-active $UNIT" 2>&1)
say "  systemctl is-active → $active"
[ "$active" != "active" ] && { say "🔴 起不来了，去看 journalctl -u $UNIT -n 50"; exit 1; }

since=$(restart_moment)
say "  起来的时间：${since:-(读不到)}"
say ""

# ---------------------------------------------------------------- 4. 等重启后的第一条 tick
# `systemctl is-active` 只证明进程活着 —— 而这次要治的毛病恰恰是
# 「进程活着、日志在打、而那件事没发生」。所以要等一条**重启之后**的记录。
if [ "$MODE" != "--wait" ]; then
  say "✅ 上线了，线上文件已回读验过。"
  say "   一天 96 个 tick ≈ 每 15 分钟一条。过 15 分钟跑这个复查（不会再动线上）："
  say "     bash scripts/deploy-moments-body.sh --check"
  exit 0
fi

[ -z "$since" ] && { say "🔴 读不到 ActiveEnterTimestamp，没法划窗口"; exit 1; }

say "── 等重启后的第一条 tick（最多 20 分钟；重启前的日志一条都不看）──"
for i in $(seq 1 20); do
  read -r withbody total <<< "$(ticks_since "$since")"
  if [ "${total:-0}" -gt 0 ]; then
    say "  重启后出现 ${total} 条 tick，其中带 body= 的 ${withbody:-0} 条"
    if [ "${withbody:-0}" -eq "$total" ]; then
      say ""
      say "✅ 日志里真带 body= 了 —— 「他会写什么」从这一刻起看得到。"
      say "   （骰子没中的 tick 里 body 是 '-'，那是对的）"
      exit 0
    fi
    say "🔴 重启后的 tick **没带** body= —— 跑着的确实还是旧代码。"
    diagnose
    exit 3
  fi
  say "  第 $i 分钟：重启后还没有新 tick，继续等"
  sleep 60
done

say "⏳ 20 分钟没等到重启后的第一条 tick。"
say "   循环可能没在跑：curl -s localhost:8000/health | python3 -m json.tool | grep -A3 post_tick"
exit 4
