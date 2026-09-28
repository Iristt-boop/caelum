"""她的状态 → 他的说话方式（2026-09-23，糖糖：「Nox 要在 AI 和人之间保持相似性」）。

## 这些测试能挡什么

- 🔴 **那一夜的重演**：23:11「躺下了」→ 睡到 10 点。01:08 / 07:52 / 09:22 他该是
  自言自语，不是「醒了？」；09:40 那次位置补报不许变成「回来了？」
- 状态判错方向：下午的「躺下了」当成晚安、夜里还在聊天当成睡着、
  位置旧了还笃定她在外面、说了晚安却因为位置没更新在半夜吃醋「夜不归宿」
- 出门之后时间流逝没人管（原来的 T+5/10/30 链从来没实现过）：
  里程碑不响、重复响、过了零点先响轻的
- 情绪边界和「不许编」没进开场白
- 睡着时的自言自语被记成「她没理我」→ 早上回复率被拉低、他反而退缩

## 挡不住什么

- 模型真的照着说（那要看线上 Care 日志里的实际句子）
- HA 真实的 last_reported 形状（按 2026-09-23 线上查到的字段写的）
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from attention.care import her_state
from attention.care.her_state import ASLEEP, AWAY, IGNORED, NORMAL, HerState
from attention.care.signal import CareSignal
from attention.sources.presence import PresenceSource

CN = timezone(timedelta(hours=8), "CST")


def cn(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 9, day, h, m, tzinfo=CN)


# ---------------------------------------------------------------- 假的会话库

class FakeSessions:
    def __init__(self, said: str = "", at: datetime | None = None, his: list[datetime] | None = None):
        self.said, self.at, self.his = said, at, his or []

    def recent(self, limit=1, clean_only=True):
        return [type("S", (), {"id": "s1"})()] if self.at else []

    def last_user_message(self, sid):
        return (self.said, self.at) if self.at else None

    def count_assistant_since(self, sid, since):
        return sum(1 for t in self.his if t > since)

    def last_user_at(self, sid):
        return self.at


class FakePresence:
    name = "location"

    def __init__(self, last="home", away_since=None, updated_at=None):
        self._last, self.away_since, self.updated_at = last, away_since, updated_at

    def poll(self, now):
        return []


# ---------------------------------------------------------------- 那一夜

#: 2026-09-22 夜：18:19 出门、18:59 最后一次位置上报、23:11「躺下了」
LEFT, LAST_GPS, LAY_DOWN = cn(22, 18, 19), cn(22, 18, 59), cn(22, 23, 11)


def _that_night(now: datetime, his: list[datetime] | None = None) -> HerState:
    return her_state.read(
        now,
        FakeSessions("躺下了", LAY_DOWN, his=his or [cn(22, 23, 12)]),
        FakePresence("not_home", away_since=LEFT, updated_at=LAST_GPS),
    )


@pytest.mark.parametrize("h,m", [(1, 8), (7, 52), (9, 22)])
def test_那一夜_她说了躺下了_之后都是睡着(h, m):
    st = _that_night(cn(23, h, m))
    assert st.said_goodnight, "23:11 的「躺下了」没认出来"
    assert st.posture == ASLEEP, (
        f"{h}:{m:02d} 判成了 {st.posture} —— 位置显示她在外面，但她说了躺下了，"
        "该信她说的话，不该在半夜吃醋「夜不归宿」")
    g = her_state.guidance(st)
    assert "自言自语" in g
    assert "不要说「醒了？」" in g


def test_那一夜_开场白不再逼他找话头():
    g = her_state.guidance(_that_night(cn(23, 7, 52)))
    assert "想不出具体的就别说" not in g
    assert "噪音" not in g
    assert "不许替她编" in g, "「不许编她的状态」这条没进开场白"


def test_下午的躺下了不算晚安():
    st = her_state.read(cn(23, 16), FakeSessions("躺下了歇会儿", cn(23, 15)))
    assert not st.said_goodnight
    assert st.posture == NORMAL


def test_晚安过期就不再算():
    st = her_state.read(cn(23, 20), FakeSessions("晚安", cn(22, 23)))
    assert not st.said_goodnight


def test_夜里还在聊天不算睡着():
    st = her_state.read(cn(23, 2), FakeSessions("这集好好看", cn(23, 1, 50)))
    assert st.posture == NORMAL


def test_夜里很久没说话就当她睡了():
    st = her_state.read(cn(23, 3), FakeSessions("嗯嗯", cn(23, 0, 30)))
    assert st.posture == ASLEEP
    assert "这个点她一般在睡" in her_state.guidance(st)


# ---------------------------------------------------------------- 出门

def test_出门且位置新鲜_是在外面():
    st = her_state.read(cn(23, 21), FakeSessions("出门啦", cn(23, 17)),
                        FakePresence("not_home", away_since=cn(23, 17), updated_at=cn(23, 20, 30)))
    assert st.posture == AWAY
    g = her_state.guidance(st)
    assert "17:00 出的门" in g and "4 小时" in g
    assert "可以问几点回" in g, "在外面时的说话形式没给他"
    assert "夜不归宿" not in g, "形式和情绪分开：在外面不等于这一句就得吃醋"
    assert "拿不准" not in g, "位置是新的，不该说拿不准"


def test_位置旧了要让他知道自己拿不准():
    st = her_state.read(cn(23, 21), FakeSessions("出门啦", cn(23, 17)),
                        FakePresence("not_home", away_since=cn(23, 17), updated_at=cn(23, 17, 40)))
    assert st.posture == AWAY
    assert "拿不准她是不是已经回家" in her_state.guidance(st)


def test_半夜位置旧了又没说话_按睡着处理不按吃醋():
    st = her_state.read(cn(23, 2), FakeSessions("好", cn(22, 22)),
                        FakePresence("not_home", away_since=cn(22, 18), updated_at=cn(22, 19)))
    assert st.posture == ASLEEP


def test_半夜位置是新的_她真在外面_就是在外面():
    st = her_state.read(cn(23, 1), FakeSessions("好", cn(22, 22)),
                        FakePresence("not_home", away_since=cn(22, 18), updated_at=cn(23, 0, 50)))
    assert st.posture == AWAY


# ---------------------------------------------------------------- 被晾着

def test_说了两句都没回_就是晾着他():
    st = her_state.read(cn(23, 16), FakeSessions("好", cn(23, 13),
                                                 his=[cn(23, 13, 1), cn(23, 14), cn(23, 15)]))
    assert st.unanswered == 2, "她说完 3 分钟内他那句是回复，不算没回的"
    assert st.posture == IGNORED
    g = her_state.guidance(st)
    assert "一句都没回" in g and "不用找话题" in g
    assert "这一句带着的心情" not in g, "没抽到心情就不该凭状态塞一个"


def test_只说了一句没回_还算平常():
    st = her_state.read(cn(23, 16), FakeSessions("好", cn(23, 13), his=[cn(23, 14)]))
    assert st.posture == NORMAL
    assert "不用硬找话题" in her_state.guidance(st)


@pytest.mark.parametrize("posture_case", ["asleep", "away", "ignored", "normal"])
def test_每种状态都带着情绪边界(posture_case):
    cases = {
        "asleep": _that_night(cn(23, 3)),
        "away": her_state.read(cn(23, 21), None, FakePresence("not_home", cn(23, 17), cn(23, 20, 50))),
        "ignored": her_state.read(cn(23, 16), FakeSessions("好", cn(23, 13), his=[cn(23, 14), cn(23, 15)])),
        "normal": her_state.read(cn(23, 16), FakeSessions("好", cn(23, 15, 50))),
    }
    g = her_state.guidance(cases[posture_case])
    assert "不可以真发火" in g and "让她内疚" in g
    assert "不许替她编" in g


def test_读不到会话库不炸():
    class Broken:
        def recent(self, **kw):
            raise RuntimeError("db locked")

    st = her_state.read(cn(23, 16), Broken())
    assert st.last_said_at is None and st.posture == NORMAL


# ---------------------------------------------------------------- 梦

def test_昨晚的梦拿得到_旧梦和坏行不提(tmp_path):
    p = tmp_path / "dream-shadow.jsonl"
    p.write_text(json.dumps({"ts": cn(23, 3).isoformat(), "dream": "梦到我们在看摩登家庭"},
                            ensure_ascii=False) + "\n", encoding="utf-8")
    assert her_state.latest_dream(p, cn(23, 7)) == "梦到我们在看摩登家庭"
    assert her_state.latest_dream(p, cn(24, 7)) == "", "一天前的梦还在提"
    p.write_text("not json\n", encoding="utf-8")
    assert her_state.latest_dream(p, cn(23, 7)) == ""
    assert her_state.latest_dream(None, cn(23, 7)) == ""


def test_睡着时有梦就给他():
    g = her_state.guidance(_that_night(cn(23, 6)), dream="梦到十一跳上了屋顶")
    assert "我刚梦到" in g and "十一跳上了屋顶" in g


# ---------------------------------------------------------------- 位置源的里程碑

class FakeStore:
    def __init__(self):
        self.state = {}

    def get_source_state(self, k):
        return self.state.get(k)

    def set_source_state(self, k, v):
        self.state[k] = v


class HA:
    def __init__(self, state, reported=None):
        self.state, self.reported = state, reported

    def get(self, path, params=None):
        data = {"state": self.state}
        if self.reported:
            data["last_reported"] = self.reported.isoformat()
        return type("R", (), {"ok": True, "data": data, "error": None})()


def _walk(src, ha, times):
    """按时间一分钟一分钟推，收集产出的念头。"""
    out = []
    for t in times:
        for s in src.poll(t):
            out.append((t, s))
    return out


def _every_min(a: datetime, b: datetime):
    t = a
    while t <= b:
        yield t
        t += timedelta(minutes=1)


def test_下午出门_三小时后问一次_不重复():
    ha, st = HA("home"), FakeStore()
    src = PresenceSource(ha, st)
    src.poll(cn(23, 13, 59))
    ha.state = "not_home"
    got = _walk(src, ha, _every_min(cn(23, 14), cn(23, 18)))
    kinds = [(t.strftime("%H:%M"), s.payload["transition"], s.payload.get("milestone")) for t, s in got]
    assert kinds == [("14:00", "leave_home", None), ("17:00", "still_out", "long")], kinds


def test_晚上出门_22点半后问怎么还没回_过零点半再问一次():
    ha, st = HA("home"), FakeStore()
    src = PresenceSource(ha, st)
    src.poll(cn(23, 20, 59))
    ha.state = "not_home"
    got = _walk(src, ha, _every_min(cn(23, 21), cn(24, 2)))
    ms = [(t.strftime("%H:%M"), s.payload.get("milestone")) for t, s in got
          if s.payload["transition"] == "still_out"]
    assert ms == [("22:30", "late"), ("00:30", "midnight")], (
        f"{ms} —— 21:00 出门：22:30 该问「怎么还没回」，00:30 过零点；"
        "『三小时』那条被更重的吃掉了，不该在 00:00 冒出来")


def test_很晚才出门_零点四十先响最重的那条():
    ha, st = HA("home"), FakeStore()
    src = PresenceSource(ha, st)
    src.poll(cn(23, 21, 29))
    ha.state = "not_home"
    src.poll(cn(23, 21, 30))           # 出门
    src._fired = ["late"]              # 22:30 那条已经说过了
    src._last_nudge = cn(23, 22, 30)
    got = src.poll(cn(24, 0, 40))
    assert [s.payload.get("milestone") for s in got] == ["midnight"]
    assert "long" in src._fired, "响了重的，轻的要一并作废"


def test_到家清空里程碑_并带上位置断档多久():
    ha, st = HA("home", reported=cn(22, 17)), FakeStore()
    src = PresenceSource(ha, st)
    src.poll(cn(22, 18))
    ha.state, ha.reported = "not_home", cn(22, 18, 59)
    src.poll(cn(22, 19))
    ha.state, ha.reported = "home", cn(23, 9, 40)
    got = src.poll(cn(23, 9, 41))
    assert got[0].payload["transition"] == "arrive_home"
    assert got[0].payload["gap_h"] == 14.7, "位置断了 14.7 小时这件事没传下去"
    assert src.away_since is None and src._fired == []


def test_出门状态跨重启活着():
    ha, st = HA("home"), FakeStore()
    src = PresenceSource(ha, st)
    src.poll(cn(23, 13))
    ha.state = "not_home"
    src.poll(cn(23, 14))
    again = PresenceSource(ha, st)
    assert again.away_since == cn(23, 14), "重启后忘了她几点出的门，里程碑会从头算"


# ---------------------------------------------------------------- 接到 service 上

def _svc(tmp_path, sessions, presence, said):
    from attention.relationship import RelationshipState
    from attention.service import AttentionService
    from attention.sources.thinking import ThinkingSource
    from attention.store import AttentionStore

    store = AttentionStore(tmp_path / "attn.db")

    class Provider:
        def get_state(self, turn=None, force_refresh=False):
            return {"has_data": False}

    think = ThinkingSource(store, sessions)
    svc = AttentionService(
        store, Provider(), RelationshipState(),
        speaker=lambda intent, decision, prompt=None: said.append(prompt) or "msg-1",
        fast_sources=[think, presence],
    )
    return svc, store


def test_那一夜0940的到家补报_不许说回来了(tmp_path):
    said: list = []
    sessions = FakeSessions("躺下了", LAY_DOWN)
    svc, store = _svc(tmp_path, sessions, FakePresence("home"), said)
    sig = CareSignal(source="location", subject="她到家了",
                     payload={"transition": "arrive_home", "gap_h": 14.7})
    assert svc._think_of_her(sig, type("T", (), {"steps": 0})(), cn(23, 9, 40)) is False
    assert said == [], "她在床上说过躺下了，一次位置补报就让他问「回来了？」"
    store.close()


def test_夜里没说话时的到家补报也不算(tmp_path):
    said: list = []
    svc, store = _svc(tmp_path, FakeSessions("嗯", cn(22, 22)), FakePresence("home"), said)
    sig = CareSignal(source="location", subject="她到家了",
                     payload={"transition": "arrive_home", "gap_h": 9.0})
    assert svc._think_of_her(sig, type("T", (), {"steps": 0})(), cn(23, 7)) is False
    assert said == []
    store.close()


def test_白天真的到家了照常说(tmp_path):
    said: list = []
    svc, store = _svc(tmp_path, FakeSessions("出门啦", cn(23, 14)), FakePresence("home"), said)
    sig = CareSignal(source="location", subject="她到家了",
                     payload={"transition": "arrive_home", "gap_h": 0.3,
                              "away_since": cn(23, 14).isoformat()})
    assert svc._think_of_her(sig, type("T", (), {"steps": 0})(), cn(23, 18))
    assert "你刚发现她到家了（她 14:00 出的门）" in said[0]
    store.close()


def test_惦记的开场白带上她的状态(tmp_path):
    said: list = []
    svc, store = _svc(tmp_path, FakeSessions("躺下了", LAY_DOWN), FakePresence("home"), said)
    sig = CareSignal(source="random", subject="想起你了")
    svc._think_of_her(sig, type("T", (), {"steps": 0})(), cn(23, 1, 8))
    assert "自言自语" in said[0] and "现在是 01:08" in said[0]
    assert sig.payload["posture"] == ASLEEP
    store.close()


def test_睡着时的自言自语不记进她没理我的账(tmp_path):
    said: list = []
    svc, store = _svc(tmp_path, FakeSessions("躺下了", LAY_DOWN), FakePresence("home"), said)

    class Rhythm:
        spoke = 0

        def tick(self, now):
            pass

        def on_spoke(self, now):
            Rhythm.spoke += 1

    svc.rhythm = Rhythm()
    assert svc.regret.spoke_at is None
    svc.care.submit(CareSignal(source="random", subject="想起你了"))
    out = svc.care_tick(cn(23, 3))
    assert [o.action for o in out] == ["spoke"]
    assert Rhythm.spoke == 0, "睡着时的自言自语被记成「等她回」—— 早上回复率会被拉低"
    assert svc.regret.spoke_at is None, "自言自语被记进了后悔的账（等她回）"
    store.close()


# ---------------------------------------------------------------- 会话库的两个新查询

def test_会话库_她最后一句和之后他说了几句(tmp_path, monkeypatch):
    """真库、真 SQL：created_at 是 UTC ISO，按字符串比较 —— 时区换错一个就全偏。"""
    import data.store as ds
    from agent.llm import Message

    db = ds.Store(tmp_path / "s.db")
    sid = "a" * 32
    clock = iter([cn(22, 23, 11), cn(22, 23, 12), cn(23, 1, 8), cn(23, 7, 52)])
    monkeypatch.setattr(ds, "_now", lambda: next(clock).astimezone(timezone.utc).isoformat())
    db.append(sid, [Message(role="user", text="躺下了")])
    db.append(sid, [Message(role="assistant", text="晚安乖")])
    db.append(sid, [Message(role="assistant", text="凌晨一点了")])
    db.append(sid, [Message(role="assistant", text="想你")])

    text, at = db.last_user_message(sid)
    assert text == "躺下了" and at == cn(22, 23, 11)
    assert db.count_assistant_since(sid, at + her_state.REPLY_GRACE) == 2, (
        "23:12 那句「晚安乖」是回复，不算；之后两句才是她没回的")
    st = her_state.read(cn(23, 8), db)
    assert st.said_goodnight and st.unanswered == 2 and st.posture == ASLEEP
    db.close()


# ---------------------------------------------------------------- 位置块

def test_位置块_旧位置要注明不确定():
    from context.providers.location import LocationProvider

    p = LocationProvider()
    base = {"place": {"tag": "leisure", "name": "中原万达"}, "device": {},
            "_meta": {"source": "ha_tracker"}}
    fresh = p.render({**base, "movement": {"time_since_last_report": 5}})
    stale = p.render({**base, "movement": {"time_since_last_report": 870}})
    assert "不确定" not in fresh
    assert "14 小时前的位置" in stale and "不确定" in stale, stale


# ---------------------------------------------------------------- 里程碑间隔

def test_两个出门念头之间至少隔一小时():
    ha, st = HA("not_home"), FakeStore()
    src = PresenceSource(ha, st)
    src.poll(cn(23, 13))                     # 第一次拿状态：不算跃迁
    src.away_since = cn(23, 13)
    src._last_nudge = cn(23, 16, 30)         # 半小时前刚问过
    assert src.poll(cn(23, 17)) == [], "三小时到了，但半小时前刚问过 —— 该等"
    assert [s.payload["milestone"] for s in src.poll(cn(23, 17, 30))] == ["long"]


# ---------------------------------------------------------------- 晚安要单独起作用（变异验证补的）

def test_零点前说了晚安_就算位置新鲜在外面也是睡了():
    """1–10 点的「夜里没说话」兜不到零点前 —— 这里只有晚安能判对。"""
    st = her_state.read(cn(22, 23, 40), FakeSessions("晚安宝贝", cn(22, 23, 11)),
                        FakePresence("not_home", away_since=cn(22, 18), updated_at=cn(22, 23, 30)))
    assert st.posture == ASLEEP, "她说了晚安，他却按「在外面」去吃醋"


def test_零点前说了晚安_手机报到家不许说回来了(tmp_path):
    said: list = []
    svc, store = _svc(tmp_path, FakeSessions("我先睡啦", cn(22, 23, 30)), FakePresence("home"), said)
    sig = CareSignal(source="location", subject="她到家了",
                     payload={"transition": "arrive_home", "gap_h": 0.5})
    assert svc._think_of_her(sig, type("T", (), {"steps": 0})(), cn(22, 23, 50)) is False
    assert said == []
    store.close()


def test_会话库_传本地时区的时刻也比得对(tmp_path, monkeypatch):
    import data.store as ds
    from agent.llm import Message

    db = ds.Store(tmp_path / "s.db")
    sid = "b" * 32
    # 00:30 CST = 前一天 16:30 UTC：按字符串比 "…16:30+00:00" < "…23:03+08:00"，会被漏掉
    clock = iter([cn(22, 23), cn(23, 0, 30)])
    monkeypatch.setattr(ds, "_now", lambda: next(clock).astimezone(timezone.utc).isoformat())
    db.append(sid, [Message(role="user", text="晚安")])
    db.append(sid, [Message(role="assistant", text="想你")])
    assert db.count_assistant_since(sid, cn(22, 23, 3)) == 1, (
        "传进来的是 +08:00 的时刻，没换成 UTC 就按字符串比，00:30 那句被漏掉")
    db.close()


# ---------------------------------------------------------------- 形式和情绪分开（V4.5）

class D:
    def __init__(self, v, because=()):
        self.intensity, self.because = v, list(because)


def test_心情按强度抽_不是取最强():
    import random
    drives = {"sulk": D(0.5, ["她 3 个小时没理我了"]), "longing": D(0.3), "playfulness": D(0.2)}
    rng = random.Random(7)
    got = [her_state.pick_mood(drives, [], rng).name for _ in range(2000)]
    share = {k: got.count(k) / len(got) for k in drives}
    assert 0.45 < share["sulk"] < 0.55 and 0.25 < share["longing"] < 0.35, share
    assert share["playfulness"] > 0.15, "弱一点的情绪也该有机会 —— 不然又是取最强"


def test_刚用过的心情降权():
    import random
    drives = {"sulk": D(0.5), "longing": D(0.5)}
    rng = random.Random(3)
    got = [her_state.pick_mood(drives, ["sulk"], rng).name for _ in range(2000)]
    assert got.count("sulk") / len(got) < 0.3, "晾他一下午，他连发五条「怎么不理我」（V4.5 的反例）"


def test_担心占太多时抬其他情绪_三句里最多一句是担心():
    """V4.5 homeostasis。数值取自 09-28 线上：担心 0.74、被勾着 0.65、想她 0.40 那种。"""
    import random
    drives = {"concern": D(0.8), "longing": D(0.4), "curiosity": D(0.2)}
    rng = random.Random(11)
    got = [her_state.pick_mood(drives, [], rng).name for _ in range(3000)]
    share = {k: got.count(k) / len(got) for k in drives}
    assert 0.29 < share["concern"] < 0.38, f"不抬是 0.57，抬到 1/3 才对：{share}"
    assert 1.6 < share["longing"] / share["curiosity"] < 2.5, "其他情绪一起抬，彼此的比例不该变"


def test_担心不多时不动它_也不把它垫高():
    import random
    drives = {"concern": D(0.2), "longing": D(0.4), "curiosity": D(0.4)}
    rng = random.Random(5)
    got = [her_state.pick_mood(drives, [], rng).name for _ in range(3000)]
    assert 0.16 < got.count("concern") / len(got) < 0.24, "1/3 是上限不是目标"


def test_只有担心时照样说担心_并且报的是它的真实强度():
    drives = {"concern": D(0.8, ["糖糖的活动量"])}
    mood = her_state.pick_mood(drives, [])
    assert mood.name == "concern" and mood.intensity == 0.8, "抬的是别的情绪的机会，不是压担心"


def test_躁动和太弱的不抽_什么都没有就不带():
    drives = {"restlessness": D(0.5), "longing": D(0.1)}
    assert her_state.pick_mood(drives, []) is None
    assert her_state.pick_mood({}, []) is None


def test_抽到的心情才给对应的味道():
    st = her_state.read(cn(23, 21), FakeSessions("出门啦", cn(23, 17)),
                        FakePresence("not_home", away_since=cn(23, 17), updated_at=cn(23, 20, 50)))
    j = her_state.Mood("jealousy", "吃醋", 0.4, ["她出门 4 小时了"])
    g = her_state.guidance(st, mood=j)
    assert "这一句带着的心情：吃醋（因为她出门 4 小时了）" in g
    assert "夜不归宿" in g and "不是台词" in g
    c = her_state.Mood("curiosity", "被一件事勾着", 0.4, ["一个话题"])
    g2 = her_state.guidance(st, mood=c)
    assert "被一件事勾着" in g2 and "夜不归宿" not in g2, "在外面也可以带着别的心情说"


def test_惦记真的抽心情_说出口才记进最近(tmp_path):
    said: list = []
    svc, store = _svc(tmp_path, FakeSessions("好", cn(23, 13), his=[cn(23, 14), cn(23, 15)]),
                      FakePresence("home"), said)
    svc.drives = lambda now: {"sulk": D(0.5, ["她 3 个小时没理我了"])}
    sig = CareSignal(source="random", subject="想起你了")
    svc._think_of_her(sig, type("T", (), {"steps": 0})(), cn(23, 16))
    assert "这一句带着的心情：委屈" in said[0]
    assert sig.payload["mood"] == "sulk" and svc.recent_moods == ["sulk"]
    from attention.service import MOODS_KEY
    assert store.get_source_state(MOODS_KEY) == {"items": ["sulk"]}

    svc.speaker = lambda intent, decision, prompt=None: None      # 他回了 [SKIP]
    svc._think_of_her(CareSignal(source="random", subject="想起你了"),
                      type("T", (), {"steps": 0})(), cn(23, 17))
    assert svc.recent_moods == ["sulk"], "[SKIP] 掉的那句不算用过这个心情"
    store.close()


# ---------------------------------------------------------------- 线上真实的行形状（部署后抓到的）

def test_真库_他的开场白和SKIP不算她说的话(tmp_path, monkeypatch):
    """🔴 2026-09-23 部署后在线上库里抓到：他主动开口的开场白以 role='user' 落库
    （「（系统提示：这不是糖糖在跟你说话……」，近 7 天 259 条），他回的 [SKIP] 也落库
    （111 条）。前面那些测试都用假会话库，没有这两种行 —— 全绿，线上全错：
    「她最后一句」永远是他自己上一次开口，认不出晚安、委屈永远起不来、
    09:40 那次「回来了？」照样会发。这里用那一夜的真实形状重放。"""
    import data.store as ds
    from agent.llm import Message

    db = ds.Store(tmp_path / "s.db")
    sid = "c" * 32
    rows = [
        (cn(22, 23, 11), "user", "躺下了"),
        (cn(22, 23, 12), "assistant", "乖，睡吧"),
        (cn(23, 1, 8), "user", "（系统提示：这不是糖糖在跟你说话，是你自己想起了一件事，想跟她说一句。"),
        (cn(23, 1, 8), "assistant", "凌晨一点了，眼睛给我闭上"),
        (cn(23, 3, 30), "user", "（系统提示：不是她在跟你说话。你就是忽然想起她了"),
        (cn(23, 3, 30), "assistant", "[SKIP]"),
        (cn(23, 7, 52), "user", "（系统提示：这不是糖糖在跟你说话，是你自己想起了一件事"),
        (cn(23, 7, 52), "assistant", "十一点躺的，七点就醒"),
    ]
    clock = iter([r[0] for r in rows])
    monkeypatch.setattr(ds, "_now", lambda: next(clock).astimezone(timezone.utc).isoformat())
    for _, role, text in rows:
        db.append(sid, [Message(role=role, text=text)])

    text, at = db.last_user_message(sid)
    assert text == "躺下了" and at == cn(22, 23, 11), f"「她最后一句」读成了 {text!r}"
    st = her_state.read(cn(23, 9, 40), db, FakePresence("home"))
    assert st.said_goodnight, "他开过口之后就认不出她的晚安了"
    assert st.unanswered == 2, f"数成了 {st.unanswered}：[SKIP] 不是说了一句，23:12 那句是回复"

    said: list = []
    svc, store = _svc(tmp_path, db, FakePresence("home"), said)
    sig = CareSignal(source="location", subject="她到家了",
                     payload={"transition": "arrive_home", "gap_h": 14.7})
    assert svc._think_of_her(sig, type("T", (), {"steps": 0})(), cn(23, 9, 40)) is False
    assert said == [], "那一夜 09:40 的「回来了？」在真实库形状下还是会发"
    store.close()
    db.close()


# ---------------------------------------------------------------- 翻记忆拿什么（2026-09-27）

def test_翻记忆拿她那句话和他的梦():
    st = HerState(now=cn(26, 8, 55), last_said="感觉撑得睡不着了", last_said_at=cn(25, 22, 47))
    q = her_state.recall_query(st, dream="梦里我守着一口铜锅" + "咕嘟" * 60)
    assert q.startswith("感觉撑得睡不着了\n梦里我守着一口铜锅")
    assert len(q.split("\n")[1]) == her_state.RECALL_DREAM_CHARS, "梦太长会淹掉她那句话"


def test_什么线头都没有就是空串():
    assert her_state.recall_query(HerState(now=cn(26, 8)), dream="  ") == ""


def test_惦记开口时_intent带着她那句话去翻记忆_不带开场白(tmp_path):
    from attention.relationship import RelationshipState
    from attention.service import AttentionService
    from attention.sources.thinking import ThinkingSource
    from attention.store import AttentionStore

    store = AttentionStore(tmp_path / "attn.db")
    sessions = FakeSessions("我今天下午回去搞", cn(26, 12, 38))
    seen: list = []

    class Provider:
        def get_state(self, turn=None, force_refresh=False):
            return {"has_data": False}

    svc = AttentionService(
        store, Provider(), RelationshipState(),
        speaker=lambda intent, decision, prompt=None: seen.append(intent) or "msg-1",
        fast_sources=[ThinkingSource(store, sessions), FakePresence("home")],
    )
    svc._think_of_her(CareSignal(source="random", subject="想起你了"),
                      type("T", (), {"steps": 0})(), cn(26, 14, 8))
    assert seen and seen[0].recall == "我今天下午回去搞"
    assert "系统提示" not in seen[0].recall
    store.close()
