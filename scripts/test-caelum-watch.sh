#!/usr/bin/env bash
# caelum-watch.sh 的行为测试 —— 全部用假的 systemctl / curl / free / df，
# 不碰任何真服务，本机 git-bash 就能跑。
#
# ## 为什么这个脚本值得测
#
# 它是「出事了你会知道」这件事的唯一实现。而它最容易坏的地方不是检查逻辑，
# 是**吵不吵**的那几段：防抖、冷却、恢复通知。
# 这几段错了的表现是两种极端 —— 要么一声不吭，要么每 5 分钟吵一次，
# 吵到她把通知关掉。**关掉之后就等于回到今天之前。**
#
# 用法：bash scripts/test-caelum-watch.sh
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
WATCH="$HERE/caelum-watch.sh"
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT

BIN="$SANDBOX/bin"; mkdir -p "$BIN"
STATE="$SANDBOX/state"
PUSHLOG="$SANDBOX/pushes"
: > "$PUSHLOG"

# ── 假环境 ────────────────────────────────────────────────────
# 各服务的状态由 $SANDBOX/svc-<名字> 决定，默认 active
cat > "$BIN/systemctl" <<EOF
#!/usr/bin/env bash
case "\$1" in
  is-active) cat "$SANDBOX/svc-\$2" 2>/dev/null || echo active ;;
  is-failed) [ "\$(cat "$SANDBOX/svc-\$2" 2>/dev/null)" = failed ] && echo failed || echo no ;;
esac
EOF

# 假 curl：/api/health 的返回由 $SANDBOX/health 决定；
# 推送则把标题+正文记进 $PUSHLOG
cat > "$BIN/curl" <<EOF
#!/usr/bin/env bash
for a in "\$@"; do case "\$a" in *api/health*) cat "$SANDBOX/health" 2>/dev/null; exit 0 ;; esac; done
for a in "\$@"; do
  if [ -f "\$a" ] && grep -q 'push/send' "\$a" 2>/dev/null; then
    b=\$(grep '^data = @' "\$a" | cut -d@ -f2-)
    cat "\$b" >> "$PUSHLOG"; echo >> "$PUSHLOG"
    cat "$SANDBOX/pushcode" 2>/dev/null || echo 200
    exit 0
  fi
done
echo 000
EOF

cat > "$BIN/free" <<'EOF'
#!/usr/bin/env bash
echo "              total        used        free      shared  buff/cache   available"
echo "Mem:           3934        1200         400          10        2300        2350"
EOF

cat > "$BIN/df" <<'EOF'
#!/usr/bin/env bash
echo "Filesystem     1024-blocks    Used Available Capacity Mounted on"
echo "/dev/vda1         51475068 20000000  28000000      41% /"
EOF

# 假 doctor.sh：只回服务清单
cat > "$SANDBOX/doctor.sh" <<'EOF'
#!/usr/bin/env bash
[ "${1:-}" = "--list-services" ] && { echo "caddy bridge nox-core"; exit 0; }
EOF

# python3：被测脚本真的要用它拼 JSON，所以**不能假装有**。
#
# 🔴 **不能只看 `command -v python3` 有没有。** Windows 上
# `C:\Users\…\AppData\Local\Microsoft\WindowsApps\python3` 是微软商店的
# **假壳**：它存在、`command -v` 找得到、退出码也正常，但**一个字都不输出**。
# 于是被测脚本拼出一个空 body —— 而我第一版 harness 就这么"全绿"了。
# 判据只能是「让它真算一次，看结果对不对」。
works_py() { [ "$("$1" -c 'print(1+1)' 2>/dev/null)" = "2" ]; }

if ! works_py python3; then
  REALPY=""
  for c in /d/claude-code/nox-core/.venv/Scripts/python.exe python py; do
    p=$(command -v "$c" 2>/dev/null) || continue
    works_py "$p" && { REALPY="$p"; break; }
  done
  [ -n "$REALPY" ] || { echo "🔴 本机没有能用的 python，这套测试跑不了（不是被测脚本的问题）"; exit 2; }
  printf '#!/usr/bin/env bash\nexec "%s" "$@"\n' "$REALPY" > "$BIN/python3"
