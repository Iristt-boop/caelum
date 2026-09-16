#!/usr/bin/env bash
# 装看门狗（排期 1.5）。在 VPS 上跑：bash /tmp/install-caelum-watch.sh
#
# ## 它解决的是什么
#
# 2026-09-13 查线上确认：**这台机器上没有任何自动监控。**
# `monitor.sh` 从没部署过（文件、cron、日志文件全都不存在），
# `doctor.sh` 写得很好但也没排期，只能手动跑。
# 也就是说：他半夜挂了，你只会发现"他不理我了"。
#
# 装完之后：每 5 分钟看一眼，连着坏两次就推到她手机锁屏。
#
# ## ⚠️ unit 内容为什么写在这个脚本里，不单独放文件
#
# 第八批删掉过仓库里的两份 `.service` —— 它们是**第三份副本**
# （线上 / deploy-config / 代码仓），路径还是旧的，
# 一个 `cp` 就能把线上配置改回去。不再制造新的第三份。
# 装完记得把 deploy-config 重新导一次（见脚本末尾提示）。
set -eu

echo "── [1] 放脚本 ──"
install -m 755 /tmp/caelum-watch.sh /root/caelum-watch.sh
echo "  /root/caelum-watch.sh"

echo "── [2] 写 unit ──"
cat > /etc/systemd/system/caelum-watch.service <<'UNIT'
[Unit]
# Caelum 看门狗（排期 1.5）。说明见 /root/caelum-watch.sh 开头。
Description=Caelum 看门狗：服务/探活/资源，坏了推她手机
# 网络就绪后再跑 —— 开机瞬间打 bridge 的端口是白打
After=network-online.target

[Service]
Type=oneshot
ExecStart=/root/caelum-watch.sh

# ⚠️ **不要设 Restart=**。它是 oneshot，"失败"本来就是它的正常结局之一
# （发现问题就退 1）。设了会变成每秒重跑一遍。

# stdout 直接进 journald：journalctl -u caelum-watch
# 🔴 **不写私有日志文件** —— 审计点名过 /var/log/nox-monitor.log 那种
#    "告警写进无人读取的文件"。

StateDirectory=caelum-watch
NoNewPrivileges=yes
ProtectSystem=strict
# 🔴 **`ProtectHome=yes` 会让这个服务起不来。**
#    它把 /root 整个藏掉，而脚本本体就在 /root/caelum-watch.sh，
#    它还要读 /root/doctor.sh 拿服务清单。
#    实测报的是 `203/EXEC: Failed to locate executable` ——
#    看起来像"文件不存在"，其实是"被沙箱挡住了看不见"。
#    read-only 保留了"不许写你的家目录"，同时让读和执行成立。
ProtectHome=read-only
PrivateTmp=yes
# 要读 /etc/nox/bridge.env 拿推送用的 token（600 root）
ReadOnlyPaths=/etc/nox
UNIT

cat > /etc/systemd/system/caelum-watch.timer <<'UNIT'
[Unit]
Description=每 5 分钟跑一次 Caelum 看门狗

[Timer]
# 开机 2 分钟后第一次 —— 别在服务还在起的时候就判它们死刑
OnBootSec=2min
OnUnitActiveSec=5min
# ⚠️ 不设 Persistent=true：监控要的是"现在怎么样"，不是"三小时前怎么样"；
#    而且补跑会在开机时一次性触发一堆告警。
AccuracySec=30s

[Install]
WantedBy=timers.target
UNIT
echo "  caelum-watch.service / .timer"

echo "── [3] 先空跑一次（不推送，只看检查结果）──"
systemd-analyze verify /etc/systemd/system/caelum-watch.service 2>&1 | sed 's/^/  /' || true
STATE_DIR=/tmp/watch-dryrun bash /root/caelum-watch.sh --dry 2>&1 | sed 's/^/  /' || true
rm -rf /tmp/watch-dryrun

echo
echo "── [4] 启用 ──"
systemctl daemon-reload
systemctl enable --now caelum-watch.timer
sleep 1

echo
echo "── [5] 回读验证（判据）──"
systemctl is-enabled caelum-watch.timer | sed 's/^/  enabled: /'
systemctl is-active  caelum-watch.timer | sed 's/^/  active : /'
systemctl list-timers caelum-watch.timer --no-pager | sed -n '2p' | sed 's/^/  下次: /'

echo
echo "── [6] 立刻跑一次真的（会走推送判定，但要连坏两次才推）──"
# 🔴 **这一步的结果要当判据看。**
#    第一版这里写的是 `systemctl start ... || true`，然后无论如何打 ✅ ——
#    于是 `ProtectHome=yes` 把脚本本身挡在沙箱外（203/EXEC）的时候，
#    脚本照样说"装好了"。和今天上午修的 deploy.ps1 是同一个毛病，
#    我自己又犯了一次：**别给会失败的一步配一句无条件的成功。**
systemctl start caelum-watch.service
RC=$?
journalctl -u caelum-watch -n 20 --no-pager | sed 's/^/  /'

# oneshot 退 1 = 发现了问题（正常）；203/EXEC 之类 = 它自己起不来（不正常）。
# 用 systemctl show 拿真实的退出码来分辨，别靠 start 的返回值猜。
STATUS=$(systemctl show caelum-watch.service -p ExecMainStatus --value)
if [ "$STATUS" = "203" ] || [ "$STATUS" = "127" ] || [ "$STATUS" = "126" ]; then
  echo
  echo "🔴 看门狗自己起不来（ExecMainStatus=$STATUS）—— 多半是 unit 的沙箱选项挡住了它。"
  echo "   没装成。上面的 journal 里有原因。"
  exit 1
fi
[ "$RC" -ne 0 ] && echo "  （退出码 $RC = 这一轮发现了问题，这是它的正常结局之一）"

echo
echo "✅ 装好了，而且**真的跑起来过一次**。"
echo "   查日志：journalctl -u caelum-watch -f"
echo "   停掉它：systemctl disable --now caelum-watch.timer"
echo "   ⚠️ 记得把 deploy-config 重新导一次，否则配置仓库和线上又漂移了"
