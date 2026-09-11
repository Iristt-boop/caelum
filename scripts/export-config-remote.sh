#!/usr/bin/env bash
# 在 VPS 上导出「配置仓库」的内容：systemd unit / drop-in / Caddyfile / crontab /
# env 模板。**导出后做一次密钥指纹扫描** —— 有命中就不给拉走。
#
# 由本地 scripts/pull-config.ps1 调用。
set -u

OUT=/root/config-export
OURS="app-tracker bridge co-reading co-watching eryu ha-mcp health-mcp health-sync mcp-train mcp-trends netease-mcp nox-core nox-daily ombre-brain touch-mcp touch-server toy-mcp"
DROPS="bridge mcp-trends nox-core ombre-brain"

rm -rf "$OUT"
mkdir -p "$OUT"/{systemd,caddy,cron,env-templates}

echo "== [1] 收 systemd unit / timer =="
n=0
for u in $OURS; do
  for ext in service timer; do
    if [ -f "/etc/systemd/system/$u.$ext" ]; then
      cp "/etc/systemd/system/$u.$ext" "$OUT/systemd/"
      n=$((n+1))
    fi
  done
done
echo "  收了 $n 个"

echo "== [2] 收 drop-in（只收我们自己的）=="
for d in $DROPS; do
  if compgen -G "/etc/systemd/system/$d.service.d/*.conf" > /dev/null; then
    mkdir -p "$OUT/systemd/$d.service.d"
    cp /etc/systemd/system/$d.service.d/*.conf "$OUT/systemd/$d.service.d/"
    echo "  $d.service.d: $(ls -1 /etc/systemd/system/$d.service.d/*.conf | wc -l) 个"
  fi
done

echo "== [3] 收 crontab =="
crontab -l 2>/dev/null > "$OUT/cron/crontab.txt"
echo "  $(grep -cv '^\s*#\|^\s*$' "$OUT/cron/crontab.txt") 条有效行"

echo "== [4] Caddyfile 脱敏 → caddy/Caddyfile.target =="
# 真实暗号换成 Caddy 的 {$VAR} 占位符（解析期从环境变量取值）
sed -E 's|(/[a-z][a-z-]*/)[0-9a-f]{16,}|\1{$TOKEN_PATH}|g; s|(/agent/)[A-Za-z0-9]{16,}|\1{$TOKEN_AGENT}|g' \
  /etc/caddy/Caddyfile > "$OUT/caddy/Caddyfile.target"
left=$(grep -cE '/[a-z-]+/[0-9a-f]{16,}|/agent/[A-Za-z0-9]{16,}' "$OUT/caddy/Caddyfile.target" || true)
echo "  脱敏后残留的长令牌: $left  $([ "$left" = 0 ] && echo ✅ || echo 🔴)"
echo "  -- 脱敏后的 handle 行 --"
grep -nE 'handle_path|handle /' "$OUT/caddy/Caddyfile.target" | head -8 | sed 's/^/    /'

echo "== [5] env 模板（只有变量名，值全部替换）=="
for f in /etc/nox/*.env; do
  [ -f "$f" ] || continue
  b=$(basename "$f")
  sed -E 's/^([A-Z_]+)=.*/\1=<填值>/' "$f" > "$OUT/env-templates/$b.example"
  echo "  $b.example: $(grep -c '=<填值>' "$OUT/env-templates/$b.example") 个变量"