fi

chmod +x "$BIN"/* "$SANDBOX/doctor.sh"
echo '{"status":"ok","checks":{}}' > "$SANDBOX/health"
echo 'NOX_TOKEN=test-token' > "$SANDBOX/bridge.env"

# ⚠️ `FLAP` / `COOL` 没设的时候**不要**注入默认值 ——
#    注入了就等于把脚本自己的默认值架空，那条默认值就再也没被测过。
#    （变异验证抓到的：把默认防抖从 2 改成 1，15 条测试全绿。）
run() {
  local env=(
    PATH="$BIN:$PATH"
    DOCTOR="$SANDBOX/doctor.sh"
    BRIDGE_ENV="$SANDBOX/bridge.env"
    STATE_DIR="$STATE"
  )
  [ -n "${FLAP:-}" ] && env+=("FLAP_COUNT=$FLAP")
  [ -n "${COOL:-}" ] && env+=("COOLDOWN=$COOL")
  env "${env[@]}" bash "$WATCH" 2>&1
}

# ⚠️ 别写 `grep -c . f || echo 0` —— 没匹配时 grep **既打印 0 又退出 1**，
#    那个 `||` 会再补一个 0，结果变成两行。第一版就栽在这儿，
#    14 条测试全红而被测脚本其实没问题。
pushes() { awk 'NF' "$PUSHLOG" 2>/dev/null | wc -l | tr -d ' '; }
reset()  { rm -rf "$STATE"; : > "$PUSHLOG"; rm -f "$SANDBOX"/svc-*; echo '{"status":"ok","checks":{}}' > "$SANDBOX/health"; }

PASS=0; FAIL=0
check() {  # $1=说明 $2=实际 $3=期望
  if [ "$2" = "$3" ]; then PASS=$((PASS+1)); echo "  [✓] $1"
  else FAIL=$((FAIL+1)); echo "  [✗] $1 —— 得到 '$2'，期望 '$3'"; fi
}

# ── 🔴 自证：先证明这套测试真的在测东西 ──────────────────────
#
# 这里有一整类失败模式叫「空过」：被测脚本根本没跑起来（路径错、缺依赖、
# 语法错），于是"一条都没推"，而大半条用例期望的正是 0 —— **全绿**。
# 2026-09-13 第一次跑就撞上了：本机没有 `python3`，推送内容是空文件，
# 14 条里有 3 条"通过"，而那 3 条是假的。
#
# 所以先做一次**必须有动静**的检查。它不过就整套中止，不往下跑。
echo "-- 自证 --"
reset
echo inactive > "$SANDBOX/svc-nox-core"
SELFTEST=$(FLAP=1 run)
if [ "$(pushes)" != "1" ]; then
  echo "  [✗] 自证失败 —— 被测脚本没有按预期推出一条。下面的用例即使全绿也不算数。"
  echo "      脚本输出："; printf '%s\n' "$SELFTEST" | sed 's/^/        /'
  exit 2
fi
echo "  [✓] 被测脚本确实在跑，也确实推得出东西"

# ── 用例 ──────────────────────────────────────────────────────
echo
echo "-- 一切正常时不吵 --"
reset; run >/dev/null; run >/dev/null
check "全绿两轮，一条都不推" "$(pushes)" "0"

echo
echo "-- 防抖：坏一次不叫，坏两次才叫 --"
# 🔴 这一组**不传 FLAP**，测的就是脚本自己的默认值。
#    默认值要是被改成 1，她每次部署都会收到一条告警（重启那几秒）——
#    收几次之后就会把通知关掉，那等于回到今天之前。
reset
echo inactive > "$SANDBOX/svc-nox-core"
run >/dev/null
check "默认设置下，第一次只记账不推送" "$(pushes)" "0"
run >/dev/null
check "第二次才推" "$(pushes)" "1"
check "正文里点名了 nox-core" "$(grep -c 'nox-core' "$PUSHLOG")" "1"

echo
echo "-- 冷却：同一个问题不重复吵 --"
run >/dev/null; run >/dev/null; run >/dev/null
check "又跑了三轮还是只推过 1 条" "$(pushes)" "1"

echo
echo "-- 恢复：好了要报一次平安，而且只报一次 --"
rm -f "$SANDBOX/svc-nox-core"
run >/dev/null
check "恢复推了第 2 条" "$(pushes)" "2"
check "第 2 条说的是恢复" "$(grep -c '恢复' "$PUSHLOG")" "1"
run >/dev/null; run >/dev/null
check "之后不再重复报平安" "$(pushes)" "2"

echo
echo "-- 🔴 从没告警过就不该报恢复 --"
reset
echo inactive > "$SANDBOX/svc-bridge"
run >/dev/null                      # 第 1 次，防抖没到，不推
rm -f "$SANDBOX/svc-bridge"
run >/dev/null
check "没头没尾的「恢复了」不许发" "$(pushes)" "0"

echo
echo "-- 换了一个问题要重新提醒，不吃上一个的冷却 --"
reset; FLAP=1
echo inactive > "$SANDBOX/svc-bridge";   run >/dev/null
rm -f "$SANDBOX/svc-bridge"
echo inactive > "$SANDBOX/svc-caddy";    run >/dev/null
check "两个不同的问题各推一条" "$(pushes)" "2"
unset FLAP

echo
echo "-- 探活不过要说出是哪一项 --"
reset; FLAP=1
echo '{"status":"degraded","checks":{"ombre":{"ok":false},"core":{"ok":true}}}' > "$SANDBOX/health"
run >/dev/null
check "正文点名 ombre" "$(grep -c 'ombre' "$PUSHLOG")" "1"
unset FLAP

echo
echo "-- 🔴 推送失败不许记账（下一轮还要再试）--"
reset; FLAP=1
echo 500 > "$SANDBOX/pushcode"
echo inactive > "$SANDBOX/svc-nox-core"
run >/dev/null
echo 200 > "$SANDBOX/pushcode"
: > "$PUSHLOG"
run >/dev/null
check "上次没送出去，这次重试了" "$(pushes)" "1"
rm -f "$SANDBOX/pushcode"

echo
echo "-- 🔴 拿不到服务清单要明说，不许默默跳过 --"
reset; FLAP=1
DOCTOR_BAK="$SANDBOX/doctor.sh"; mv "$DOCTOR_BAK" "$SANDBOX/doctor.gone"
run >/dev/null
check "把「拿不到清单」当成问题推出去" "$(grep -c '拿不到服务清单' "$PUSHLOG")" "1"
mv "$SANDBOX/doctor.gone" "$DOCTOR_BAK"
unset FLAP

echo
echo "-- 🔴 token 不许出现在命令行里 --"
reset; FLAP=1
cat > "$BIN/curl" <<EOF
#!/usr/bin/env bash
for a in "\$@"; do case "\$a" in *api/health*) cat "$SANDBOX/health" 2>/dev/null; exit 0 ;; esac; done
printf '%s\n' "\$@" > "$SANDBOX/curl-argv"
echo 200
EOF
chmod +x "$BIN/curl"
echo inactive > "$SANDBOX/svc-nox-core"
run >/dev/null
# 同 pushes()：grep -c 没匹配时会「打印 0 且退出 1」，别再接 `|| echo 0`
check "argv 里没有 token" "$(grep -c 'test-token' "$SANDBOX/curl-argv" 2>/dev/null | head -1)" "0"
check "确实抓到了那次调用（否则上一条是空过）" \
      "$(grep -c 'push/send\|-K' "$SANDBOX/curl-argv" 2>/dev/null | head -1)" "1"
unset FLAP

echo
echo "════════"
echo "通过 $PASS，失败 $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
