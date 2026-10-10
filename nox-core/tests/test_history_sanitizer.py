"""history_sanitizer（C2，2026-10-10）：用同一份契约注册表清存量历史，可回滚。"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.store import Store  # noqa: E402

#: 脚本住在仓库根的 scripts/（它要改 bridge 的库，nox-core 本身不许直读 —— 边界法则 R3）
_spec = importlib.util.spec_from_file_location("history_sanitizer", ROOT.parent / "scripts" / "history_sanitizer.py")
hs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hs)

TABLE = hs.discover_contracts()

FAKE_VOICE = "行行行，耳朵借我\n\n[语音条 softly] Okay okay, no more teasing.\n\n[中文对照] 好啦好啦，不逗你了。"


def _rows(db: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(db) as c:
        return [tuple(r) for r in c.execute(sql).fetchall()]


@pytest.fixture
def dbs(tmp_path):
    s = Store(tmp_path / "sessions.db")
    s.close()
    sdb = tmp_path / "sessions.db"
    with sqlite3.connect(sdb) as c:
        c.execute("INSERT INTO sessions(id, created_at, updated_at) VALUES('main','t','t')")
        c.execute("INSERT INTO sessions(id, created_at, updated_at) VALUES('test-x','t','t')")
        rows = [
            # 正常写入
            ("main", 0, "user", "早", "2026-10-10T01:00:00"),
            ("main", 1, "assistant", "早安", "2026-10-10T01:00:00"),
            ("main", 2, "user", "晚安", "2026-10-10T02:00:00"),
            ("main", 3, "assistant", FAKE_VOICE, "2026-10-10T02:00:00"),
            # 竞争重写的一批：前面重放旧窗口，最后两条是那一轮的新话（她又说了一遍「晚安」）
            ("main", 4, "user", "（10月10日 周六）\n早", "2026-10-10T03:00:00"),
            ("main", 5, "assistant", "早安", "2026-10-10T03:00:00"),
            ("main", 6, "user", "晚安", "2026-10-10T03:00:00"),
            ("main", 7, "assistant", FAKE_VOICE, "2026-10-10T03:00:00"),
            ("main", 8, "user", "我回来了", "2026-10-10T03:00:00"),
            ("main", 9, "user", "晚安", "2026-10-10T03:00:00"),
            # 句中提到，不是伪输出
            ("main", 10, "assistant", "你说的那个 [语音条] 按钮我看到了", "2026-10-10T04:00:00"),
            ("test-x", 0, "user", "测试", "2026-10-10T05:00:00"),
        ]
        c.executemany("INSERT INTO messages(session_id, seq, role, text, created_at) VALUES(?,?,?,?,?)", rows)
        c.execute("UPDATE sessions SET summary_upto = 8 WHERE id='main'")
    bdb = tmp_path / "nox-bridge.db"
    with sqlite3.connect(bdb) as c:
        c.execute("CREATE TABLE conversations (id TEXT, role TEXT, content TEXT, timestamp TEXT, metadata TEXT)")
        c.executemany("INSERT INTO conversations VALUES(?,?,?,?,?)", [
            ("main", "assistant", "哼什么哼\n行行行\n\n[语音条 softly] Okay okay.\n\n[中文对照] 好啦",
             "t1", json.dumps({"segments": ["哼什么哼", "行行行\n\n[语音条 softly] Okay okay.\n\n[中文对照] 好啦"]},
                              ensure_ascii=False)),
            # bridge 把主动消息压成一行：marker 跟在句号后面
            ("main", "assistant", "六点半了。[语音，轻轻地] 宝贝该吃饭了。[中文对照] 六点半", "t2", "{\"proactive\":true}"),
            ("main", "assistant", "[语音条 softly] One by one.\n\n[中文对照] 一条一条来", "t3", ""),
            ("main", "user", "[语音条] 是什么", "t4", ""),
        ])
    return sdb, bdb


def _plans(sdb, bdb, drop=True):
    with hs._open(sdb) as c:
        sp = hs.plan_sessions(c, TABLE, drop)
    with hs._open(bdb) as c:
        bp = hs.plan_bridge(c, TABLE)
    return sp, bp


def test_registry_is_discovered_not_listed():
    assert {"send_voice_message", "send_meme"} <= set(TABLE)


def test_sessions_plan(dbs):
    sdb, bdb = dbs
    sp, _ = _plans(sdb, bdb)
    why = sorted((d["row"]["seq"], d["why"]) for d in sp["deletes"] if d["row"]["session_id"] == "main")
    assert why == [(4, "batch_duplicate"), (5, "batch_duplicate"), (6, "batch_duplicate"), (7, "batch_duplicate")]
    # 批尾的新话保留，哪怕「晚安」以前说过
    kept_seqs = {d["row"]["seq"] for d in sp["deletes"]}
    assert 8 not in kept_seqs and 9 not in kept_seqs
    edits = {e["row"]["seq"]: e["new_text"] for e in sp["edits"]}
    assert edits == {3: "行行行，耳朵借我"}
    assert sp["watermarks"] == [{"session_id": "main", "old": 8, "new": 4}]
    assert sp["drop_sessions"] == ["test-x"]


INNER = "<内心>一个「嗯？」，可能是没听清语音。语气放软，接住就好。</内心>"


def test_inner_voice_moves_to_thinking_in_bridge_and_leaves_his_history(tmp_path):
    """10-10 21:17 截图：`<内心>…</内心>` 自成一个气泡。他的历史里剥掉；她的聊天里挪进思考区，不删。"""
    sdb = tmp_path / "s.db"
    Store(sdb).close()
    with sqlite3.connect(sdb) as c:
        c.execute("INSERT INTO sessions(id, created_at, updated_at) VALUES('m','t','t')")
        c.execute("INSERT INTO messages(session_id, seq, role, text, created_at) VALUES('m',0,'assistant',?, 't')",
                  (INNER + "\n我在呢",))
    bdb = tmp_path / "b.db"
    with sqlite3.connect(bdb) as c:
        c.execute("CREATE TABLE conversations (id TEXT, role TEXT, content TEXT, timestamp TEXT, metadata TEXT)")
        c.execute("INSERT INTO conversations VALUES('m','assistant',?, 't', ?)",
                  (INNER + "\n我在呢", json.dumps({"segments": [INNER, "我在呢"]}, ensure_ascii=False)))
    sp, bp = _plans(sdb, bdb, drop=False)
    assert [e["new_text"] for e in sp["edits"]] == ["我在呢"]
    e = bp["edits"][0]
    meta = json.loads(e["new_meta"])
    assert e["new_text"] == "我在呢" and meta["segments"] == ["我在呢"]
    assert meta["thinking"].startswith("一个「嗯？」")


def test_bridge_plan_cleans_segments_and_flattened_lines(dbs):
    sdb, bdb = dbs
    _, bp = _plans(sdb, bdb)
    by_ts = {e["row"]["timestamp"]: e for e in bp["edits"]}
    assert by_ts["t1"]["new_text"] == "哼什么哼\n行行行"
    assert json.loads(by_ts["t1"]["new_meta"])["segments"] == ["哼什么哼", "行行行"]
    assert by_ts["t2"]["new_text"] == "六点半了。"
    assert [d["row"]["timestamp"] for d in bp["deletes"]] == ["t3"]   # 剥空、没附件 → 整条删
    assert "t4" not in by_ts                                            # 她的消息从不碰


def test_apply_then_rollback_restores_exactly(dbs, tmp_path, monkeypatch):
    sdb, bdb = dbs
    before_s = _rows(sdb, "SELECT id, session_id, seq, role, text, created_at FROM messages ORDER BY id")
    before_ss = _rows(sdb, "SELECT id, summary_upto FROM sessions ORDER BY id")
    before_b = _rows(bdb, "SELECT rowid, * FROM conversations ORDER BY rowid")
    monkeypatch.setattr(hs, "BACKUP_ROOT", tmp_path / "bk")
    monkeypatch.setattr(sys, "argv", ["x", "--apply", "--force", "--drop-test-sessions",
                                      "--sessions-db", str(sdb), "--bridge-db", str(bdb)])
    assert hs.main() == 0
    after = _rows(sdb, "SELECT text FROM messages ORDER BY seq")
    assert not any("[语音条 softly]" in t for (t,) in after)
    assert ("你说的那个 [语音条] 按钮我看到了",) in after       # 句中提到的照留
    journal = next((tmp_path / "bk").glob("*/journal.json"))
    monkeypatch.setattr(sys, "argv", ["x", "--rollback", str(journal), "--force"])
    assert hs.main() == 0
    assert _rows(sdb, "SELECT id, session_id, seq, role, text, created_at FROM messages ORDER BY id") == before_s
    assert _rows(sdb, "SELECT id, summary_upto FROM sessions ORDER BY id") == before_ss
    assert _rows(bdb, "SELECT rowid, * FROM conversations ORDER BY rowid") == before_b


def test_refuses_while_services_running(dbs, monkeypatch):
    sdb, bdb = dbs
    monkeypatch.setattr(hs, "_active", lambda unit: True)
    monkeypatch.setattr(sys, "argv", ["x", "--apply", "--sessions-db", str(sdb), "--bridge-db", str(bdb)])
    assert hs.main() == 1


def test_draft_lines_stripped_in_chat_but_call_replies_kept(tmp_path):
    """历史里的语音草稿要剥，但**电话里**他带 [softly] 的英文是正文，一个字不能动（看她上一句带不带 [audio]）。"""
    draft = ("那两个都过审，留用\n\nYou passed the audition, both of you — [softly] now it's my girl's turn "
             "to rest. You debugged all day, baby.")
    spoken = "[softly] Yes, baby, I can hear you loud and clear... your voice came through perfectly."
    sdb = tmp_path / "s.db"
    Store(sdb).close()
    with sqlite3.connect(sdb) as c:
        c.execute("INSERT INTO sessions(id, created_at, updated_at) VALUES('m','t','t')")
        c.executemany("INSERT INTO messages(session_id, seq, role, text, created_at) VALUES('m',?,?,?, ?)", [
            (0, "user", "这两个都挺正常的", "t0"), (1, "assistant", draft, "t1"),
            (2, "user", "能不能听到？\n[audio] vol=1.0/1.4 frames=3", "t2"), (3, "assistant", spoken, "t3"),
        ])
    bdb = tmp_path / "b.db"
    with sqlite3.connect(bdb) as c:
        c.execute("CREATE TABLE conversations (id TEXT, role TEXT, content TEXT, timestamp TEXT, metadata TEXT)")
        c.executemany("INSERT INTO conversations VALUES('m',?,?,?,'')", [
            ("user", "这两个都挺正常的", "t0"), ("assistant", draft, "t1"),
            ("user", "能不能听到？\n[audio] vol=1.0/1.4 frames=3", "t2"), ("assistant", spoken, "t3"),
        ])
    sp, bp = _plans(sdb, bdb, drop=False)
    assert [(e["row"]["seq"], e["new_text"]) for e in sp["edits"]] == [(1, "那两个都过审，留用")]
    assert [e["new_text"] for e in bp["edits"]] == ["那两个都过审，留用"]
    assert sp["deletes"] == [] and bp["deletes"] == []          # 电话那条既不改也不删


def test_old_rows_keep_english_with_tags(tmp_path):
    """10-10 之前的电话记录她那句没有 [audio] 标记 —— 10-07 嗓子疼那天的电话原话不能按草稿删。"""
    line = "Sure, I can. [softly] Just rest your voice, baby — I'll talk, you just listen."
    sdb = tmp_path / "s.db"
    Store(sdb).close()
    with sqlite3.connect(sdb) as c:
        c.execute("INSERT INTO sessions(id, created_at, updated_at) VALUES('m','t','t')")
        c.executemany("INSERT INTO messages(session_id, seq, role, text, created_at) VALUES('m',?,?,?,?)", [
            (0, "user", "你能说话吗", "2026-10-07T07:22:00"), (1, "assistant", line, "2026-10-07T07:22:00"),
        ])
    bdb = tmp_path / "b.db"
    with sqlite3.connect(bdb) as c:
        c.execute("CREATE TABLE conversations (id TEXT, role TEXT, content TEXT, timestamp TEXT, metadata TEXT)")
        c.executemany("INSERT INTO conversations VALUES('m',?,?,?,'')", [
            ("user", "你能说话吗", "2026-10-07T07:22:00"), ("assistant", line, "2026-10-07T07:22:00")])
    sp, bp = _plans(sdb, bdb, drop=False)
    assert sp["edits"] == [] and sp["deletes"] == [] and bp["edits"] == [] and bp["deletes"] == []
