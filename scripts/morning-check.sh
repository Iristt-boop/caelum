#!/usr/bin/env bash
# 晨检 —— 每天早上 08:00 给糖糖的锁屏推一张「系统体检卡」。
#
# 查四块（全部只读）：
#   1. 备份：昨晚 04:17 那份加密包在不在、多新、多大
#   2. 话题：中文热榜（trendshub）昨天进了多少候选、池子进了几条
#   3. 昨夜主动：CareLedger 的念头/开口/被拦/失败计数
#   4. 服务：7 个 systemd + attention 心跳 + 磁盘
#
# 边界说明：这张卡是**系统消息**不是 Nox 说话 —— 标题写「晨检」，
# 走 /api/push/send 但不带 session_id（不进聊天流、不落 conversations）。
# MORNING_CHECK_NO_PUSH=1 时只输出不推送（调试用）。
set -u
LOG_TAG="[晨检 $(date '+%m-%d %H:%M')]"

SUMMARY=$(python3 - <<'PYEOF'
import json, re, sqlite3, subprocess, time
from datetime import datetime, timedelta, timezone

problems = []
parts = []

def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def load(path, sql, params=()):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows

# ── 1) 备份 ──────────────────────────────────────────
try:
    out = subprocess.run(
        ["bash", "-c",
         "ls -1t /root/backups/auto/caelum-*.tar.gz.enc 2>/dev/null | head -1"],
        capture_output=True, text=True).stdout.strip()
    if not out:
        problems.append("备份缺失")
        parts.append("备份✗缺失")
    else:
        import os
        age_h = (time.time() - os.path.getmtime(out)) / 3600
        size_mb = os.path.getsize(out) / 1e6
        if age_h > 26:
            problems.append(f"备份 {age_h:.0f}h 未更新")
        parts.append(f"备份✓{size_mb:.0f}M/{age_h:.0f}h前")
except Exception as e:
    problems.append(f"备份检查炸了:{e}")
    parts.append("备份✗异常")

# ── 2) 中文热榜话题 ──────────────────────────────────
try:
    cand = load("/root/nox-core/data/topics.db",
                "SELECT count(*) FROM candidates WHERE source='trendshub' "
                "AND fetched_at >= datetime('now', '-1 day')")[0][0]
    pool = load("/root/nox-core/data/topics.db",
                "SELECT count(*) FROM topics WHERE observed_at >= datetime('now', '-1 day')")[0][0]
    parts.append(f"热榜候选+{cand}/入池{pool}")
except Exception as e:
    problems.append(f"话题检查炸了:{e}")
    parts.append("话题✗异常")

# ── 3) 昨夜主动（CareLedger，近 24h）────────────────
try:
    raw = load("/root/nox-core/data/attention.db",
               "SELECT value FROM source_state WHERE key='care.ledger'")[0][0]
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    ev = [e for e in json.loads(raw)["events"] if parse_ts(e["at"]) >= since]
    speak = sum(1 for e in ev if e["decision"] == "speak")
    block = sum(1 for e in ev if e["decision"] == "block")
    skip = sum(1 for e in ev if e["decision"] == "skip")
    failed = sum(1 for e in ev if e["decision"] == "failed")
    if failed:
        problems.append(f"昨夜 failed {failed} 次")
    parts.append(f"昨夜主动:说{speak}拦{block}吞{skip}" + (f"✗fail{failed}" if failed else ""))
except Exception as e:
    problems.append(f"账本检查炸了:{e}")
    parts.append("账本✗异常")

# ── 4) 服务 + 心跳 + 磁盘 ───────────────────────────
try:
    dead = []
    for s in ("bridge", "nox-core", "ombre-brain", "co-reading",
              "co-watching", "eryu", "netease-mcp"):
        r = subprocess.run(["systemctl", "is-active", s],
                           capture_output=True, text=True).stdout.strip()
        if r != "active":
            dead.append(s)
    if dead:
        problems.append("服务挂:" + ",".join(dead))
        parts.append(f"服务✗{len(dead)}挂")
    else:
        parts.append("服务7/7")

    # attention 心跳：attention.db 最近写入
    import os
    attn = "/root/nox-core/data/attention.db"
    newest = max(os.path.getmtime(attn), os.path.getmtime(attn + "-wal")
                 if os.path.exists(attn + "-wal") else 0)
    age_m = (time.time() - newest) / 60
    if age_m > 30:
        problems.append(f"attention {age_m:.0f} 分钟没动静")
        parts.append(f"心跳✗{age_m:.0f}m")
    else:
        parts.append("心跳✓")

    disk = subprocess.run(["df", "-P", "/"], capture_output=True, text=True)
    pct = disk.stdout.split("\n")[1].split()[4].rstrip("%")
    if int(pct) > 85:
        problems.append(f"磁盘 {pct}%")
    parts.append(f"磁盘{pct}%")
except Exception as e:
    problems.append(f"系统检查炸了:{e}")
    parts.append("系统✗异常")


# ── 5) 他那只手：反向链路昨天断了几次 ────────────────
#
# 2026-09-06 加。糖糖：「断线基本 1-2 天出现一次」「我没办法判断
# local-gateway 在不在跑」。OS 侧栏的灯只看得见**此刻**，
# 这里补上「昨天怎么样」—— 那才是判断「是不是变差了」的依据。
#
# 🔴 只数 1005/1006（真的异常断开）。**1012 是 Service Restart**，
# 那是部署重启，不是故障 —— 09-06 那天 7 次 1012 全是部署，
# 混进来会让这个数字天天报警而她学会无视它。
try:
    j = subprocess.run(
        ["journalctl", "-u", "nox-core", "--since", "24 hours ago", "--no-pager"],
        capture_output=True, text=True, timeout=30).stdout
    drops = [l for l in j.splitlines()
             if "本地链路断开" in l and ("1005" in l or "1006" in l)]
    ups = [l for l in j.splitlines() if "本地链路建立" in l]
    if not ups and not drops:
        parts.append("手·无记录")
    elif drops:
        # 「断了 N 秒」是新版 local_link 在重连时打的，能直接读出恢复时长
        gaps = []
        for l in ups:
            m = re.search(r"断了 (\d+) 秒", l)
            if m:
                gaps.append(int(m.group(1)))
        worst = f"，最久 {max(gaps)//60 or 1} 分" if gaps else ""
        parts.append(f"手✗断{len(drops)}次{worst}")
        problems.append(f"链路断 {len(drops)} 次{worst}")
    else:
        parts.append("手✓稳")
except Exception as e:
    parts.append("手·查不到")

# ── 6) 日志里出了什么新问题 ──────────────────────────
#
# 2026-09-08 加。糖糖：「有日志有警告，没人看是个问题吧？」
# 那天量出来的代价：utility 全线 401 **三十多个小时**没人发现
# （主聊天照常，表面看不出来）、bridge 的估价告警喊了 8728 次。
# 三件全躺在日志里，一条都没被看见。
#
# 🔴 这里**只放一句结论**。卡的 body 硬卡在 260 字，塞不下细节 ——
# 细节在 Caelum OS → Settings → Advanced，log-digest 顺手写成 JSON 了。
# 「没人看日志」的解法不是「再加一个要看的地方」。
#
# 🔴 扫描器**自己不修任何东西**。她划的线：「先报给 nox，
# 让他理清楚，但是先不让他自己操作」。
try:
    r = subprocess.run(["python3", "/root/log-digest.py", "--since", "48 hours ago"],
                       capture_output=True, text=True, timeout=180)
    line = next((l for l in r.stdout.splitlines() if l.startswith("日志")), "")
    if not line:
        parts.append("日志·没产出")
    elif line.startswith("日志✓"):
        parts.append("日志✓")
    else:
        # 「新增 N 类」原样搬过来 —— 这几个字就是全部结论
        parts.append(line.replace("日志⚠️ ", "日志⚠️"))
        problems.append(line.replace("日志⚠️ ", "日志"))
    # 哑巴服务是另一行，单独挂上去（它永远不会出现在「新增」里）
    mute = next((l for l in r.stdout.splitlines() if l.startswith("日志🔇")), "")
    if mute:
        parts.append("🔇" + mute.split(" ")[1].split("(")[0] + "等不写日志")
except Exception as e:
    parts.append("日志·查不到")

# ── 组摘要（bridge 会把 body 截到 300 字，这里控制在 260 内）────
head = "晨检✅ " if not problems else f"晨检⚠️ {len(problems)}项 "
body = " · ".join(parts)
if problems:
    body += " ⚠️" + "；".join(problems[:3])
print(body[:260])
PYEOF
)

echo "$LOG_TAG $SUMMARY"

# ── 推锁屏（系统卡：标题「晨检」，不带 session_id 不进聊天流）────
if [ "${MORNING_CHECK_NO_PUSH:-0}" = "1" ]; then
  echo "$LOG_TAG 调试模式，跳过推送"
  exit 0
fi

TOKEN=$(grep -oP 'Environment="?NOX_TOKEN=\K[^"]+' /etc/systemd/system/bridge.service)
PAYLOAD=$(python3 -c "
import json
print(json.dumps({'title': '晨检', 'body': '''$SUMMARY'''}, ensure_ascii=False))
")
RESP=$(curl -s -m 20 -X POST http://127.0.0.1:3003/api/push/send \
  -H "X-Nox-Token: $TOKEN" -H "Content-Type: application/json" \
  -d "$PAYLOAD")
echo "$RESP" | grep -q '"ok":true' \
  && echo "$LOG_TAG 已推送" \
  || echo "$LOG_TAG 推送失败: $RESP"
