#!/bin/bash
# 认领那六个端口。只读：看进程的 cwd 和命令行，不动任何东西。
for p in 8003 8004 8101 8102 3456 9090 3100 8000 8002; do
  pid=$(ss -lntp 2>/dev/null | grep ":$p " | grep -oP 'pid=\K[0-9]+' | head -1)
  if [ -z "$pid" ]; then
    echo "$p  → 没在听"
    continue
  fi
  cwd=$(readlink /proc/$pid/cwd 2>/dev/null)
  cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null | cut -c1-90)
  unit=$(cat /proc/$pid/cgroup 2>/dev/null | grep -oP '[^/]+\.service' | head -1)
  age=$(ps -o etimes= -p $pid 2>/dev/null | tr -d ' ')
  printf '%-5s → %s\n' "$p" "${unit:-（不是 systemd 服务）}"
  printf '       cwd  : %s\n' "$cwd"
  printf '       cmd  : %s\n' "$cmd"
  printf '       跑了 : %s 秒\n' "$age"
done
