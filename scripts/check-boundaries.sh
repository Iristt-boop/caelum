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

echo "════════"
if [ "$FAIL" -eq 0 ]; then echo "边界法则全部守住了 ✓"; else echo "有越界，见上 ✗"; exit 1; fi
