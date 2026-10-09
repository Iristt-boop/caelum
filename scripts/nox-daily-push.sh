#!/bin/bash
# 每天早上给糖糖推一次早报。由 nox-daily.timer 按点调。
#
# 打的是 bridge，不是 Core —— 和自动关心（server.js 的 [Care]）走同一条路：
#   bridge 找到她最近说话的 session
#     → 让 Core 生成（这句话进他自己的 history）
#     → saveMessage 落 conversations（前端读的是这张表）
#     → 再推锁屏
#
# 直接打 Core 会绕过中间两步，她回「嗯有点」的时候他就不知道自己问过什么了。
# 数据不全时 Core 会自己跳过，不需要在这里判断。
#
# token 从 bridge 自己的 EnvironmentFile 里取，不另存一份 ——
# 复制出去的密钥迟早和真源对不上。取到之后绝不 echo。
#
# 🔴 2026-10-09 修：这里原来读 `systemctl show bridge -p Environment`。09-11 换密钥那天
# NOX_TOKEN 从 unit 的 Environment= 挪进了 /etc/nox/bridge.env（EnvironmentFile），
# 于是这条一直取空 —— **早报 09-12 起连续 28 天没发出去**，每天 10:00 只在 journal 里
# 留一行「没取到 NOX_TOKEN」。同批的 caelum-watch.sh / morning-check.sh 早改成读 env 文件了，
# 只有这个漏了。
#
# 环境变量 BRIDGE_ENV / CURL 只给测试用（scripts/test-nox-daily-push.sh），线上不设。
set -u

BRIDGE_ENV="${BRIDGE_ENV:-/etc/nox/bridge.env}"
CURL="${CURL:-/usr/bin/curl}"

TOKEN=$(grep -m1 '^NOX_TOKEN=' "$BRIDGE_ENV" 2>/dev/null | cut -d= -f2- | tr -d "\"'\r")
if [ -z "${TOKEN}" ]; then
  echo "从 ${BRIDGE_ENV} 里没取到 NOX_TOKEN，/api/* 会被 403 挡下" >&2
  exit 1
fi

# --max-time 给足：include_memory 要去 Ombre Brain 捞一遍（约 7 秒），
# 加上模型生成，正常十几秒。
exec "$CURL" -fsS --max-time 120 \
  -X POST http://127.0.0.1:3003/api/daily-push \
  -H 'Content-Type: application/json' \
  -H "X-Nox-Token: ${TOKEN}" \
  -d '{}'
