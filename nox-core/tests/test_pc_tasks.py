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


#: 电脑上 Claude Code 交的结构化卡片字段（runner.ps1 + card.ps1 读出来的形状）
CARD = {"title": "发布按钮改成圆的", "changes": ["把 Moments 的发布按钮改成圆的"],
        "verified": ["跑了 vitest，全过"], "unverified": ["没在真手机上看过"]}

DONE = lambda jid, card=CARD, files=None: {  # noqa: E731
    "ok": True, "state": {"id": jid, "repo": "caelum", "state": "done"},
    "result": {"summary": "加好了，测试也跑了。", "commits": ["abc 加按钮"], "card": card,
               "files": files if files is not None else [{"status": "M", "path": "nox-core/tools/x.py"}],
               "branch": f"nox/{jid}", "diffstat": " 1 file changed, 12 insertions(+), 3 deletions(-)",
               "uncommitted": 0}}


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
    assert card["title"] == "发布按钮改成圆的" and card["changes"] == ["把 Moments 的发布按钮改成圆的"]
    assert card["verified"] == ["跑了 vitest，全过"] and card["unverified"] == ["没在真手机上看过"]
    assert card["stat"] == {"commits": 1, "files": 1, "plus": 12, "minus": 3}
    assert "别说已经上线了" in msg and "别在聊天里再列一遍" in msg
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


def test_上线进度_只在卡上走_不放纸条(env):
    """她 10-29：点上线后聊天流零新增消息，状态全程只在原卡内变化。"""
    t, ssh, clock = env
    jid = _done_job(env)
    t.ship_propose(jid, "s1", "上线吧")
    t.ship_confirm(jid)
    book = Book()
    ssh.states[jid] = {**DONE(jid), "ship": {"state": "running", "step": "正在跑 nox-core 的全量测试（大约 4 分钟）…"}}
    assert t.ship_get(jid)["step"] == "正在跑 nox-core 的全量测试（大约 4 分钟）…"
    ssh.states[jid] = {**DONE(jid), "ship": {"state": "shipped", "finished": "2026-10-29T11:15:30",
                                              "deployed": ["nox-core/tools/x.py"]}}
    clock.t += timedelta(minutes=3)
    assert t.poll(book) == [] and book.notes == [], "上线收尾又在聊天里叫他开口了"
    s = t.ship_get(jid, refresh=False)               # 她没开着 App：心跳自己也得记下
    assert s["status"] == "shipped" and s["finished"] == "2026-10-29T11:15:30" and not s.get("step")


def test_上线进度_卡一查就对齐_不等心跳(env):
    """收尾了的状态卡上一查就记下（不然卡停在「上线中：完成」—— 她 10-29 截图）。"""
    t, ssh, _ = env
    jid = _done_job(env)
    t.ship_propose(jid, "s1", "上线吧")
    t.ship_confirm(jid)
    ssh.states[jid] = {**DONE(jid), "ship": {"state": "shipped", "step": None, "finished": "2026-10-29T11:15:30"}}
    assert t.ship_get(jid)["status"] == "shipped"
    t.pc.ip = None                                   # 电脑关了也还记得
    assert t.ship_get(jid)["status"] == "shipped"


def test_上线没成_卡上看得到原因_能重试(env):
    t, ssh, _ = env
    jid = _done_job(env)
    t.ship_propose(jid, "s1", "上线吧")
    t.ship_confirm(jid)
    ssh.states[jid] = {**DONE(jid), "ship": {"state": "failed", "error": "测试没过，没合进主线"}}
    s = t.ship_get(jid)
    assert s["status"] == "failed" and s["error"] == "测试没过，没合进主线" and not s.get("merged")
    again = t.ship_confirm(jid)                      # 重试
    assert again["status"] == "shipping" and again["error"] is None and again["attempts"] == 2
    assert ssh.verbs("ship") == [f"ship {jid}", f"ship {jid}"]
    ssh.states[jid] = {**DONE(jid), "ship": {"state": "failed", "merged": "a9a4105", "error": "已经合进主线了，但部署没成"}}
    assert t.ship_get(jid)["merged"] is True         # 卡上不能再写「未合进主线」


def test_上线卡_不要了不能重试(env):
    t, ssh, _ = env
    jid = _done_job(env)
    t.ship_propose(jid, "s1", "上线吧")
    t.ship_reject(jid)
    with pytest.raises(pc_tasks.ShipError):
        t.ship_confirm(jid)


def test_上线卡_字段缺一块就不出(env):
    """她 10-29：副作用和风险缺一块，卡就不许发出来。"""
    t, ssh, _ = env
    for card, files, why in [
        (None, None, "结构化"),                                                  # 改版前的活 / 没按格式交
        ({**CARD, "verified": [], "unverified": []}, None, "验证"),              # 一句没说怎么验的
        ({**CARD, "changes": []}, None, "改了什么"),
        ({**CARD, "title": ""}, None, "叫什么"),
        (CARD, [], "文件清单"),                                                  # 算不出点了会怎样
    ]:
        t.start("改按钮", "caelum", "s1", "做吧")
        jid = ssh.calls[-1][0].split()[1]
        ssh.states[jid] = DONE(jid, card=card, files=files)
        msg, out = t.ship_propose(jid, "s1", "上线吧")
        assert out is None and "不发" in msg and why in msg, (why, msg)


def test_点了会发生什么_按文件算():
    eff = lambda files, repo="caelum": [e["text"] for e in pc_tasks.ship_effects(  # noqa: E731
        [{"status": s, "path": p} for s, p in files], repo)]
    warn = lambda files, repo="caelum": [e["text"] for e in pc_tasks.ship_effects(  # noqa: E731
        [{"status": s, "path": p} for s, p in files], repo) if e["warn"]]
    docs = eff([("A", "docs/Nox-开关糖糖电脑-说明.md"), ("M", "docs/README.md")])
    assert "只改了文档，不用部署、不重启" in docs and not warn([("A", "docs/x.md")])
    core = eff([("M", "nox-core/tools/x.py"), ("M", "nox-core/tests/test_x.py")])
    assert any("全量测试" in s for s in core) and any("换上 1 个文件" in s for s in core)
    assert any("重启" in s and "离线" in s for s in warn([("M", "nox-core/tools/x.py")]))
    assert eff([("M", "nox-core/tests/test_x.py")])[-1] == "只改了测试，不用部署、不重启"
    assert not warn([("M", "nox-core/tests/test_x.py")])                       # 只改测试：不部署、不重启
    assert any("bridge" in s and "上电脑" in s for s in warn([("M", "bridge/server.js")]))
    assert any("App" in s and "上电脑" in s for s in warn([("M", "frontend/src/x.jsx")], "nox-app"))


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
