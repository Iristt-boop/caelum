#!/usr/bin/env bash
# Care 周报 —— Nox 主动行为成色的只读仪表（Week4 观察用，2026-09-05）
# 全部只读：attention.db(care.ledger/Drive/gate) + topics.db + nox-bridge.db(conversations)
# 用法: /root/care-weekly.sh [天数]   默认 7。cron 每周一 09:03 出上一周的数。
set -u
DAYS="${1:-7}"

python3 - "$DAYS" <<'PYEOF'
import json, sqlite3, sys, re
from datetime import datetime, timedelta, timezone

DAYS = int(sys.argv[1])
now = datetime.now(timezone.utc)
since = now - timedelta(days=DAYS)

def load(path, sql, params=()):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows

def parse_ts(s):
    # ledger 是 +08:00，conversations 是 Z；python3.10 的 fromisoformat 不认 Z
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

# ---------- 1) CareLedger：动了多少次念头、说出口多少 ----------
LEDGER = "/root/nox-core/data/attention.db"
events = []
try:
    raw = load(LEDGER, "SELECT value FROM source_state WHERE key='care.ledger'")[0][0]
    events = [e for e in json.loads(raw)["events"] if parse_ts(e["at"]) >= since]
except Exception as e:
    print(f"[!] CareLedger 读不到: {e}")

dec = {}
srcs = {}
block_reasons = {}
for e in events:
    dec[e["decision"]] = dec.get(e["decision"], 0) + 1
    if e["decision"] == "speak":
        srcs[e["source"]] = srcs.get(e["source"], 0) + 1
    if e["decision"] == "block":
        r = e.get("reason", "?")
        # 理由是人话，归一化成桶（数字抹掉）
        if "安静时段" in r:            key = "安静时段"
        elif "刚追过" in r:            key = "链内间隔（60 分钟）"
        elif "开过一条链" in r:         key = "新链一小时额度"
        elif "额度" in r:              key = "每日额度"
        elif "看" in r or "watch" in r.lower(): key = "她在看片抑制"
        else:                          key = r[:24]
        block_reasons[key] = block_reasons.get(key, 0) + 1

considered = dec.get("speak", 0) + dec.get("skip", 0) + dec.get("block", 0)
spoke = dec.get("speak", 0)
rate = f"{spoke/considered*100:.0f}%" if considered else "—"

ledger_span = ""
try:
    _raw = load(LEDGER, "SELECT value FROM source_state WHERE key='care.ledger'")[0][0]
    _ev = json.loads(_raw)["events"]
    if _ev:
        earliest = parse_ts(_ev[0]["at"]).astimezone(timezone(timedelta(hours=8)))
        ledger_span = f"，账本实际覆盖自 {earliest.strftime('%m-%d %H:%M')}"
except Exception:
    pass
print(f"════════ Care 周报（近 {DAYS} 天：{since.astimezone(timezone(timedelta(hours=8))).strftime('%m-%d')} ~ "
      f"{now.astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M')} CST{ledger_span}）════════")
print("-- 开口 --")
print(f"  动了念头 {considered} 次：speak {spoke} · skip {dec.get('skip',0)} · block {dec.get('block',0)}"
      f" · failed {dec.get('failed',0)}  → 命中率 {rate}")
if srcs:
    print("  说出口按源: " + " · ".join(f"{k} {v}" for k, v in sorted(srcs.items(), key=lambda x: -x[1])))
print("-- 被拦原因（栏杆松紧的依据）--")
if block_reasons:
    for k, v in sorted(block_reasons.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
else:
    print("  （近 {} 天没有 BLOCK）".format(DAYS))

# ---------- 2) 主动消息与她的回复率（含话题口味的真实信号）----------
CONV = "/root/data/nox-bridge.db"
try:
    rows = load(CONV, "SELECT id, role, content, timestamp, metadata FROM conversations "
                      "WHERE metadata LIKE '%proactive%'")
    proactive = []
    for sid, role, content, ts, meta in rows:
        try:
            t = parse_ts(ts)
        except Exception:
            continue
        if t >= since:
            proactive.append({"sid": sid, "ts": t, "content": content or ""})
    # 只算「发起接触」的主动消息：开口前她已沉默 >60 分钟——
    # 不然她本来就在聊天，24h 窗口永远算「回复了」，数字没有意义
    her = load(CONV, "SELECT id, timestamp FROM conversations WHERE role='user'")
    her_by_sid = {}
    for sid, ts in her:
        try:
            her_by_sid.setdefault(sid, []).append(parse_ts(ts))
        except Exception:
            pass
    # 主动消息按源归属：和 ledger speak 事件按时间贴近配对（±3 分钟）
    speak_events = [(parse_ts(e["at"]), e["source"]) for e in events if e["decision"] == "speak"]
    def src_of(t):
        for et, s in speak_events:
            if abs((et - t).total_seconds()) < 180:
                return s
        return "（早于账本）"
    total, replied, by_src, gaps = 0, 0, {}, []
    for p in proactive:
        times = sorted(her_by_sid.get(p["sid"], []))
        prev_user = [h for h in times if h < p["ts"]]
        if prev_user and (p["ts"] - prev_user[-1]) <= timedelta(minutes=60):
            continue  # 你们本来就在聊，不算他主动发起
        total += 1
        after = [h for h in times if p["ts"] < h <= p["ts"] + timedelta(hours=24)]
        hit = bool(after)
        replied += 1 if hit else 0
        if hit:
            gaps.append((after[0] - p["ts"]).total_seconds() / 60)
        s = src_of(p["ts"])
        tt = by_src.setdefault(s, [0, 0])
        tt[0] += 1
        tt[1] += 1 if hit else 0
    rr = f"{replied/total*100:.0f}%" if total else "—"
    med = f"{sorted(gaps)[len(gaps)//2]:.0f} 分钟" if gaps else "—"
    print("-- 话题与回复（只算「发起接触」：开口前你已沉默 1 小时以上）--")
    print(f"  主动发起 {total} 条，你 24h 内接了 {replied} 条 → 回复率 {rr}，中位响应 {med}")
    for s, (a, b) in sorted(by_src.items(), key=lambda x: -x[1][0]):
        print(f"    {s}: {b}/{a}")
except Exception as e:
    print(f"[!] conversations 读不到: {e}")

# ---------- 3) 当前快照（不是一周累计，是此刻）----------
print("-- 当前快照 --")
try:
    for key, label in [("resonance.longing", "想念"), ("resonance.regret", "后悔"),
                       ("resonance.dejection", "低落"), ("resonance.playfulness", "促狭")]:
        row = load(LEDGER, "SELECT value FROM source_state WHERE key=?", (key,))
        if not row:
            continue
        d = json.loads(row[0][0])
        print(f"  {label}: {json.dumps(d, ensure_ascii=False)[:220]}")
except Exception as e:
    print(f"  [!] Drive 快照读不到: {e}")
try:
    g = json.loads(load(LEDGER, "SELECT value FROM source_state WHERE key='gate'")[0][0])
    print(f"  开口闸: {json.dumps(g, ensure_ascii=False)[:200]}")
except Exception:
    pass

print("════════")
print("解读提示：命中率骤降=闸太紧或他没料；BLOCK 集中在链内间隔=正常纪律；")
print("话题回复率连续 0=味型有问题（参考 9-1 作业式提问事故）；regret 出现=开口后你 4 小时没理。")
PYEOF
