"""「Nox 的一天」聚合器（`Nox 的一天 架构设计文档.md` v1.1）。

钉住文档里那三条硬约束：

1. **不新建事实存储** —— 聚合器只读，测试里给什么它就用什么
2. **每条能溯源** —— related 里必须带得回原始记录的 id
3. **只输出用户可感知的事件** —— 心跳、故障、内部调度不进时间线
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from attention.care.ledger import BLOCK, FAILED, SKIP, SPEAK, CareLedger
from day.aggregator import CONVERSATION_GAP_MIN, build_day

CST = timezone(timedelta(hours=8))
DAY = "2026-08-18"
# 中国时间 2026-08-18 20:00
T20 = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.asked = None

    def messages_between(self, start, end, clean_only=True):
        self.asked = (start, end)
        return self.rows


class FakeWake:
    def __init__(self, items=None):
        self.items = items or []

    def all(self):
        return self.items


class Note:
    """够用的 Wakeup 替身。"""

    def __init__(self, id, why, kind, history):
        self.id, self.why, self.kind, self.history = id, why, kind, history


def msg(session, role, text, at):
    return {"session_id": session, "role": role, "text": text, "created_at": at.isoformat()}


# ---------------------------------------------------------------- CareLedger


def test_三种决策都进时间线():
    """文档第 4 节第 5 条：spoke / skip / blocked 全部纳入聚合。"""
    led = CareLedger()
    led.record(source="random", decision=SPEAK, thread_id="care-1", message_id="m1", now=T20)
    led.record(source="random", decision=SKIP, now=T20)
    led.record(source="sleep", decision=BLOCK, reason="今天额度用完了", now=T20)

    day = build_day(DAY, ledger=led)
    kinds = [(e["type"], e["status"]) for e in day["events"]]
    assert ("care", "completed") in kinds
    assert ("care", "skipped") in kinds
    assert ("care", "blocked") in kinds


def test_故障不进时间线():
    """推送挂了不是她能感知的事（文档 1.2 第 4 条）。"""
    led = CareLedger()
    led.record(source="sleep", decision=FAILED, reason="推送挂了", now=T20)
    assert build_day(DAY, ledger=led)["events"] == []


def test_每条care都能溯源():
    led = CareLedger()
    led.record(source="wake", decision=SPEAK, thread_id="care-9", message_id="msg-9", now=T20)
    e = build_day(DAY, ledger=led)["events"][0]
    assert e["related"]["careId"] == "care-9"
    assert e["related"]["conversationId"] == "msg-9"


def test_标题说的是他做了什么不是模块名():
    led = CareLedger()
    led.record(source="random", decision=SPEAK, now=T20)
    e = build_day(DAY, ledger=led)["events"][0]
    assert e["title"] == "惦记你"
    assert "random" not in e["title"]
    # 模块名放 metadata，给排查用，不给她看
    assert e["metadata"]["trigger"] == "random"


def test_skip和blocked的摘要不一样():
    """混成一句话，就分不清「他没什么可说」和「规则拦着」。"""
    led = CareLedger()
    led.record(source="random", decision=SKIP, now=T20)
    led.record(source="random", decision=BLOCK, reason="60 分钟内已经开过一条链了", now=T20)
    a, b = build_day(DAY, ledger=led)["events"]
    assert "没什么具体的可说" in a["summary"]
    assert "60 分钟" in b["summary"]


def test_别的日期的账本不算进来():
    led = CareLedger()
    led.record(source="random", decision=SPEAK, now=T20)
    assert build_day("2026-08-17", ledger=led)["events"] == []


# ---------------------------------------------------------------- 对话


def test_对话按静默间隔切段():
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)   # 中国 12:00
    rows = [
        msg("a" * 32, "user", "在吗", base),
        msg("a" * 32, "assistant", "在", base + timedelta(minutes=1)),
        # 隔了超过阈值 → 另一段
        msg("a" * 32, "user", "帮我看看代码", base + timedelta(minutes=CONVERSATION_GAP_MIN + 5)),
        msg("a" * 32, "assistant", "好", base + timedelta(minutes=CONVERSATION_GAP_MIN + 6)),
    ]
    day = build_day(DAY, store=FakeStore(rows))
    convs = [e for e in day["events"] if e["type"] == "conversation"]
    assert len(convs) == 2


def test_单条消息不成段():
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    day = build_day(DAY, store=FakeStore([msg("a" * 32, "user", "喂", base)]))
    assert [e for e in day["events"] if e["type"] == "conversation"] == []


def test_对话摘要用她开头那句原话():
    """V1 不引入 AI 总结（文档 1.2 第 3 条）。"""
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    rows = [
        msg("a" * 32, "user", "帮我看看这个代码哪里有问题呀", base),
        msg("a" * 32, "assistant", "好的宝贝", base + timedelta(minutes=1)),
    ]
    e = build_day(DAY, store=FakeStore(rows))["events"][0]
    assert e["summary"].startswith("帮我看看这个代码")
    assert e["related"]["conversationId"] == "a" * 32


def test_系统提示词不是对话():
    """2026-08-18 上线当天抓到的：主动开口那几条链把指令当 user message
    塞进会话，时间线原样摊给她看「（系统提示：这不是糖糖在跟你说话…」。"""
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    rows = [
        msg("a" * 32, "user", "（系统提示：这不是糖糖在跟你说话，是每天早上的主动问候。", base),
        msg("a" * 32, "assistant", "早安宝贝", base + timedelta(minutes=1)),
    ]
    assert build_day(DAY, store=FakeStore(rows))["events"] == [], \
        "他主动开口的回声不该算成一段对话 —— 那已经由 care 事件覆盖了"


def test_视觉注入也不算她说话():
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    rows = [
        msg("a" * 32, "user", "[她发来 1 张图片。你看不了图，以下是视觉模型的描述…]", base),
        msg("a" * 32, "assistant", "好好看", base + timedelta(minutes=1)),
    ]
    assert build_day(DAY, store=FakeStore(rows))["events"] == []


def test_系统提示词不该劈开一段真对话():
    """滤要滤在切段之前 —— 之后滤的话，中间插一条提示词会切成两段。"""
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    rows = [
        msg("a" * 32, "user", "在吗", base),
        msg("a" * 32, "assistant", "在", base + timedelta(minutes=1)),
        msg("a" * 32, "user", "（系统提示：不是她在跟你说话。", base + timedelta(minutes=2)),
        msg("a" * 32, "assistant", "忽然想起你", base + timedelta(minutes=3)),
        msg("a" * 32, "user", "干嘛呢", base + timedelta(minutes=4)),
    ]
    convs = [e for e in build_day(DAY, store=FakeStore(rows))["events"]
             if e["type"] == "conversation"]
    assert len(convs) == 1, "被系统提示词劈成了两段"
    assert convs[0]["summary"] == "在吗"


def test_按中国时区切天():
    """⚠️ 用 UTC 切的话，中国时间 00:30 的事件会落到前一天。"""
    store = FakeStore()
    build_day(DAY, store=store)
    start, end = store.asked
    # 中国 8-18 00:00 = UTC 8-17 16:00
    assert start.startswith("2026-08-17T16:00")
    assert end.startswith("2026-08-18T16:00")


# ---------------------------------------------------------------- 纸条


def test_纸条只取真发生过的动作():
    at = datetime(2026, 8, 18, 5, 0, tzinfo=timezone.utc)
    notes = [Note("wake-1", "她去吃饭了", "followup", [
        {"at": at.isoformat(), "action": "spoke", "text": "吃完了吗"},
        {"at": at.isoformat(), "action": "pass", "raw": "..."},        # 内部调度，不进
        {"at": at.isoformat(), "action": "done"},
    ])]
    day = build_day(DAY, wakeups=FakeWake(notes))
    actions = [e["metadata"]["action"] for e in day["events"]]
    assert sorted(actions) == ["done", "spoke"]
    assert all(e["related"]["careId"] == "wake-1" for e in day["events"])


def test_别的日子的纸条不算():
    at = datetime(2026, 8, 17, 5, 0, tzinfo=timezone.utc)
    notes = [Note("wake-1", "旧的", "followup", [{"at": at.isoformat(), "action": "spoke"}])]
    assert build_day(DAY, wakeups=FakeWake(notes))["events"] == []


# ---------------------------------------------------------------- 汇总 / 韧性


def test_汇总用账本的数字():
    led = CareLedger()
    for _ in range(3):
        led.record(source="random", decision=SPEAK, now=T20)
    for _ in range(5):
        led.record(source="random", decision=SKIP, now=T20)
    for _ in range(4):
        led.record(source="random", decision=BLOCK, reason="拦了", now=T20)

    s = build_day(DAY, ledger=led)["summary"]
    assert (s["careSpoke"], s["careSkipped"], s["careBlocked"]) == (3, 5, 4)
    assert s["careConsidered"] == 12


# ---------------------------------------------------------------- 待办 / 音乐 / 世界


class FakeBridge:
    def __init__(self, todo=None, music=None, memory=None, ok=True):
        self.todo, self.music, self.memory = todo or [], music or [], memory or []
        self.ok = ok

    def get(self, path, params=None):
        class R:
            pass
        r = R()
        r.ok = self.ok
        if "todo" in path:
            r.data = {"items": self.todo}
        elif "music" in path:
            r.data = {"songs": self.music}
        else:
            r.data = {"items": self.memory}
        return r


class FakeWorld:
    def __init__(self, items=None):
        self.items = items or []

    def query(self, type, days=None, limit=30, now=None):
        return self.items


class Ev:
    def __init__(self, content, at, source="health", kind="observed"):
        self.content, self.observed_at, self.source, self.kind = content, at, source, kind
        self.reference = "health://sleep/2026-08-18"


def test_待办的记下和划掉都进时间线():
    at = datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc)   # 中国 14:30
    b = FakeBridge(todo=[
        {"id": "t1", "text": "背单词", "at": at.isoformat(), "kind": "created"},
        {"id": "t1", "text": "背单词", "at": at.isoformat(), "kind": "completed", "repeat": "daily"},
    ])
    ev = build_day(DAY, bridge=b)["events"]
    assert [e["title"] for e in ev] == ["记下一件事", "陪你做完一件事"]
    assert all(e["related"]["taskId"] == "t1" for e in ev)


def test_共听只取今天的():
    """/music/recent 回最近 30 首，跨好几天。"""
    today = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    old = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    b = FakeBridge(music=[
        {"songId": "1", "name": "Ocean Eyes", "artist": "Billie Eilish",
         "playedAt": today.isoformat()},
        {"songId": "2", "name": "前几天那首", "playedAt": old.isoformat()},
    ])
    ev = [e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "music"]
    assert len(ev) == 1
    assert ev[0]["summary"] == "Ocean Eyes — Billie Eilish"


def test_世界事实进时间线():
    at = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc)
    ev = build_day(DAY, world=FakeWorld([Ev("昨晚睡了 7.2 小时", at)]))["events"]
    assert len(ev) == 1
    assert ev[0]["type"] == "world"
    assert "7.2" in ev[0]["summary"]


def test_今天记住的事进时间线():
    at = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    b = FakeBridge(memory=[
        {"id": "b1", "name": "她喜欢的晚霞颜色", "type": "dynamic",
         "created": at.isoformat(), "preview": "今天她说……"},
    ])
    ev = [e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "memory"]
    assert len(ev) == 1
    assert ev[0]["summary"] == "她喜欢的晚霞颜色"
    assert ev[0]["related"]["memoryId"] == "b1"


def test_记忆不显示全文():
    """正文是他和她之间的东西，列表里只给名字。"""
    at = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    b = FakeBridge(memory=[{
        "id": "b1", "name": "一个名字", "created": at.isoformat(),
        "preview": "这段正文不该出现在时间线上",
    }])
    e = [x for x in build_day(DAY, bridge=b)["events"] if x["type"] == "memory"][0]
    assert "不该出现" not in str(e)


def test_没带时区的created按中国时间理解():
    """OB 的 now_iso() 可能不带时区，而它就跑在那台机器上。"""
    b = FakeBridge(memory=[{"id": "b1", "name": "无时区", "created": "2026-08-18T14:00:00"}])
    ev = [e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "memory"]
    assert len(ev) == 1


def test_别的日子的记忆不算():
    old = datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc)
    b = FakeBridge(memory=[{"id": "b1", "name": "旧的", "created": old.isoformat()}])
    assert [e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "memory"] == []


def test_bridge挂了不影响别的源():
    led = CareLedger()
    led.record(source="random", decision=SPEAK, now=T20)
    day = build_day(DAY, ledger=led, bridge=FakeBridge(ok=False))
    assert len(day["events"]) == 1, "bridge 读不到，care 那条也该照常出来"


def test_没接的源要如实标出来():
    """「今天没听歌」和「共听根本没接」不能长得一样。"""
    day = build_day(DAY)
    assert day["sources"]["care_ledger"]["wired"] is True
    assert day["sources"]["music"]["wired"] is True
    # 2026-08-18 给 OB 加了 /recent 之后才接上的
    assert day["sources"]["memory"]["wired"] is True
    # 八个源现在全接了 —— 但 note 还得留着说清各自的限制
    assert all(v["wired"] for v in day["sources"].values())


def test_待办完成数从事件里数出来():
    at = datetime(2026, 8, 18, 6, 30, tzinfo=timezone.utc)
    b = FakeBridge(todo=[
        {"id": "t1", "text": "背单词", "at": at.isoformat(), "kind": "completed"},
        {"id": "t2", "text": "运动", "at": at.isoformat(), "kind": "completed"},
        {"id": "t3", "text": "买传感器", "at": at.isoformat(), "kind": "created"},
    ])
    assert build_day(DAY, bridge=b)["summary"]["tasksCompleted"] == 2


def test_一个源炸了不带塌整条时间线():
    class Boom:
        def messages_between(self, *a, **k):
            raise RuntimeError("库坏了")

    led = CareLedger()
    led.record(source="random", decision=SPEAK, now=T20)
    day = build_day(DAY, ledger=led, store=Boom())
    assert len(day["events"]) == 1, "对话源炸了，care 那条也该照常出来"


def test_事件按时间正序():
    led = CareLedger()
    led.record(source="random", decision=SPEAK,
               now=datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc))
    led.record(source="sleep", decision=SPEAK,
               now=datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc))
    stamps = [e["timestamp"] for e in build_day(DAY, ledger=led)["events"]]
    assert stamps == sorted(stamps)
