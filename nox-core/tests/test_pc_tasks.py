"""把活转给电脑上的 Claude Code（tools/pc_tasks.py）。ssh 打桩，不碰真电脑。"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from tools import context as tool_context
from tools import pc_tasks
from tools.pc_tasks import PcTasks, her_words

#: 电脑侧 relay.ps1 认的 id 形状 —— 两边必须对得上
RELAY_ID = re.compile(r"^[a-z0-9][a-z0-9-]{5,39}$")


class FakePc:
    user = "14372"

    def __init__(self, ip="192.168.1.19", wakes_to=None):
        self.ip = ip
        self.wakes_to = wakes_to
        self.woken = 0

    def find_ip(self):
        return self.ip

    def wake(self):
        self.woken += 1
        self.ip = self.wakes_to


class FakeSsh:
    """记下每次 ssh 的动词和 stdin；status 的回包按 states 里写的给。"""

    def __init__(self):
        self.calls: list[tuple[str, bytes]] = []
        self.states: dict[str, dict] = {}
        self.ship_reply: dict | None = None

    def verbs(self, head):
        return [v for v, _ in self.calls if v.split()[0] == head]

    def __call__(self, argv, input=b"", **kw):
        verb = argv[-1]
        self.calls.append((verb, input))
        parts = verb.split()
        if parts[0] == "start":
            out = {"ok": True, "id": parts[1], "repo": parts[2]}
        elif parts[0] == "status":
            out = self.states.get(parts[1], {"ok": True, "state": {"id": parts[1], "state": "running"}})
        elif parts[0] == "cancel":
            out = {"ok": True, "state": "cancelled"}
        elif parts[0] == "ship":
            out = self.ship_reply or {"ok": True, "id": parts[1], "state": "queued"}
        else:
            out = {"ok": True, "items": []}
        return subprocess.CompletedProcess(argv, 0, ("﻿" + json.dumps(out, ensure_ascii=False)).encode(), b"")


class Book:
    def __init__(self):
        self.notes: list[tuple[str, str]] = []

    def add_routine(self, sid, why, now=None):
        self.notes.append((sid, why))


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 27, 13, 30, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    ssh, clock = FakeSsh(), Clock()
    t = PcTasks(FakePc(), tmp_path / "pc-tasks.json", run=ssh, now=clock)
    return t, ssh, clock


DONE = lambda jid: {"ok": True, "state": {"id": jid, "repo": "caelum", "state": "done"},  # noqa: E731
                    "result": {"summary": "加好了，测试也跑了。", "commits": ["abc 加按钮"],
                               "branch": f"nox/{jid}", "diffstat": "1 file changed", "uncommitted": 0}}


def test_她的原话才算():
    assert her_words("帮我把 Moments 的按钮改大点")
    assert not her_words("")
    assert not her_words("   ")
    assert not her_words("（系统提示：不是她在跟你说话。到点了")
    assert not her_words("(系统提示：这不是聊天窗口")


def test_不是她亲口交代的不转(env):
    t, ssh, _ = env
    out = t.start("改个按钮", "caelum", "s1", "（系统提示：不是她在跟你说话。")
    assert "不转" in out and ssh.calls == []


def test_转活_中文按UTF8原样送过去_id电脑侧认得(env):
    t, ssh, _ = env
    out = t.start("把 Moments 页的发布按钮改成圆的", "caelum", "s1", "让电脑上的 Claude 改一下按钮")
    verb, stdin = ssh.calls[-1]
    _, jid, repo = verb.split()
    assert repo == "caelum" and RELAY_ID.match(jid)
    assert stdin.decode("utf-8") == "把 Moments 页的发布按钮改成圆的"
    assert jid in out and "别说已经做好了" in out
    saved = json.loads(t.path.read_text(encoding="utf-8"))
    assert saved[jid]["session_id"] == "s1" and saved[jid]["notified"] is False


def test_仓库只能是那两个(env):
    t, ssh, _ = env
    assert "repo 只能是" in t.start("删掉 C 盘", "windows", "s1", "做吧")
    assert ssh.calls == []


def test_电脑关着先开机_叫不醒就不交(tmp_path):
    ssh = FakeSsh()
    pc = FakePc(ip=None, wakes_to=None)
    t = PcTasks(pc, tmp_path / "j.json", run=ssh)
    out = t.start("改按钮", "caelum", "s1", "做吧")
    assert pc.woken == 1 and "没交出去" in out and ssh.calls == []


def test_电脑关着先开机_醒了就交(tmp_path):
    ssh = FakeSsh()
    pc = FakePc(ip=None, wakes_to="192.168.1.19")
    t = PcTasks(pc, tmp_path / "j.json", run=ssh)
    assert "交给电脑上的 Claude Code 了" in t.start("改按钮", "caelum", "s1", "做吧")
    assert pc.woken == 1


def test_做完了才放纸条_只放一次(env):
    t, ssh, clock = env
    t.start("改按钮", "caelum", "s1", "做吧")
    jid = ssh.calls[-1][0].split()[1]
    book = Book()
    assert t.poll(book) == [] and book.notes == []          # 还在跑
    ssh.states[jid] = DONE(jid)
    clock.t += timedelta(minutes=1)
    assert t.poll(book) == []                                # 两分钟内不再问
    clock.t += timedelta(minutes=2)
    assert t.poll(book) == [jid]
    sid, why = book.notes[0]
    assert sid == "s1" and "加好了" in why and "上线得她亲口说" in why
    # 结论写死在第一句 + 告诉她去哪看（10-27 第一个真活他说成「差不多了吧」、也没说在哪）
    assert why.splitlines()[0].endswith("**已经做完了**（不是「差不多」，是做完了、提交了）。")
    assert f"nox/{jid}" in why and "还没合进主线" in why
    clock.t += timedelta(minutes=3)
    assert t.poll(book) == [] and len(book.notes) == 1      # 不重复叫醒


def test_电脑关着不问_下回接上(env):
    t, ssh, clock = env
    t.start("改按钮", "caelum", "s1", "做吧")
    jid = ssh.calls[-1][0].split()[1]
    ssh.states[jid] = DONE(jid)
    t.pc.ip = None
    book = Book()
    assert t.poll(book) == [] and book.notes == []
    t.pc.ip = "192.168.1.19"
    clock.t += timedelta(minutes=3)
    assert t.poll(book) == [jid]


def test_没做成也要告诉她(env):
    t, ssh, clock = env
    t.start("改按钮", "caelum", "s1", "做吧")
    jid = ssh.calls[-1][0].split()[1]
    ssh.states[jid] = {"ok": True, "state": {"id": jid, "state": "failed"},
                       "result": {"summary": "没跑完：超过 60 分钟", "is_error": True}}
    book = Book()
    assert t.poll(book) == [jid]
    assert "没做成" in book.notes[0][1] and "60 分钟" in book.notes[0][1]
    assert "**没做成**" in book.notes[0][1].splitlines()[0] and "还没合进主线" not in book.notes[0][1]


def test_看进度带上总结(env):
    t, ssh, _ = env
    ssh.states["1027-2130-ab12"] = DONE("1027-2130-ab12")
    out = t.check("1027-2130-ab12")
    assert "做完了" in out and "加好了" in out and "1 个提交" in out


def _handlers(t):
    hs = {}

    class Loop:
        def register(self, spec, h): hs[spec.name] = h

    pc_tasks.register_all(Loop(), t)
    return hs


def test_工具层_看这一轮的原话(env):
    t, ssh, _ = env
    hs = _handlers(t)
    with tool_context.bind(tool_context.ToolContext(session_id="s9", user_text="（系统提示：到点了")):
        assert "不转" in hs["pc_task_start"]({"task": "改按钮", "repo": "caelum"})
    assert ssh.calls == []
    with tool_context.bind(tool_context.ToolContext(session_id="s9", user_text="帮我改按钮")):
        assert "交给电脑" in hs["pc_task_start"]({"task": "改按钮", "repo": "caelum"})
    assert json.loads(t.path.read_text(encoding="utf-8")).popitem()[1]["session_id"] == "s9"


def test_工具层_测试会话不转(env, monkeypatch):
    t, ssh, _ = env
    monkeypatch.setattr("config.is_test_session", lambda sid: True)
    with tool_context.bind(tool_context.ToolContext(session_id="test-x", user_text="帮我改按钮")):
        assert "测试会话" in _handlers(t)["pc_task_start"]({"task": "改按钮", "repo": "caelum"})
    assert ssh.calls == []


def test_声明():
    assert pc_tasks.START_SPEC.side_effect == "write" and pc_tasks.START_SPEC.confirm_via
    assert pc_tasks.CHECK_SPEC.side_effect == "read"
    assert set(pc_tasks.START_SPEC.parameters["properties"]["repo"]["enum"]) == {"caelum", "nox-app"}


# ---------- 接线：60 秒的快循环里收尾就当场叫醒他（10-27 第一个真活晚了十分钟才被发现） ----------

def _attention(tmp_path):
    from attention.relationship import RelationshipState
    from attention.service import AttentionService
    from attention.store import AttentionStore

    class Provider:
        def get_state(self, turn=None, force_refresh=False):
            return {"has_data": False}

    store = AttentionStore(tmp_path / "attn.db")
    woke = []
    svc = AttentionService(store, Provider(), RelationshipState(), fast_sources=[],
                           waker=lambda book, now: woke.append(now))
    return svc, store, woke


def test_快循环_活收尾了当场叫唤醒链(tmp_path):
    svc, store, woke = _attention(tmp_path)

    class Done:
        def poll(self, book, at=None): return ["1027-2217-36a2"]

    svc.pc_tasks = Done()
    svc.care_tick(datetime(2026, 10, 27, 14, 28, tzinfo=timezone.utc))
    assert len(woke) == 1, "活收尾了，快循环没当场叫唤醒链（又得等 15 分钟的 tick）"
    store.close()


def test_快循环_没收尾不叫(tmp_path):
    svc, store, woke = _attention(tmp_path)

    class Running:
        def poll(self, book, at=None): return []

    svc.pc_tasks = Running()
    svc.care_tick(datetime(2026, 10, 27, 14, 28, tzinfo=timezone.utc))
    assert woke == []
    store.close()


# ---------- 上线卡（10-29，她：「你在手机上上线和部署也弹个确认卡吧」）：他只出卡，她点了才动 ----------

def _done_job(env):
    t, ssh, clock = env
    t.start("写个文档", "caelum", "s1", "做吧")
    jid = ssh.calls[-1][0].split()[1]
    ssh.states[jid] = DONE(jid)
    t.poll(Book())
    clock.t += timedelta(minutes=3)
    return jid


def test_上线卡_不是她亲口说的不出(env):
    t, ssh, _ = env
    jid = _done_job(env)
    n = len(ssh.calls)
    msg, card = t.ship_propose(jid, "s1", "（系统提示：到点了")
    assert card is None and len(ssh.calls) == n


def test_上线卡_没做完不出_不认识的不出(env):
    t, ssh, _ = env
    t.start("写个文档", "caelum", "s1", "做吧")
    jid = ssh.calls[-1][0].split()[1]
    msg, card = t.ship_propose(jid, "s1", "上线吧")
    assert card is None and "没做完" in msg
    msg, card = t.ship_propose("0000-0000-zzzz", "s1", "上线吧")
    assert card is None and "没有" in msg


def test_上线卡_出卡时什么都没动(env):
    t, ssh, _ = env
    jid = _done_job(env)
    msg, card = t.ship_propose(jid, "s1", "上线吧")
    assert card["summary"] == "加好了，测试也跑了。" and card["commits"] == 1
    assert "别说已经上线了" in msg
    assert ssh.verbs("ship") == [], "出卡时就发了 ship —— 她还没点"
    assert t.ship_get(jid)["status"] == "proposed"


def test_上线卡_工具层把卡挂上(env):
    t, ssh, _ = env
    jid = _done_job(env)
    ctx = tool_context.ToolContext(session_id="s1", user_text="上线吧")
    with tool_context.bind(ctx):
        out = _handlers(t)["pc_task_ship"]({"id": jid})
    assert "卡发给她了" in out
    assert [a for a in ctx.attachments if a["type"] == "ship"][0]["job_id"] == jid
    assert ssh.verbs("ship") == []


def test_上线卡_工具层测试会话不出(env, monkeypatch):
    t, ssh, _ = env
    jid = _done_job(env)
    monkeypatch.setattr("config.is_test_session", lambda sid: True)
    ctx = tool_context.ToolContext(session_id="test-x", user_text="上线吧")
    with tool_context.bind(ctx):
        assert "测试会话" in _handlers(t)["pc_task_ship"]({"id": jid})
    assert ctx.attachments == []


def test_上线卡_她点了才发ship_只发一次(env):
    t, ssh, _ = env
    jid = _done_job(env)
    t.ship_propose(jid, "s1", "上线吧")
    s = t.ship_confirm(jid)
    assert s["status"] == "shipping" and ssh.verbs("ship") == [f"ship {jid}"]
    with pytest.raises(pc_tasks.ShipError) as e:
        t.ship_confirm(jid)
    assert e.value.status == 409 and len(ssh.verbs("ship")) == 1
    # 上线中再出卡也不行
    assert t.ship_propose(jid, "s1", "再上一次")[1] is None


def test_上线卡_没出卡不能点_不要了也不能点(env):
    t, ssh, _ = env
    jid = _done_job(env)
    with pytest.raises(pc_tasks.ShipError):
        t.ship_confirm(jid)
    t.ship_propose(jid, "s1", "上线吧")
    assert t.ship_reject(jid)["status"] == "rejected"
    with pytest.raises(pc_tasks.ShipError):
        t.ship_confirm(jid)
    assert ssh.verbs("ship") == []


def test_上线卡_电脑没接就标没上成(env):
    t, ssh, _ = env
    jid = _done_job(env)
    t.ship_propose(jid, "s1", "上线吧")
    ssh.ship_reply = {"ok": False, "error": "已经在上线 / 上过了（running）"}
    with pytest.raises(pc_tasks.ShipError) as e:
        t.ship_confirm(jid)
    assert e.value.status == 502 and t.ship_get(jid)["status"] == "failed"


def test_上线收尾_上成了告诉她_只说一次(env):
    t, ssh, clock = env
    jid = _done_job(env)
    t.ship_propose(jid, "s1", "上线吧")
    t.ship_confirm(jid)
    book = Book()
    ssh.states[jid] = {**DONE(jid), "ship": {"state": "running", "step": "跑 nox-core 的测试"}}
    assert t.poll(book) == [] and book.notes == []
    assert t.ship_get(jid)["step"] == "跑 nox-core 的测试"
    ssh.states[jid] = {**DONE(jid), "ship": {"state": "shipped", "step": "完成",
                                              "deployed": ["nox-core/tools/x.py"], "manual": ["bridge/server.js"]}}
    clock.t += timedelta(minutes=3)
    assert t.poll(book) == [jid]
    why = book.notes[0][1]
    assert "**已经上线了**" in why.splitlines()[0] and "1 个文件" in why and "bridge/server.js" in why
    assert t.ship_get(jid)["status"] == "shipped"
    clock.t += timedelta(minutes=3)
    assert t.poll(book) == [] and len(book.notes) == 1


def test_上线收尾_没上成也告诉她(env):
    t, ssh, clock = env
    jid = _done_job(env)
    t.ship_propose(jid, "s1", "上线吧")
    t.ship_confirm(jid)
    ssh.states[jid] = {**DONE(jid), "ship": {"state": "failed", "error": "测试没过，没合：1 failed"}}
    book = Book()
    assert t.poll(book) == [jid]
    assert "**没上成**" in book.notes[0][1] and "测试没过" in book.notes[0][1]


def test_上线工具排在最后_声明是写():
    names = list(_handlers(PcTasks(FakePc(), "/tmp/x.json")).keys())
    assert names[-1] == "pc_task_ship" and names[:3] == ["pc_task_start", "pc_task_check", "pc_task_cancel"]
    assert pc_tasks.SHIP_SPEC.side_effect == "write" and pc_tasks.SHIP_SPEC.confirm_via


def test_上线卡_工具层看这一轮的原话(env):
    t, ssh, _ = env
    jid = _done_job(env)
    ctx = tool_context.ToolContext(session_id="s1", user_text="（系统提示：到点了")
    with tool_context.bind(ctx):
        _handlers(t)["pc_task_ship"]({"id": jid})
    assert ctx.attachments == [], "唤醒链那一轮也出了上线卡"


def test_快循环_纸条当轮就到期_不等大心跳(tmp_path):
    """10-29 实测：poll 问完电脑晚了一秒，纸条 wake_at 比这一轮的 now 晚，当轮不到期，晚了快 5 分钟才说。"""
    from attention.relationship import RelationshipState
    from attention.service import AttentionService
    from attention.store import AttentionStore

    class Provider:
        def get_state(self, turn=None, force_refresh=False):
            return {"has_data": False}

    tick_now = datetime(2026, 10, 29, 3, 16, 4, tzinfo=timezone.utc)
    clock = Clock()
    clock.t = tick_now + timedelta(seconds=1)          # 问电脑花了一秒
    ssh = FakeSsh()
    t = PcTasks(FakePc(), tmp_path / "j.json", run=ssh, now=clock)
    t.start("写个文档", "caelum", "s1", "做吧")
    jid = ssh.calls[-1][0].split()[1]
    ssh.states[jid] = DONE(jid)
    store = AttentionStore(tmp_path / "attn.db")
    seen = []
    svc = AttentionService(store, Provider(), RelationshipState(), fast_sources=[],
                           waker=lambda book, now: seen.append(len(book.due(now))))
    svc.pc_tasks = t
    svc.care_tick(tick_now)
    assert seen == [1], "纸条放了，但叫 waker 时还没到期（要等下一次大心跳）"
    store.close()
