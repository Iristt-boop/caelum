#!/bin/bash
# ================================================================
# Nox VPS Monitor  v1.0
# 运行:  crontab -e  →  */5 * * * * /root/vps-scripts/monitor.sh
# 日志:  /var/log/nox-monitor.log
# ================================================================

set -e

LOG="/var/log/nox-monitor.log"
MAX_LOG_LINES=5000
HOST=$(hostname)
NOW=$(date '+%Y-%m-%d %H:%M:%S')

# ---- 阈值 ----
MEM_WARN_PCT=80      # 内存告警
MEM_CRIT_PCT=92      # 内存严重（接近 OOM）
CPU_WARN_LOAD=2.0    # load avg 告警
DISK_WARN_PCT=85     # 磁盘告警
DISK_CRIT_PCT=95     # 磁盘严重

# ---- 服务列表 ----
SERVICES=(
  bridge
  ombre-brain
  app-tracker
  caddy
  cloudflared-bridge
  cloudflared-ombre
  cloudflared-tracker
)

# ================================================================
# 日志轮转
# ================================================================
rotate_log() {
  if [ -f "$LOG" ]; then
    lines=$(wc -l < "$LOG")
    if [ "$lines" -gt "$MAX_LOG_LINES" ]; then
      tail -n $((MAX_LOG_LINES / 2)) "$LOG" > "${LOG}.tmp"
      mv "${LOG}.tmp" "$LOG"
    fi
  fi
}

log() {
  echo "[$NOW] $1" >> "$LOG"
}

# ================================================================
# 1. 内存检查
# ================================================================
check_memory() {
  mem_total_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
  mem_avail_kb=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
  mem_used_pct=$(( 100 - (mem_avail_kb * 100 / mem_total_kb) ))
  mem_total_mb=$(( mem_total_kb / 1024 ))
  mem_avail_mb=$(( mem_avail_kb / 1024 ))

  if [ "$mem_used_pct" -ge "$MEM_CRIT_PCT" ]; then
    log "[CRIT] 内存 ${mem_used_pct}% (可用 ${mem_avail_mb}MB / 总计 ${mem_total_mb}MB) — 接近 OOM！"
    # 列出占用最高的进程
    log "[CRIT] TOP 5 进程:"
    ps aux --sort=-%mem | head -6 | tail -5 >> "$LOG"
    # 尝试释放 cache
    sync; echo 1 > /proc/sys/vm/drop_caches 2>/dev/null || true
    log "[CRIT] 已尝试释放 page cache"
  elif [ "$mem_used_pct" -ge "$MEM_WARN_PCT" ]; then
    log "[WARN] 内存 ${mem_used_pct}% (可用 ${mem_avail_mb}MB / 总计 ${mem_total_mb}MB)"
  fi
}

# ================================================================
# 2. CPU 负载
# ================================================================
check_cpu() {
  load=$(awk '{print $1}' /proc/loadavg)
  if [ "$(echo "$load > $CPU_WARN_LOAD" | bc -l 2>/dev/null || echo 0)" = "1" ]; then
    log "[WARN] CPU load: $load"
  fi
}

# ================================================================
# 3. 磁盘
# ================================================================
check_disk() {
  df -h / | tail -1 | while read fs size used avail pct mp; do
    pct_num=${pct%%%}
    if [ "$pct_num" -ge "$DISK_CRIT_PCT" ]; then
      log "[CRIT] 磁盘 ${pct} (已用 ${used}/${size})"
    elif [ "$pct_num" -ge "$DISK_WARN_PCT" ]; then
      log "[WARN] 磁盘 ${pct} (已用 ${used}/${size})"
    fi
  done
}

# ================================================================
# 4. 服务健康检查 + 自动重启
# ================================================================
check_services() {
  for svc in "${SERVICES[@]}"; do
    if ! systemctl is-active --quiet "$svc" 2>/dev/null; then
      log "[DOWN] $svc 未运行 — 尝试重启..."
      systemctl restart "$svc" 2>&1 >> "$LOG" || true
      sleep 2
      if systemctl is-active --quiet "$svc" 2>/dev/null; then
        log "[OK] $svc 重启成功"
      else
        log "[FAIL] $svc 重启失败！请手动检查"
      fi
    fi
  done
}

# ================================================================
# 5. Bridge API 端点探测
# ================================================================
check_api() {
  if command -v curl >/dev/null 2>&1; then
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:3003/health 2>/dev/null || echo "000")
    if [ "$code" != "200" ]; then
      log "[WARN] Bridge /health 返回 $code"
    fi
  fi
}

# ================================================================
# 6. Nginx/Caddy 端口探测（443 是否可达）
# ================================================================
check_ports() {
  if ! ss -tlnp | grep -q ':443\b'; then
    log "[WARN] 端口 443 未监听（Caddy 可能挂了）"
  fi
  if ! ss -tlnp | grep -q ':3003\b'; then
    log "[WARN] 端口 3003 未监听（Bridge 可能挂了）"
  fi
}

# ================================================================
# 执行
# ================================================================
rotate_log
log "--- tick ---"

check_memory
check_cpu
check_disk
check_services
check_api
check_ports

# 每小时汇总一次
if [ "$(date '+%M')" = "00" ]; then
  mem_pct=$(( 100 - ($(grep MemAvailable /proc/meminfo | awk '{print $2}') * 100 / $(grep MemTotal /proc/meminfo | awk '{print $2}')) ))
  disk_pct=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')
  load=$(awk '{print $1}' /proc/loadavg)
  svc_ok=0; svc_down=""
  for svc in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
      svc_ok=$((svc_ok + 1))
    else
      svc_down="$svc_down $svc"
    fi
  done
  log "[HOURLY] 内存 ${mem_pct}% | 磁盘 ${disk_pct}% | Load ${load} | 服务 ${svc_ok}/${#SERVICES[@]}${svc_down:+ 异常:${svc_down}}"
fi
