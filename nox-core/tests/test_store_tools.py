"""历史里留着工具调用（2026-10-24，她：「做，压缩之后不需要工具细节」）。

10-23 查出来的根因：库里只存正文，每次重启 / 跨天 / 缓存过期从库里重读，他眼前一百多条历史里一次真工具调用都没有，
模型照着前文「用嘴说」，把调用写成 <desk_open><url>… 这种字。同一个模型在 DSH / Codex 里不这样 —— 那边历史留着结构化调用。

守的几条：
  · 🔴 落库再读回来（真的那条路：Sessions.put → 缓存过期 → Sessions.get → load_window）：工具调用还原成真的 tool_calls / tool_results
  · 🔴 行数一条不多：水位、锚点、条数比对都不受影响
  · 🔴 水位之前（已经压进摘要）的不带工具细节
  · 读回来的结果标明「之前那次」；参数 / 结果只留开头
  · 一轮分两次落库：第一次那几行事后补上附注
  · 老库补列不炸；读回来的历史过得了 OpenAI 兼容的转换（调用和结果按 id 配得上）
"""

from __future__ import annotations

import json

from agent.adapters import OpenAICompatAdapter
from agent.llm import Message, ToolCall, ToolResult
from config import LLMConfig
from data.store import STALE_PREFIX, Store, tool_steps_after


def _call(i, tool, **args):
    return ToolCall(id=f"c{i}", name=tool, arguments=args)


def _turn():
    """她让我开个网页：先说一句、开网页、再读一段，最后回她；下一轮只发了个表情（没正文）"""
    return [
        Message(role="user", text="帮我看看 GreenCloud"),
        Message(role="assistant", text="等我一下。", tool_calls=[_call(1, "desk_open", url="https://greencloudvps.com", doing="在查 VPS")]),
        Message(role="tool_results", tool_results=[ToolResult("c1", "【GreenCloud】https://greencloudvps.com\n正文……")]),
        Message(role="assistant", text=None, tool_calls=[_call(2, "desk_read", part=2)]),
        Message(role="tool_results", tool_results=[ToolResult("c2", "第 2 段" + "很长" * 300)]),
        Message(role="assistant", text="东京 Budget KVM 有货，$25/年。"),
        Message(role="user", text="好"),
        Message(role="assistant", text=None, tool_calls=[_call(3, "send_meme", name="摸摸头")]),
        Message(role="tool_results", tool_results=[ToolResult("c3", "发出去了")]),
    ]


def _shape(msgs):
    out = []
    for m in msgs:
        if m.role == "tool_results":
            out.append(("tool_results", [r.content[:len(STALE_PREFIX)] == STALE_PREFIX for r in m.tool_results]))
        else:
            out.append((m.role, (m.text or "").split("）\n")[-1] or None, [c.name for c in m.tool_calls]))
    return out


def test_红线_落库读回来_工具调用还原成真的_行数一条不多(tmp_path):
    db = Store(tmp_path / "s.db")
    db.sync("s1", _turn())
    assert db.count("s1") == 4, "行数和以前一样：只有有正文的四条"
    back = db.load_window("s1", 0)
    assert _shape(back) == [
        ("user", "帮我看看 GreenCloud", []),
        ("assistant", "等我一下。", ["desk_open", "desk_read"]),
        ("tool_results", [True, True]),
        ("assistant", "东京 Budget KVM 有货，$25/年。", []),
        ("user", "好", []),
        ("assistant", None, ["send_meme"]),
        ("tool_results", [True]),
    ]
    calls = back[1].tool_calls
    assert calls[0].arguments == {"url": "https://greencloudvps.com", "doing": "在查 VPS"}
    long = back[2].tool_results[1].content
    assert long.startswith(STALE_PREFIX + "第 2 段") and long.endswith("…") and len(long) < 260, "结果只留开头"


def test_红线_真的那条路_缓存过期从库里重读_还带着工具(tmp_path):
    from api.server import Sessions
    db = Store(tmp_path / "s.db")
    s = Sessions(db, history_limit=40, cap_tokens=100_000)
    s.put("s1", _turn())
    s._cache.clear()            # 重启 / 跨天 / 快照过期 —— 都是这一步
    got = s.get("s1")
    assert any(m.tool_calls and m.tool_calls[0].name == "desk_open" for m in got)
    assert sum(m.role == "tool_results" for m in got) == 2


def test_红线_压进摘要的那段不带工具细节(tmp_path):
    db = Store(tmp_path / "s.db")
    db.sync("s1", _turn())
    back = db.load_window("s1", 3)    # 水位 3：前三条已经压进摘要
    assert [m.role for m in back] == ["user", "assistant", "tool_results"]
    assert back[0].text.endswith("好") and back[1].tool_calls[0].name == "send_meme"
    assert all("desk_open" not in json.dumps([c.name for c in m.tool_calls]) for m in back)
    full = db.load_full("s1")
    assert all(not m.tool_calls and m.role != "tool_results" for m in full), "压缩看的全文只有正文"


def test_一轮分两次落库_第一次那几行事后补上附注(tmp_path):
    db = Store(tmp_path / "s.db")
    t = _turn()
    db.sync("s1", t[:2])          # 第一次：她那句 + 他先说的「等我一下」（还没调工具的样子）
    db.sync("s1", t)              # 第二次：整轮
    assert db.count("s1") == 4
    back = db.load_window("s1", 0)
    assert [c.name for c in back[1].tool_calls] == ["desk_open", "desk_read"]


def test_老库补列_旧行没有附注(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, title TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, seq INTEGER NOT NULL,
                               role TEXT NOT NULL, text TEXT, created_at TEXT NOT NULL);
        INSERT INTO sessions VALUES ('s1', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00', NULL);
        INSERT INTO messages(session_id, seq, role, text, created_at) VALUES ('s1', 0, 'user', '在吗', '2026-10-01T00:00:00+00:00');
    """)
    con.commit()
    con.close()
    back = Store(p).load_window("s1", 0)
    assert [m.role for m in back] == ["user"] and not back[0].tool_calls


def test_读回来的历史过得了OpenAI兼容的转换_调用和结果配得上(tmp_path):
    db = Store(tmp_path / "s.db")
    db.sync("s1", _turn())
    a = OpenAICompatAdapter.__new__(OpenAICompatAdapter)
    a.cfg = LLMConfig(provider="openai_compat", model="deepseek-chat", base_url="", api_key="")
    native = [x for m in db.load_window("s1", 0) for x in a._to_native(m)]
    asked = [tc["id"] for x in native if x["role"] == "assistant" for tc in x.get("tool_calls") or []]
    answered = [x["tool_call_id"] for x in native if x["role"] == "tool"]
    assert asked == answered and len(asked) == 3


def test_附注怎么分():
    t = _turn()
    steps = tool_steps_after(t)
    assert [s["name"] for s in steps[id(t[1])]] == ["desk_open", "desk_read"], "纯调用记在前面那条有正文的名下"
    assert [s["name"] for s in steps[id(t[6])]] == ["send_meme"], "这一轮只有调用：记在她那句名下"
    assert id(t[0]) not in steps and id(t[5]) not in steps
