#!/usr/bin/env bash
# Caelum 边界法则的 grep 哨兵 —— 文档会过期，测试不会。
# 规则全文见 CAELUM-MAP.md 第二节。新增"合法例外"必须同时更新那里的例外清单。
set -u
FAIL=0
#: 有规则因为依赖不在而没跑。**跳过不是通过** —— 结尾要说出来
SKIPPED=0
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
#
# 🔴 **它扫的是另一个仓库**（2026-09-16 修）。`nox-app/` 是嵌套的独立仓库，
# 干净 clone 和 CI 上根本没有这个目录。原来那版 `2>/dev/null` 把
# 「目录不存在」的报错吞了 → grep 什么都找不到 → veto 通过 →
# **R5 在干净 clone 上恒为绿**，一条永远成立的检查。
#
# 所以这里分三种状态，而不是两种：目录在就真查，不在就**明说跳过**。
# 静默通过和明说跳过的区别，正是这份排期一直在讲的那件事。
if [ -d nox-app/frontend/src ] || [ -d nox-app/caelum-os-ui/src ]; then
  rule "R5" "前端不直接推消息" veto \
    "grep -rn 'api/push/send' nox-app/frontend/src nox-app/caelum-os-ui/src 2>/dev/null \
     | grep -vE ':[0-9]+:[[:space:]]*(//|\\*|/\\*)'"
else
  echo "[–] R5 跳过 —— nox-app/ 不在本地（独立仓库）。**这不是通过**"
  SKIPPED=1
fi

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
# 只有 nox-core/temporal/ 这个包能造它。别处再写就是 F1 长回来了。
rule "R9" "UTC+8 只在 temporal 包里定义一次" veto   "$PYG -E 'timezone\(timedelta\(hours=8' nox-core | grep -v tests/ | grep -v 'nox-core/temporal/' | $PYV"

# R10 朋友圈不许推送（2026-09-15）。
#
# 发帖不推送、不弹锁屏 —— **它不是开口**，所以它不走 R1 那条出口，
# 也不占 Care 的每日额度。但反过来说，它**绝不许自己长出一条推送**：
# 那就是第五条主动消息渠道（糖糖 2026-08-18：「不要成为第五条主动消息渠道」）。
#
# ⚠️ 这条**不能靠自觉**：发帖和开口长得太像了 ——
#    `bridge.post("/api/diary", ...)` 和 `bridge.post("/api/push/send", ...)`
#    在 diff 里只差几个字符，review 时眼睛会滑过去。
#
# 🔴 口径和 R1 一样认**完整端点路径** `api/push/send`，**故意不认裸的
#    `push/send`** —— 后者会命中 `moments/__init__.py` 里解释这条法则本身的
#    那段文档，于是哨兵天生就是红的，几天后它会被当噪音关掉。
rule "R10" "Moments 不许推送（发帖不是开口）" veto \
  "$PYG -E 'api/push/send|api/nox/push' nox-core/moments | $PYV"

# 🔴 空集不是通过（CAELUM-MAP.md 第三·五节第一个案例）：上面那条 veto 在
#    空集上恒真。`nox-core/moments/` 哪天被删掉或改名，它就**静默地永远为真** ——
#    哨兵全绿，而它盯的东西已经不存在了。
rule "R10b" "Moments 那一层确实还在（法则不许对着空目录成立）" allow \
  "ls nox-core/moments/loop.py nox-core/moments/writer.py 2>/dev/null"

# R2b Moments 不直接读话题池（设计文档第一节）：话题池的料只能经
#     `CuriositySource`（它已经把池子的料变成「好奇」这个 Drive），
#     帖子读的是**那个 Drive 和它的 evidence**，不许直接读池子。
rule "R2b" "Moments 不直接读话题池（只经 CuriositySource 变成 Drive）" veto \
  "$PYG -E 'topic_pool|topics_browse|TopicPool' nox-core/moments | $PYV"

echo "════════"
if [ "$FAIL" -ne 0 ]; then echo "有越界，见上 ✗"; exit 1; fi
if [ "$SKIPPED" -ne 0 ]; then
  echo "边界法则守住了，**但有规则被跳过（见上面的 [–]）** ——"
  echo "那些规则这一轮什么都没验证。要全覆盖，在有 nox-app/ 的机器上跑。"
else
  echo "边界法则全部守住了 ✓"
fi