done
# 光有 /etc/nox 还不够：还有几个服务用自己的 .env
for pair in "co-reading:/root/co-reading-mcp/.env" "health-mcp:/root/health-mcp/.env" "netease-mcp:/root/netease-music-mcp/.env" "eryu:/root/eryu/server/.secret" "nocore:/root/nox-core/.env"; do
  svc=${pair%%:*}; path=${pair#*:}
  [ -f "$path" ] || continue
  # 🔴 有的文件**不是 KEY=value 格式**（例如 eryu 的 .secret 就是个裸 token）。
  #    那种一律不导出内容 —— 2026-09-11 第一版就是在这里漏了一个真 token 出去，
  #    靠第 [6] 步的指纹扫描抓回来的。
  if grep -qE '^[A-Za-z_][A-Za-z0-9_]*=' "$path"; then
    sed -E 's/^([A-Za-z_][A-Za-z0-9_]*)=.*/\1=<填值>/' "$path" > "$OUT/env-templates/$svc.env.example"
    echo "  $svc.env.example: $(grep -c '=<填值>' "$OUT/env-templates/$svc.env.example") 个变量"
  else
    {
      echo "# $path 不是 KEY=value 格式（裸凭据），**故意不导出内容**。"
      echo "# 恢复时手工写入，权限 600。"
      echo "# 行数: $(wc -l < "$path")  字节: $(stat -c %s "$path")"
    } > "$OUT/env-templates/$svc.env.example"
    echo "  $svc.env.example: 裸凭据，只留说明（不导出内容）"
  fi
done

echo
echo "== [6] 🔴 密钥扫描：导出内容里有没有"活的"凭据 =="
python3 - "$OUT" <<'PYEOF'
import glob, hashlib, os, re, sys, subprocess

OUT = sys.argv[1]

def fp(v):
    return hashlib.sha256(v.encode()).hexdigest()[:8]

# 1) 收集"活值"的指纹：所有服务的运行环境 + /etc/nox/*.env + 各服务 .env
live = set()
pats = {}
for svc in "bridge nox-core ombre-brain ha-mcp toy-mcp touch-mcp touch-server co-reading co-watching eryu netease-mcp health-mcp app-tracker".split():
    try:
        pid = subprocess.check_output(["systemctl","show","-p","MainPID","--value",svc], text=True).strip()
        if pid and pid != "0":
            raw = open(f"/proc/{pid}/environ","rb").read().decode("utf-8","replace")
            for kv in raw.split("\0"):
                if "=" in kv:
                    k, v = kv.split("=",1)
                    if len(v) >= 16 and re.search(r"(TOKEN|KEY|PASSWORD|SECRET|COOKIE)", k, re.I):
                        live.add(fp(v)); pats[fp(v)] = f"{svc}:{k}"
    except Exception:
        pass
for f in ["/etc/nox/*.env", "/root/nox-core/.env", "/root/co-reading-mcp/.env",
          "/root/health-mcp/.env", "/root/netease-music-mcp/.env", "/root/eryu/server/.secret"]:
    for p in glob.glob(f):
        try:
            for line in open(p, encoding="utf-8", errors="replace"):
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'")
                    if len(v) >= 16:
                        live.add(fp(v)); pats.setdefault(fp(v), os.path.basename(p)+":"+k)
        except Exception:
            pass

print(f"  活值指纹库: {len(live)} 条")

# 2) 扫导出内容里每一个"看起来像凭据"的串
hits = []
for path in glob.glob(f"{OUT}/**/*", recursive=True):
    if not os.path.isfile(path):
        continue
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    for m in re.finditer(r"[A-Za-z0-9_\-\.]{16,}", txt):
        if fp(m.group(0)) in live:
            hits.append((path.replace(OUT + "/", ""), m.group(0)[:6] + "...", pats[fp(m.group(0))]))

if hits:
    print(f"  🔴 命中 {len(hits)} 处 —— 不许拉走：")
    for h in hits[:20]:
        print(f"      {h[0]}  值以 {h[1]} 开头  ← {h[2]}")
    sys.exit(1)
print("  ✅ 导出内容里没有任何活值")
PYEOF
scan=$?

echo
if [ "$scan" != 0 ]; then
  echo "🔴 扫描有命中，导出物保留在 $OUT 供你检查，但不该拉进仓库"
  exit 1
fi
echo "✅ 导出完成：$OUT"
find "$OUT" -type f | wc -l | sed 's/^/  文件数: /'
du -sh "$OUT" | sed 's/^/  大小: /'
