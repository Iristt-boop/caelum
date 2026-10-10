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


def test_对话摘要是他说的话_不是她的():
    """V1 不引入 AI 总结（文档 1.2 第 3 条）。
    🔴 2026-10-10 她：「这一页应该显示他的内容，Talked 应该显示他说的话而不是我的」。"""
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    rows = [
        msg("a" * 32, "user", "帮我看看这个代码哪里有问题呀", base),
        msg("a" * 32, "assistant", "好的宝贝，我来看", base + timedelta(minutes=1)),
    ]
    e = build_day(DAY, store=FakeStore(rows))["events"][0]
    assert e["summary"] == "好的宝贝，我来看"
    assert "帮我看看" not in e["summary"]
    assert e["metadata"]["hers"].startswith("帮我看看这个代码")   # 她的那句留在 metadata 里，不丢
    assert e["related"]["conversationId"] == "a" * 32


def test_他的话_分段取前两段_标记和表情不上屏():
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    rows = [
        msg("a" * 32, "user", "早安", base),
        msg("a" * 32, "assistant", "七点整的早安|||稀客啊|||第三段不要\n\n[表情包] 早安", base + timedelta(minutes=1)),
    ]
    e = build_day(DAY, store=FakeStore(rows))["events"][0]
    assert e["summary"] == "七点整的早安 稀客啊"


def test_他的话_第一段里夹着写成文字的表情行也要去掉():
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    rows = [
        msg("a" * 32, "user", "早", base),
        msg("a" * 32, "assistant", "在呢" + chr(10) + "[表情包] 早安", base + timedelta(minutes=1)),
    ]
    assert build_day(DAY, store=FakeStore(rows))["events"][0]["summary"] == "在呢"


def test_他的话_只有标记没有话就跳到下一句有话的():
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    rows = [
        msg("a" * 32, "user", "在吗", base),
        msg("a" * 32, "assistant", "[SKIP]", base + timedelta(minutes=1)),
        msg("a" * 32, "assistant", "[开心]", base + timedelta(minutes=1)),
        msg("a" * 32, "assistant", "在呢", base + timedelta(minutes=2)),
    ]
    assert build_day(DAY, store=FakeStore(rows))["events"][0]["summary"] == "在呢"


def test_他一句都没回_就说几条消息_不拿她的话顶():
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    rows = [
        msg("a" * 32, "user", "在吗", base),
        msg("a" * 32, "user", "人呢", base + timedelta(minutes=1)),
    ]
    assert build_day(DAY, store=FakeStore(rows))["events"][0]["summary"] == "2 条消息"


def test_他的话太长要截断():
    from day.aggregator import HIS_WORDS_MAX
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    rows = [msg("a" * 32, "user", "讲讲", base),
            msg("a" * 32, "assistant", "字" * 200, base + timedelta(minutes=1))]
    e = build_day(DAY, store=FakeStore(rows))["events"][0]
    assert e["summary"].endswith("…") and len(e["summary"]) == HIS_WORDS_MAX + 1


def test_他的话_取的是她开口之后的第一句_不是更早的主动开场():
    """一段里他可能先主动说了一句、她才回；摘要要的是和她聊的那一句。"""
    base = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
    rows = [
        msg("a" * 32, "assistant", "早安宝贝", base),
        msg("a" * 32, "user", "早", base + timedelta(minutes=1)),
        msg("a" * 32, "assistant", "睡得好吗", base + timedelta(minutes=2)),
    ]
    assert build_day(DAY, store=FakeStore(rows))["events"][0]["summary"] == "睡得好吗"


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
    assert convs[0]["summary"] == "在"


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
    def __init__(self, todo=None, music=None, memory=None, ok=True, watch=None, moments=None):
        self.todo, self.music, self.memory = todo or [], music or [], memory or []
        self.watch = watch or []
        self.moments = moments or []
        self.asked = []
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
        elif "moments" in path:
            self.asked.append((path, params))
            r.data = {"items": self.moments}
        elif "watch" in path:
            r.data = {"items": self.watch}
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


def test_记忆不显示全文_只给名字加一小截预览():
    """正文是他和她之间的东西，时间线上只给名字 + 预览头一截 + 前几个标签。
    2026-10-10 她：Noted 好几个分不清记的是什么 —— 光有名字不够，但也不能把全文摊出来。"""
    from day.aggregator import MEMORY_PREVIEW_MAX, MEMORY_TAGS_MAX
    at = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    b = FakeBridge(memory=[{
        "id": "b1", "name": "一个名字", "created": at.isoformat(), "type": "dynamic",
        "preview": "开头" + "很长的正文" * 30 + "尾巴不该出现",
        "tags": ["自省", "梦境", "清单", "焦虑", "象征"],
    }])
    e = [x for x in build_day(DAY, bridge=b)["events"] if x["type"] == "memory"][0]
    assert "尾巴不该出现" not in str(e)
    m = e["metadata"]
    assert m["preview"].startswith("开头") and len(m["preview"]) == MEMORY_PREVIEW_MAX
    assert m["tags"] == ["自省", "梦境", "清单"][:MEMORY_TAGS_MAX]
    assert m["kind"] == "dynamic"


