#!/usr/bin/env bash
# 重启策略收口（排期 1.6）。在 VPS 上跑：bash /tmp/apply-restart-policy.sh
#
# ## 现状（2026-09-13 实测）
#
#   服务         Restart   RestartSec  Burst  Interval
#   caddy        no        —           5      10s        ← 🔴 挂了就一直挂
#   其余 12 个   always    3~5s        5      10s        ← 🔴 限流永远触发不到
#
# **限流为什么触发不到**：要「10 秒内启动 5 次」才算超限，而重启间隔是 3~5 秒，
# 10 秒里最多启 2~3 次。于是一个起不来的服务会**以 5 秒一次的节奏无限重启**，
# 烧 CPU、刷满日志，而且没有任何人知道。
#
# **caddy 为什么更糟**：它压根没写 Restart=，systemd 默认 no。
# 它是整套系统**唯一的入口** —— 它一挂，App / MCP / 所有域名全挂，
# 而且**不会自己起来**，要等人发现。而在今天之前，没有任何东西在看着。
#
# ## 改成什么
#
#   [Service] RestartSec=10             —— 慢一点，别刷
#   [Unit]    StartLimitIntervalSec=300
#             StartLimitBurst=10        —— 5 分钟内 10 次起不来就放弃，标记 failed
#   caddy 另加 [Service] Restart=on-failure
#
# 10 次 × 10 秒 = 100 秒，远小于 300 秒窗口 —— **所以这次限流是真的会触发**。
# 偶发抖动（上游没就绪、端口还没释放）在这 10 次里能恢复；
# 真坏了就停下来，由 caelum-watch.sh 推到她手机。
#
# ⚠️ **`StartLimitIntervalSec` / `StartLimitBurst` 必须写在 `[Unit]` 里。**
#    写进 `[Service]` 是老写法，新版 systemd **静默忽略** ——
#    那会得到一个"看起来配了、其实没配"的结果，正是这份排期一直在防的事。
#    所以最后一步一定要用 `systemctl show` 回读验证，不能只看文件写没写。
set -u

STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="/root/dropin-backup-$STAMP"
mkdir -p "$BACKUP"

SERVICES="bridge nox-core ombre-brain co-reading co-watching eryu netease-mcp touch-server touch-mcp ha-mcp toy-mcp app-tracker"

echo "── 备份到 $BACKUP ──"
for s in $SERVICES caddy; do
  d="/etc/systemd/system/$s.service.d"
  [ -d "$d" ] && cp -r "$d" "$BACKUP/$s.service.d" 2>/dev/null
done

write_dropin() {
  local svc="$1" extra="$2"
  local dir="/etc/systemd/system/$svc.service.d"
  mkdir -p "$dir"
  cat > "$dir/restart-policy.conf" <<EOF
# 由 scratch/apply-restart-policy.sh 生成（排期 1.6，$STAMP）
# 见那个脚本开头的说明。改这里之前先读它。
[Unit]
# 🔴 这两条必须在 [Unit]。写进 [Service] 会被静默忽略。
StartLimitIntervalSec=300
StartLimitBurst=10

[Service]
RestartSec=10
$extra
EOF
}

echo "── 写 drop-in ──"
for s in $SERVICES; do
  systemctl cat "$s" >/dev/null 2>&1 || { echo "  跳过 $s（没这个 unit）"; continue; }
  write_dropin "$s" ""
  echo "  $s"
done

# caddy 单独：它原来根本没有 Restart=
# on-failure 而不是 always —— 配置写错时 caddy 会主动退出，
# 那种情况该停下来让人看见，不该假装还活着。
write_dropin caddy "Restart=on-failure"
echo "  caddy（另加 Restart=on-failure）"

systemctl daemon-reload
echo "── daemon-reload 完成 ──"

echo
echo "── 回读验证（这一步才是判据）──"
printf "%-14s %-11s %-11s %-6s %-9s %s\n" 服务 Restart RestartSec Burst Interval 判定
FAIL=0
for s in $SERVICES caddy; do
  systemctl cat "$s" >/dev/null 2>&1 || continue
  R=$(systemctl show "$s" -p Restart --value)
  RS=$(systemctl show "$s" -p RestartUSec --value)
  B=$(systemctl show "$s" -p StartLimitBurst --value)
  I=$(systemctl show "$s" -p StartLimitIntervalUSec --value)
  verdict="OK"
  [ "$RS" = "10s" ] || { verdict="🔴 RestartSec 没生效"; FAIL=1; }
  [ "$B" = "10" ]   || { verdict="🔴 Burst 没生效"; FAIL=1; }
  case "$I" in 5min|300s) ;; *) verdict="🔴 Interval 没生效"; FAIL=1 ;; esac
  [ "$s" = "caddy" ] && [ "$R" = "no" ] && { verdict="🔴 caddy 还是不重启"; FAIL=1; }
  printf "%-14s %-11s %-11s %-6s %-9s %s\n" "$s" "$R" "$RS" "$B" "$I" "$verdict"
done

echo
if [ "$FAIL" -eq 0 ]; then
  echo "✅ 全部生效。回滚：rm 掉各 *.service.d/restart-policy.conf，或从 $BACKUP 拷回，再 daemon-reload"
else
  echo "🔴 有没生效的，见上面。备份在 $BACKUP"
  exit 1
fi
