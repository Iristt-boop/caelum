#!/bin/bash
# nox-daily-push.sh 的测试（沙箱里跑，不碰线上）：假 env 文件 + 假 curl。
# 守的是 2026-10-09 那件事：token 搬进 EnvironmentFile 之后脚本还在读 systemctl 的 Environment，
# 取空 → 早报 28 天没发，而且只有 journal 里一行字。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="${SCRIPT:-$HERE/nox-daily-push.sh}"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
fail=0
ok()  { echo "  ✓ $1"; }
bad() { echo "  ✗ $1"; fail=1; }

# 假 curl：把收到的参数写进文件，然后成功退出
cat > "$SANDBOX/fakecurl" <<'EOS'
#!/bin/bash
printf '%s\n' "$@" > "$FAKE_ARGS"
echo '{"ok":true}'
EOS
chmod +x "$SANDBOX/fakecurl"
export FAKE_ARGS="$SANDBOX/args"

run() { BRIDGE_ENV="$1" CURL="$SANDBOX/fakecurl" bash "$SCRIPT" 2>"$SANDBOX/stderr" >"$SANDBOX/stdout"; echo $?; }

echo "1) env 文件里有 token → 带着它打 /api/daily-push"
printf 'FOO=1\nNOX_TOKEN=abc123\nBAR=2\n' > "$SANDBOX/env1"
rc=$(run "$SANDBOX/env1")
[ "$rc" = 0 ] && ok "退出码 0" || bad "退出码 $rc"
grep -qx "X-Nox-Token: abc123" "$FAKE_ARGS" && ok "token 放在 X-Nox-Token 头里" || bad "没带上 token 头"
grep -q "http://127.0.0.1:3003/api/daily-push" "$FAKE_ARGS" && ok "打的是 bridge 的 daily-push" || bad "URL 不对"

echo "2) 带引号 / CRLF 的写法也认"
printf 'NOX_TOKEN="q-token"\r\n' > "$SANDBOX/env2"
rc=$(run "$SANDBOX/env2")
grep -qx "X-Nox-Token: q-token" "$FAKE_ARGS" && ok "引号和 \r 都剥掉了" || bad "token 取错：$(grep X-Nox "$FAKE_ARGS")"

echo "3) 没有 token → 退出码 1，说清楚缺什么，而且不调 curl"
printf 'FOO=1\n' > "$SANDBOX/env3"; rm -f "$FAKE_ARGS"
rc=$(run "$SANDBOX/env3")
[ "$rc" = 1 ] && ok "退出码 1" || bad "退出码 $rc"
grep -q "NOX_TOKEN" "$SANDBOX/stderr" && ok "stderr 点名 NOX_TOKEN" || bad "没说缺什么"
[ ! -e "$FAKE_ARGS" ] && ok "没有发出请求" || bad "没 token 还发了请求"

echo "4) env 文件不存在也是退出码 1，不是崩"
rc=$(run "$SANDBOX/nope")
[ "$rc" = 1 ] && ok "退出码 1" || bad "退出码 $rc"

echo "5) token 从不出现在 stdout / stderr"
printf 'NOX_TOKEN=s3cr3t-value\n' > "$SANDBOX/env5"
run "$SANDBOX/env5" >/dev/null
! grep -q "s3cr3t-value" "$SANDBOX/stderr" && ok "stderr 里没有" || bad "stderr 漏了 token"
! grep -q "s3cr3t-value" "$SANDBOX/stdout" && ok "stdout 里没有" || bad "stdout 漏了 token"

[ "$fail" = 0 ] && echo "全部通过" || { echo "有失败"; exit 1; }