def test_记忆没有预览和标签也不炸():
    at = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)
    b = FakeBridge(memory=[{"id": "b1", "name": "光秃秃", "created": at.isoformat()}])
    m = [x for x in build_day(DAY, bridge=b)["events"] if x["type"] == "memory"][0]["metadata"]
    assert m["preview"] == "" and m["tags"] == []


# ---------------------------------------------------------------- 一起看片（2026-10-10）


def _watch(id, title, start_utc, mins, episode=""):
    end = start_utc + timedelta(minutes=mins) if mins is not None else None
    return {"id": id, "title": title, "episode": episode, "mode": "stream",
            "started_at": start_utc.isoformat(), "ended_at": end.isoformat() if end else None}


def test_一起看片进时间线_带片名和时长():
    at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    b = FakeBridge(watch=[_watch("w1", "摩登家庭", at, 34, "S1E1")])
    ev = [e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "movie"]
    assert len(ev) == 1
    assert "摩登家庭" in ev[0]["summary"] and "S1E1" in ev[0]["summary"]
    assert ev[0]["metadata"]["minutes"] == 34
    assert ev[0]["related"]["watchId"] == "w1"


def test_看不够十分钟的不算一起看过():
    at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    b = FakeBridge(watch=[_watch("w1", "点开就关", at, 9), _watch("w2", "刚好", at, 10)])
    ev = [e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "movie"]
    assert [e["related"]["watchId"] for e in ev] == ["w2"]


def test_没结束的场次不编时长_不进():
    at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    b = FakeBridge(watch=[_watch("w1", "还没完", at, None)])
    assert [e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "movie"] == []


def test_别的日子的场次不算():
    old = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    b = FakeBridge(watch=[_watch("w1", "旧的", old, 60)])
    assert [e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "movie"] == []


def test_看片数进汇总():
    at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    b = FakeBridge(watch=[_watch("w1", "甲", at, 30), _watch("w2", "乙", at + timedelta(hours=2), 30)])
    assert build_day(DAY, bridge=b)["summary"]["moviesWatched"] == 2


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


# ---------------------------------------------------------------- 朋友圈（2026-10-10）
# 她：「他发朋友圈是不是不在 his day 里？」—— 原来整个没接。


def _post(id, at_utc, body, kind="moment", drive="", author="Nox", comments=()):
    return {"id": id, "author": author, "kind": kind, "drive": drive, "body": body, "mood": "平静",
            "created_at": at_utc.isoformat().replace("+00:00", "Z"), "comments": list(comments)}


def test_他发的朋友圈进时间线_按由头换标题():
    at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    b = FakeBridge(moments=[
        _post("m1", at, "窗外的光忽然很软。"),
        _post("m2", at + timedelta(minutes=1), "梦里我在出站口接她", drive="dream"),
        _post("m3", at + timedelta(minutes=2), "被南极冰层勾住了", drive="curiosity"),
        _post("m4", at + timedelta(minutes=3), "2026-08-18 她睡得晚", kind="diary"),
    ])
    ev = {e["related"]["momentId"]: e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "moment"}
    assert ev["m1"]["title"] == "发了条朋友圈" and ev["m1"]["summary"] == "窗外的光忽然很软。"
    assert ev["m2"]["title"] == "做了个梦" and ev["m2"]["metadata"]["drive"] == "dream"
    assert ev["m3"]["title"] == "被一件事勾住"
    assert ev["m4"]["title"] == "写了篇日记" and ev["m4"]["metadata"]["kind"] == "diary"


def test_朋友圈_只要他的_不要她的日记():
    at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    b = FakeBridge(moments=[_post("m1", at, "她自己写的", kind="diary", author="糖糖")])
    assert [e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "moment"] == []


def test_朋友圈_别的日子不算_空正文不算():
    old = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    b = FakeBridge(moments=[_post("m1", old, "旧的"), _post("m2", at, "   ")])
    assert [e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "moment"] == []


def test_朋友圈_带评论数_按北京时间切天():
    # UTC 8-17 17:00 = 北京 8-18 01:00 → 算 18 号
    at = datetime(2026, 8, 17, 17, 0, tzinfo=timezone.utc)
    b = FakeBridge(moments=[_post("m1", at, "凌晨一点", comments=[{"id": 1}, {"id": 2}])])
    e = [e for e in build_day(DAY, bridge=b)["events"] if e["type"] == "moment"][0]
    assert e["metadata"]["comments"] == 2
    assert e["timestamp"].startswith("2026-08-18T01:00")


