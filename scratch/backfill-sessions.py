#!/usr/bin/env python3
"""回填 Core 会话库：把 bridge conversations 里 8-07 之后丢失的对话补进
nox-core 的 sessions.db（2026-08-14 修好 sync 后，历史丢失段需要回填）。

v2 关键变更（演练发现）：
- **不能用文本锚点**：Core 的 assistant 文本是流式拼接、bridge 是清理后的，
  两边逐字不一致，精确匹配必然失败。改用**时间锚点**。
- 先删掉我今天（2026-08-14 21:20+）验证写入的测试消息（seq>=63），
  否则会和回填内容重复。

安全：
- 只处理主会话 1809ea16...
- 回填范围：8-07 12:20:52 < timestamp < 8-14 13:20（我测试之前）
- 保留原始 timestamp；只导 content 非空的 user/assistant
- 先备份；幂等；跑完打印按天统计
"""
import sqlite3
import shutil
import sys
from datetime import datetime

BRIDGE_DB = "/root/data/nox-bridge.db"
CORE_DB = "/root/nox-core/data/sessions.db"
SID = "1809ea16d4f74dd4a46a291ce1e4a84b"
CUTOVER_UTC = "2026-08-07T12:20:52"   # Core 原最后一条的时间（丢失起点）
TEST_START_UTC = "2026-08-14T13:20"   # 我今天测试写入的起点，回填到此为止

core = sqlite3.connect(CORE_DB)
core.row_factory = sqlite3.Row

# 1. 删除我今天验证写入的测试消息（seq >= 63，全部是我 curl 造的）
test_rows = core.execute(
    "SELECT seq, role, substr(text,1,30) AS t FROM messages WHERE session_id=? AND seq>=63 "
    "ORDER BY seq", (SID,)).fetchall()
print("== 将删除的测试消息（我今天的验证）==")
for r in test_rows:
    print(f"  seq {r['seq']} {r['role']}: {r['t']}")
if test_rows:
    core.execute("DELETE FROM messages WHERE session_id=? AND seq>=63", (SID,))
    core.commit()
    print(f"已删除 {len(test_rows)} 条测试消息")

# 2. 确认现在 Core 最后一条（应该是 8-07 的毛血旺那条）
last = core.execute(
    "SELECT seq, role, created_at, substr(text,1,30) AS t FROM messages WHERE session_id=? "
    "ORDER BY seq DESC LIMIT 1", (SID,)).fetchone()
print(f"== 清理后 Core 最后一条: seq {last['seq']} {last['created_at']} {last['t']}")

# 3. bridge 里取回填段（时间锚点，排除测试段）
bridge = sqlite3.connect(BRIDGE_DB)
bridge.row_factory = sqlite3.Row
rows = bridge.execute(
    "SELECT role, content, timestamp FROM conversations WHERE id=? "
    "AND timestamp > ? AND timestamp < ? "
    "AND role IN ('user','assistant') AND content IS NOT NULL AND content != '' "
    "ORDER BY rowid", (SID, CUTOVER_UTC, TEST_START_UTC)
).fetchall()
print(f"== bridge 回填段 {CUTOVER_UTC} ~ {TEST_START_UTC}: {len(rows)} 条")

# 4. 幂等：跳过 Core 里已存在的 text（其实这段 Core 应该是空的，但保险）
existing = {r["text"].strip() for r in core.execute(
    "SELECT text FROM messages WHERE session_id=?", (SID,)).fetchall()}
fresh = [r for r in rows if r["content"].strip() not in existing]
print(f"去重后 {len(fresh)} 条")

if not fresh:
    print("无需回填"); sys.exit(0)

# 5. 备份
shutil.copy2(CORE_DB, CORE_DB + ".bak-20260814-backfill")
print(f"已备份 -> {CORE_DB}.bak-20260814-backfill")

# 6. 写入：seq 从 MAX+1 起，created_at 用原始时间
max_seq = core.execute(
    "SELECT COALESCE(MAX(seq), -1) AS s FROM messages WHERE session_id=?", (SID,)).fetchone()["s"]
now = datetime.utcnow().isoformat()
core.execute(
    "INSERT INTO sessions(id, created_at, updated_at) VALUES(?,?,?) "
    "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
    (SID, now, now))
for i, r in enumerate(fresh):
    ts = r["timestamp"]
    try:
        created = datetime.fromisoformat(ts.replace("Z", "+00:00")).isoformat()
    except ValueError:
        created = now
    core.execute(
        "INSERT INTO messages(session_id, seq, role, text, created_at) VALUES(?,?,?,?,?)",
        (SID, max_seq + 1 + i, r["role"], r["content"].strip(), created))
core.commit()
print(f"回填完成：{len(fresh)} 条")

# 7. 按天统计
dist = core.execute(
    "SELECT substr(created_at,1,10) AS d, COUNT(*) AS n FROM messages WHERE session_id=? "
    "GROUP BY d ORDER BY d", (SID,)).fetchall()
print("== 回填后按天分布 ==")
for d in dist:
    print(f"  {d['d']}: {d['n']} 条")
core.close(); bridge.close()
