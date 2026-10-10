"""history_sanitizer —— 用交互契约清洗存量历史（2026-10-10，C2）。

C1 拦住了**新写出来的**工具伪输出；但已经落库的那些还在，他下一轮照样会读到、照着学。
这里用**同一份契约注册表**（自动发现所有登记了 output_contract 的工具）扫存量：

  sessions.db       他看的历史（nox-core）：剥伪输出 + 去掉 sync 竞争造成的成批重复 + 修正摘要水位
  nox-bridge.db     她看的聊天（bridge）：剥伪输出

以后换库、迁移会话，跑一遍就不会把旧污染带回来。

⚠️ 住在仓库根的 scripts/ 而不是 nox-core 里：它要同时改 bridge 的库，是运维工具，
nox-core 本身不许直读 bridge 的库（边界法则 R3）。契约注册表从 nox-core 引用。

用法（在 N150 上，用 nox-core 的 venv）：
  /root/nox-core/.venv/bin/python /root/history_sanitizer.py              # 只出报告，不改
  … --apply             # 改。要求 nox-core / bridge 已停（它们开着库）
  … --rollback <journal.json>
  --drop-test-sessions   顺带删 sessions.db 里 id 以 test- 开头的测试会话

--apply 前把两个库整库备份到 /root/backups/sanitizer/<时间>/，每处改动写进 journal（原值全留），可回滚。
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

#: nox-core 在哪：环境变量 > N150 的线上布局 > 仓库里的相邻目录
_CANDIDATES = [os.environ.get("NOX_CORE_DIR", ""), "/root/nox-core/code",
               str(Path(__file__).resolve().parents[1] / "nox-core")]
ROOT = next(Path(p) for p in _CANDIDATES if p and (Path(p) / "agent" / "contracts.py").exists())
sys.path.insert(0, str(ROOT))

from agent import contracts as contracts_mod  # noqa: E402
from agent.llm import ToolSpec  # noqa: E402
from agent.llm import INNER_ALIASES  # noqa: E402
from data.store import norm_text  # noqa: E402

_INNER_BLOCK_RE = re.compile(
    r"<(" + "|".join(re.escape(a) for a in INNER_ALIASES) + r")>(.*?)</\1>\s*", re.S)

SESSIONS_DB = Path("/root/nox-core/data/sessions.db")
BRIDGE_DB = Path("/root/data/nox-bridge.db")
BACKUP_ROOT = Path("/root/backups/sanitizer")
#: sync 竞争造成的重复是**成批**写入的（同一个 created_at 一次几十上百条）。
#: 只认这种批里的重复 —— 她真的连说两遍「在吗」不会落在同一个时间戳的大批里
BATCH_MIN = 4


# ------------------------------------------------------------------ 契约注册表

def discover_contracts() -> dict[str, contracts_mod.OutputContract]:
    """扫 tools/ 下所有模块里的 ToolSpec，收集登记了契约的。加工具只要登记，这里不用改。"""
    import tools
    found: dict[str, contracts_mod.OutputContract] = {}
    for mod in pkgutil.iter_modules(tools.__path__):
        try:
            m = importlib.import_module(f"tools.{mod.name}")
        except Exception as exc:  # noqa: BLE001 —— 一个工具模块导不进来不该让清洗整个停
            print(f"  ⚠️ tools.{mod.name} 导入失败，跳过：{exc}", file=sys.stderr)
            continue
        for v in vars(m).values():
            if isinstance(v, ToolSpec) and v.output_contract is not None:
                found[v.name] = v.output_contract
    return found


# ------------------------------------------------------------------ 剥伪输出

def clean_text(text: str | None, table: dict[str, contracts_mod.OutputContract],
               voice: bool = False) -> tuple[str | None, list[dict]]:
    """存量文本里的伪输出剥掉。返回 (新文本, 认出的块)；没认出东西就原样返回。

    存量比实时宽一点：bridge 把主动消息压成一行，`[语音，轻轻地]` 跟在句号后面而不在行首。
    所以 marker 紧跟在**句末标点或 `|||`** 后面也算行首（先补一个换行再按契约判）。
    句子中间提到「那个 [语音条] 按钮」前面是普通字，不受影响。
    """
    if not text:
        return text, []
    # 漏进正文的心里话（10-10：`<内心>…</内心>` 整段当成一个气泡发给了她）——
    # 别名表和实时拆标签用的是同一张（agent.llm.INNER_ALIASES）。他的历史里不该有（10-06 她定的），
    # 她的聊天里挪进 metadata.thinking（见 plan_bridge），不删
    inner_arts: list[dict] = []
    blocks = list(_INNER_BLOCK_RE.finditer(text))
    if blocks:
        inner = "\n".join(m.group(2).strip() for m in blocks)
        text_wo = re.sub(r"\n{3,}", "\n\n", _INNER_BLOCK_RE.sub("", text)).strip()
        inner_arts.append({"tool": "inner_voice", "rescuable": False, "text": inner[:200], "inner": inner})
        out2, arts2 = clean_text(text_wo, table, voice) if text_wo else (text_wo, [])
        return out2, inner_arts + arts2
    alt = "|".join(re.escape(m) for m in contracts_mod.all_markers(table))
    ahead = r"[ \t]*(?=(?:" + alt + "))"
    pre = re.sub(r"(?<=[。！？!?…）)\]」])" + ahead, "\n", text)
    pre = re.sub(r"(?<=\|\|\|)" + ahead, "\n", pre)
    out, arts = contracts_mod.apply(pre, contracts=table, allowed=set(table), called=set(), voice=voice)
    arts = [a for a in arts if a.action != "not_allowed"]
    if not arts:
        return text, []
    out = re.sub(r"[ \t]*\|\|\|[ \t]*$", "", out or "", flags=re.M)   # 块前留下的孤零零的 |||
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, [{"tool": a.tool, "rescuable": a.arguments is not None, "text": a.text[:200]} for a in arts]


def is_call(user_text: str) -> bool:
    """这一轮是不是电话：她的话是语音转的，后面带「[audio] vol=…」（两个库都一样）。"""
    t = user_text or ""
    return "\n[audio] vol=" in t or t.startswith("[audio] vol=")


# ------------------------------------------------------------------ sessions.db

def plan_sessions(conn: sqlite3.Connection, table, drop_test: bool) -> dict:
    rows = conn.execute(
        "SELECT id, session_id, seq, role, text, created_at FROM messages ORDER BY session_id, seq"
    ).fetchall()
    batch_size = Counter((r["session_id"], r["created_at"]) for r in rows)
    seen: dict[str, set] = defaultdict(set)
    #: 一批重写 = 前面是重放的旧窗口、**最后几条是那一轮真正的新消息**。
    #: 从头认重复，碰到这一批里第一条没见过的就停 —— 后面的是新话，她又说了一遍「晚安」也照留
    replay_over: set[tuple[str, str]] = set()
    #: 电话那几轮：她的话后面带「[audio] vol=…」—— 他回的带语气标签的英文是正文，草稿判别器不能用
    last_user: dict[str, str] = {}
    edits, deletes = [], []
    for r in rows:
        sid = r["session_id"]
        if r["role"] == "user":
            last_user[sid] = r["text"] or ""
        if drop_test and sid.startswith("test-"):
            deletes.append({"row": dict(r), "why": "test_session"})
            continue
        key = (r["role"], norm_text(r["text"]))
        batch = (sid, r["created_at"])
        if batch_size[batch] >= BATCH_MIN and batch not in replay_over:
            if key in seen[sid]:
                deletes.append({"row": dict(r), "why": "batch_duplicate"})
                continue
            replay_over.add(batch)
        seen[sid].add(key)
        if r["role"] == "assistant":
            new, arts = clean_text(r["text"], table, voice=is_call(last_user.get(sid, "")))
            if arts:
                if new:
                    edits.append({"row": dict(r), "new_text": new, "artifacts": arts})
                else:
                    deletes.append({"row": dict(r), "why": "artifact_only", "artifacts": arts})
    deleted_ids = {d["row"]["id"] for d in deletes}
    # 摘要水位是「已经折进摘要的消息条数」（按 seq 正序数），删行之后要跟着改
    watermarks = []
    for s in conn.execute("SELECT id, summary_upto FROM sessions WHERE summary_upto > 0").fetchall():
        ids = [r["id"] for r in rows if r["session_id"] == s["id"]][: s["summary_upto"]]
        new_upto = sum(1 for i in ids if i not in deleted_ids)
        if new_upto != s["summary_upto"]:
            watermarks.append({"session_id": s["id"], "old": s["summary_upto"], "new": new_upto})
    test_sessions = sorted({d["row"]["session_id"] for d in deletes if d["why"] == "test_session"})
    return {"edits": edits, "deletes": deletes, "watermarks": watermarks, "drop_sessions": test_sessions}


def apply_sessions(conn, plan):
    for e in plan["edits"]:
        conn.execute("UPDATE messages SET text = ? WHERE id = ?", (e["new_text"], e["row"]["id"]))
    conn.executemany("DELETE FROM messages WHERE id = ?", [(d["row"]["id"],) for d in plan["deletes"]])
    for w in plan["watermarks"]:
        conn.execute("UPDATE sessions SET summary_upto = ? WHERE id = ?", (w["new"], w["session_id"]))
    for sid in plan["drop_sessions"]:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        plan.setdefault("dropped_session_rows", []).append(dict(row) if row else None)
        conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))


def rollback_sessions(conn, plan):
    for row in plan.get("dropped_session_rows", []):
        if row:
            cols = ",".join(row)
            conn.execute(f"INSERT OR REPLACE INTO sessions({cols}) VALUES({','.join('?' * len(row))})", list(row.values()))
    for d in plan["deletes"]:
        r = d["row"]
        conn.execute("INSERT INTO messages(id, session_id, seq, role, text, created_at) VALUES(?,?,?,?,?,?)",
                     (r["id"], r["session_id"], r["seq"], r["role"], r["text"], r["created_at"]))
    for e in plan["edits"]:
        conn.execute("UPDATE messages SET text = ? WHERE id = ?", (e["row"]["text"], e["row"]["id"]))
    for w in plan["watermarks"]:
        conn.execute("UPDATE sessions SET summary_upto = ? WHERE id = ?", (w["old"], w["session_id"]))


# ------------------------------------------------------------------ nox-bridge.db

#: metadata 里能在聊天里显示出东西的键 —— 正文剥空后只要还有这些，气泡留着；否则整条删
_RENDERABLE = ("attachments", "images", "image", "voice", "memes", "meme", "music", "order", "task", "card")


def plan_bridge(conn, table) -> dict:
    """⚠️ 她的聊天按 metadata.segments 一段一个气泡渲染 —— content 和 segments 两份都要清。"""
    edits, deletes = [], []
    last_user: dict[str, str] = {}
    for r in conn.execute("SELECT rowid AS rid, id, role, content, timestamp, metadata FROM conversations "
                          "ORDER BY rowid").fetchall():
        if r["role"] == "user":
            last_user[r["id"]] = r["content"] or ""
            continue
        call = is_call(last_user.get(r["id"], ""))
        new, arts = clean_text(r["content"], table, voice=call)
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
        except ValueError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        changed = dict(meta)
        if isinstance(meta.get("segments"), list):
            segs, seg_arts = [], []
            for s in meta["segments"]:
                ns, a = clean_text(s, table, voice=call) if isinstance(s, str) else (s, [])
                seg_arts += a
                if ns:
                    segs.append(ns)
            if seg_arts:
                arts = arts or seg_arts
                changed["segments"] = segs
        # 漏进正文的心里话挪到思考区（界面按 metadata.thinking 显示成可展开的思考块），不删
        inner = "\n".join(a["inner"] for a in arts if a.get("tool") == "inner_voice")
        if inner and not meta.get("thinking"):
            changed["thinking"] = inner
        new_meta = json.dumps(changed, ensure_ascii=False) if changed != meta else None
        if not arts:
            continue
        renderable = bool(changed.get("thinking")) or any(meta.get(k) for k in _RENDERABLE)
        if new or renderable:
            edits.append({"row": dict(r), "new_text": new or "", "new_meta": new_meta, "artifacts": arts})
        else:
            deletes.append({"row": dict(r), "why": "artifact_only", "artifacts": arts})
    return {"edits": edits, "deletes": deletes}


def apply_bridge(conn, plan):
    for e in plan["edits"]:
        conn.execute("UPDATE conversations SET content = ? WHERE rowid = ?", (e["new_text"], e["row"]["rid"]))
        if e.get("new_meta") is not None:
            conn.execute("UPDATE conversations SET metadata = ? WHERE rowid = ?", (e["new_meta"], e["row"]["rid"]))
    conn.executemany("DELETE FROM conversations WHERE rowid = ?", [(d["row"]["rid"],) for d in plan["deletes"]])


def rollback_bridge(conn, plan):
    for d in plan["deletes"]:
        r = d["row"]
        conn.execute("INSERT INTO conversations(rowid, id, role, content, timestamp, metadata) VALUES(?,?,?,?,?,?)",
                     (r["rid"], r["id"], r["role"], r["content"], r["timestamp"], r["metadata"]))
    for e in plan["edits"]:
        conn.execute("UPDATE conversations SET content = ?, metadata = ? WHERE rowid = ?",
                     (e["row"]["content"], e["row"]["metadata"], e["row"]["rid"]))


# ------------------------------------------------------------------ 主流程

def _open(p: Path) -> sqlite3.Connection:
    c = sqlite3.connect(p, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _active(unit: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False)
    return r.returncode == 0


def _report(sp, bp) -> None:
    why = Counter(d["why"] for d in sp["deletes"])
    print(f"sessions.db：改 {len(sp['edits'])} 条 · 删 {len(sp['deletes'])} 条 {dict(why)} · "
          f"修水位 {len(sp['watermarks'])} 个会话 · 删测试会话 {sp['drop_sessions']}")
    print(f"nox-bridge.db：改 {len(bp['edits'])} 条 · 删 {len(bp['deletes'])} 条")
    tools_hit = Counter(a["tool"] for x in sp["edits"] + bp["edits"] for a in x["artifacts"])
    print(f"认出的伪输出按工具：{dict(tools_hit)}")
    for e in (sp["edits"] + bp["edits"])[:6]:
        old = (e["row"].get("text") or e["row"].get("content") or "").replace("\n", " ⏎ ")
        print(f"  - {old[:90]}\n    → {e['new_text'].replace(chr(10), ' ⏎ ')[:90]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", type=Path)
    ap.add_argument("--drop-test-sessions", action="store_true")
    ap.add_argument("--sessions-db", type=Path, default=SESSIONS_DB)
    ap.add_argument("--bridge-db", type=Path, default=BRIDGE_DB)
    ap.add_argument("--force", action="store_true", help="服务开着也照改（只给测试用）")
    a = ap.parse_args()

    if (a.apply or a.rollback) and not a.force:
        running = [u for u in ("nox-core", "bridge") if _active(u)]
        if running:
            print(f"ERROR: {running} 还在跑（它们开着库，改了会被覆盖或锁住）。先 systemctl stop。")
            return 1

    if a.rollback:
        j = json.loads(a.rollback.read_text(encoding="utf-8"))
        with _open(Path(j["sessions_db"])) as c:
            rollback_sessions(c, j["sessions"])
        with _open(Path(j["bridge_db"])) as c:
            rollback_bridge(c, j["bridge"])
        print(f"已按 {a.rollback} 回滚")
        return 0

    table = discover_contracts()
    print(f"契约注册表：{ {k: v.markers for k, v in table.items()} }")
    with _open(a.sessions_db) as c:
        sp = plan_sessions(c, table, a.drop_test_sessions)
    with _open(a.bridge_db) as c:
        bp = plan_bridge(c, table)
    _report(sp, bp)
    if not a.apply:
        print("（dry-run，没改任何东西。加 --apply 才改）")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bdir = BACKUP_ROOT / stamp
    bdir.mkdir(parents=True, exist_ok=True)
    for src in (a.sessions_db, a.bridge_db):
        with _open(src) as s, sqlite3.connect(bdir / src.name) as d:
            s.backup(d)
    with _open(a.sessions_db) as c:
        apply_sessions(c, sp)
    with _open(a.bridge_db) as c:
        apply_bridge(c, bp)
    journal = bdir / "journal.json"
    journal.write_text(json.dumps({"sessions_db": str(a.sessions_db), "bridge_db": str(a.bridge_db),
                                   "sessions": sp, "bridge": bp}, ensure_ascii=False, default=str),
                       encoding="utf-8")
    print(f"已改。整库备份 + journal：{bdir}")
    print(f"回滚：{sys.executable} {Path(__file__).resolve()} --rollback {journal}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
