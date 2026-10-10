#!/usr/bin/env bash
# ============================================================
# n150-cutover.sh —— 东京 → N150 切换日，N150 这一侧（2026-10-10）
#
# 前提：东京的 Caelum 服务已停、已现打备份、包已传到这台机器。
# 用法：n150-cutover.sh <caelum-XXXX.tar.gz.enc>
#
# 做的事：停 → caelum-restore.sh → 撤安静模式 → 按东京的 enabled 集合起全套
#        → 装 crontab → 从使用方验「他醒着、会主动开口」。
# DNS 翻转不在这里（要 Cloudflare 授权，在 cloudflared 那步单独做）。
# ============================================================
set -euo pipefail
PKG="${1:?缺备份包路径}"

# 东京 10-10 的 enabled 集合（eryu / netease-mcp / mcp-train 东京是 disabled，不起）
SVCS="ombre-brain nox-core bridge co-reading co-watching co-listening ha-mcp health-mcp health-sync
      toy-mcp touch-mcp touch-server app-tracker mcp-trends stackchan-gateway stackchan-tracker"
TIMERS="caelum-retention.timer ombre-brain-consolidate.timer ombre-brain-decay.timer nox-daily.timer"

echo "== 1) 停"
systemctl stop $TIMERS $SVCS 2>/dev/null || true

echo "== 2) 从包恢复"
/root/caelum-restore.sh "$PKG" /root/.backup-pass

echo "== 3) 撤安静模式"
rm -f /etc/systemd/system/nox-core.service.d/zz-n150-quiet.conf /etc/nox/n150-quiet.env
systemctl daemon-reload

echo "== 4) 起全套 + 开机自启"
systemctl enable $SVCS $TIMERS >/dev/null 2>&1
systemctl start $SVCS
systemctl start $TIMERS

echo "== 5) crontab（去掉腾讯云 stargate 那条）"
grep -v stargate /root/restored-config/crontab.root | crontab -
crontab -l | sed 's/^/   /'

echo "== 6) 验：从使用方看，不看配置"
sleep 30
bad=0
for s in $SVCS; do
  [ "$(systemctl is-active "$s")" = active ] || { echo "NOT ACTIVE: $s"; bad=1; }
done
for t in $TIMERS; do
  [ "$(systemctl is-active "$t")" = active ] || { echo "TIMER NOT ACTIVE: $t"; bad=1; }
done
curl -s --noproxy '*' localhost:8100/health | python3 -c '
import json, sys
d = json.load(sys.stdin)
a = d.get("attention")
ok = a is not None and a.get("dry_run") is False
print("attention:", "LIVE" if ok else a, "| background:", sorted(d.get("background", {})))
sys.exit(0 if ok else 1)
' || { echo "BAD: 他没醒（attention 不是 live）"; bad=1; }
n=$(sqlite3 /root/data/nox-bridge.db "select count(*) from push_subs")
echo "推送订阅: $n 个"
[ "$n" -gt 0 ] || { echo "BAD: 推送订阅是空的 —— 他主动找她发不出去"; bad=1; }
[ "$bad" -eq 0 ] && echo "N150 侧切换完成" || { echo "N150 侧有问题，见上"; exit 1; }
