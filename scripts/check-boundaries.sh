#!/usr/bin/env bash
# Caelum 边界法则的 grep 哨兵 —— 文档会过期，测试不会。
# 规则全文见 CAELUM-MAP.md 第二节。新增"合法例外"必须同时更新那里的例外清单。
set -u
FAIL=0
rule() {  # $1=编号 $2=说明 $3=期望(allow/veto) $4=完整 grep 命令（经 eval）
  local id="$1" desc="$2" expect="$3" cmd="$4"
  local out; out=$(eval "$cmd")
  if [ "$expect" = "veto" ]; then
    if [ -n "$out" ]; then echo "[✗] $id $desc"; echo "$out" | sed 's/^/      /'; FAIL=1
    else echo "[✓] $id $desc"; fi
  else
    if [ -z "$out" ]; then echo "[✗] $id $desc（合法调用点消失了？）"; FAIL=1
    else echo "[✓] $id $desc"; fi
  fi
}

cd "$(dirname "$0")/.." || exit 1
PYG="grep -rn --include=*.py --exclude-dir=.venv"   # nox-core 代码扫描统一口径
PYV="grep -vE :[0-9]+:[[:space:]]*#"               # 过滤纯注释行（文档性引用不算调用）

# R1 主动开口唯一出口：bridge 的 /api/push/send 只准被 nox-core/attention 调用
rule "R1" "push/send 只出现在 attention 出口 + 早报（都过 CareLedger）" veto \
  "$PYG 'api/push/send' nox-core | grep -v tests/ | grep -v 'attention/' | grep -v 'api/server.py' | $PYV"
rule "R1b" "attention 出口确实存在" allow \
  "grep -rln 'api/push/send' nox-core/attention --include='*.py'"

# R2 话题池永不直接触发说话
rule "R2" "话题池不说话" veto \
  "grep -rnE 'push/send|speaker|core\.chat|nox_chat' --include='*.py' --exclude-dir=.venv nox-core/topic_pool | $PYV"

# R3 nox-core 跨进程只走 REST/MCP，不直读别人家的 SQLite
rule "R3" "nox-core 不直读 bridge 的库" veto \
  "$PYG -E 'nox-bridge|nox_bridge\.db|/data/nox' nox-core | grep -v tests/ | $PYV"

# R4 记忆只经 MCP 客户端：直连 OB 端口的只准是 config（URL 定义处）和 ob_client
rule "R4" "OB 只准经 ob_client" veto \
  "$PYG -E '8002|ombre/mcp' nox-core | grep -v tests/ | grep -v config.py | grep -v ob_client.py | $PYV"

# R5 前端不给第二把主动消息钥匙（注释里提到端点是文档性引用，不算调用）
rule "R5" "前端不直接推消息" veto \
  "grep -rn 'api/push/send' nox-app/frontend/src nox-app/caelum-os-ui/src 2>/dev/null \
   | grep -vE ':[0-9]+:[[:space:]]*(//|\\*|/\\*)'"

# R8 花钱的动作模型够不着（2026-09-06）：createOrder 这类只准出现在
# 确认端点后面。工具层（tools/luckin.py）里若出现真下单调用就是闸门塌了。
#
# ⚠️ 认的是「工具层直接调 SERVER_TOOLS['luckin_order']」这种形状。
# `place()` 是给 confirm 端点用的出口，它在 tools/ 里定义但只被 api/server.py 调 ——
# 所以这里查的是**除 place 之外**有没有别的地方触达下单。
rule "R8" "花钱的动作只在确认端点后面" veto   "grep -n \"SERVER_TOOLS\[.luckin_order.\]\" nox-core/tools/luckin.py    | grep -v 'def place' | grep -v '^[0-9]*: *#'"

# R9 时间语义只有一处地基（2026-09-14，审计 F1）。
#
# 审计时全仓有 **8 份**独立的 `timezone(timedelta(hours=8))`，
# 三个名字（CST / LOCAL_TZ / _CST）指同一个东西。眼下值一样所以看不出问题 ——
# 这正是它危险的地方：哪天要支持她出国、或者把「她的一天」从 00:00 挪到 04:00
# （她凌晨才睡），得改八处，**漏一处不报错**。
#
# 只有 nox-core/temporal.py 能造它。别处再写就是 F1 长回来了。
rule "R9" "UTC+8 只在 temporal.py 定义一次" veto   "$PYG -E 'timezone\(timedelta\(hours=8' nox-core | grep -v tests/ | grep -v 'nox-core/temporal.py' | $PYV"

echo "════════"
if [ "$FAIL" -eq 0 ]; then echo "边界法则全部守住了 ✓"; else echo "有越界，见上 ✗"; exit 1; fi