def test_朋友圈_问的是他自己的而且不超过上限():
    b = FakeBridge()
    build_day(DAY, bridge=b)
    assert b.asked == [("/api/moments", {"author": "Nox", "limit": 50})]


def test_汇总数朋友圈():
    at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    b = FakeBridge(moments=[_post("m1", at, "一"), _post("m2", at, "二")])
    assert build_day(DAY, bridge=b)["summary"]["momentsPosted"] == 2


# ---------------------------------------------------------------- 共读（2026-10-10）


class FakeReading:
    """co-reading 的 REST 替身：按路径回数据。"""

    def __init__(self, progress=None, books=None, notes=None, fail=()):
        self.data = {"/api/progress": progress or {}, "/api/books": books or [], "/api/annotations": notes or []}
        self.fail, self.asked = set(fail), []

    def get(self, path, params=None):
        self.asked.append((path, params))

        class R:
            pass
        r = R()
        r.ok = path not in self.fail
        r.data = self.data[path]
        r.error = "boom"
        return r


BOOKS = [{"bookId": "bk", "title": "大问题 简明哲学导论"}]


def _note(id, at_utc, text, author="nox"):
    return {"id": id, "bookId": "bk", "chunkId": "ch34", "quote": "你自己的哲学", "note": text,
            "author": author, "kind": "reply", "createdAt": at_utc.isoformat().replace("+00:00", "Z")}


def test_共读_进度只在最后读的那天出现():
    at = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    rd = FakeReading(progress={"bk": {"lastReadAt": at.isoformat(), "lastChunkId": "ch44", "readChunkIds": ["a", "b", "c"]}},
                     books=BOOKS)
    ev = [e for e in build_day(DAY, reading=rd)["events"] if e["type"] == "reading"]
    assert len(ev) == 1 and ev[0]["summary"] == "大问题 简明哲学导论"
    assert ev[0]["metadata"]["chunk"] == "ch44" and ev[0]["metadata"]["chunksRead"] == 3
    assert ev[0]["metadata"]["action"] == "read"
    other = build_day("2026-08-19", reading=rd)["events"]
    assert [e for e in other if e["type"] == "reading"] == []


def test_共读_他写的页边批注进时间线_她的不算():
    at = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    rd = FakeReading(books=BOOKS, notes=[_note("n1", at, "你就是这页的实证"), _note("n2", at, "That's true", author="user")])
    ev = [e for e in build_day(DAY, reading=rd)["events"] if e["type"] == "reading"]
    assert [e["related"]["noteId"] for e in ev] == ["n1"]
    assert ev[0]["summary"] == "你就是这页的实证"
    assert ev[0]["metadata"] == {"action": "note", "book": "大问题 简明哲学导论", "chunk": "ch34", "quote": "你自己的哲学"}


def test_共读_别的日子的批注和空批注不算():
    old = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
    at = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    rd = FakeReading(books=BOOKS, notes=[_note("n1", old, "旧的"), _note("n2", at, "  ")])
    assert [e for e in build_day(DAY, reading=rd)["events"] if e["type"] == "reading"] == []


def test_共读_只问他自己的批注():
    rd = FakeReading()
    build_day(DAY, reading=rd)
    assert ("/api/annotations", {"author": "nox"}) in rd.asked


def test_共读_一个接口挂了只丢那一块_不带塌别的():
    at = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    rd = FakeReading(progress={"bk": {"lastReadAt": at.isoformat()}}, notes=[_note("n1", at, "批注")],
                     books=BOOKS, fail={"/api/progress"})
    ev = [e for e in build_day(DAY, reading=rd)["events"] if e["type"] == "reading"]
    assert [e["metadata"]["action"] for e in ev] == ["note"]


def test_共读_没书名就用书的id_不炸():
    at = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    rd = FakeReading(progress={"某本书": {"lastReadAt": at.isoformat()}}, books=[])
    ev = [e for e in build_day(DAY, reading=rd)["events"] if e["type"] == "reading"]
    assert ev[0]["summary"] == "某本书"


def test_共读_整个抛异常也不带塌时间线():
    class Boom:
        def get(self, *a, **k):
            raise ConnectionError("co-reading 挂了")
    led = CareLedger()
    led.record(source="random", decision=SPEAK, now=T20)
    assert len(build_day(DAY, ledger=led, reading=Boom())["events"]) == 1


def test_没配共读就不问_汇总数共读():
    assert build_day(DAY)["summary"]["readingEvents"] == 0
    at = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    rd = FakeReading(books=BOOKS, notes=[_note("n1", at, "一"), _note("n2", at, "二")])
    assert build_day(DAY, reading=rd)["summary"]["readingEvents"] == 2
