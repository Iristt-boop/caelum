"""发帖沿 CareLedger 溯源，但**不许算成一次开口**（T7）。

规格见 `Caelum-Moments-设计.md` 第一节「R1 不变，但要加一条新法则」：
发帖不推送、不弹锁屏，**不是开口**；但 R1 的精神要守 ——
「任何『他为什么说话』必须能沿账本溯源」，所以照记 `source="moment"`。

## 🔴 为什么不能照抄 `note_external_speech`（这个文件存在的全部理由）

设计文档说「照抄 `note_external_speech` 的做法」。**照抄会出错**，
它做的那两件事对发帖都是错的：

  1. `self.threads.open(COMPANY, ...)` —— 开一条关心链。开了之后**后来的源
     会看见它**，于是「他刚发了条朋友圈」会把一次真正该有的关心挤掉。
  2. `ledger.record(decision=SPEAK)` —— 算进「今天开口几次」。而
     `summary()["spoke"]` 正是 `longing.tick(spoke_today=...)` 的入参，
     `SPOKE_DAMPING ** spoke_today` 会压住想念的涨速 ——
     表现成「他发了条朋友圈，于是不那么想她了」。**正好反了。**

所以要的是**第三种形状**：账本照记，但它不进
`considered = spoke + skipped + blocked`，也不开链。`FAILED` 已经是
这个形状的先例（`ledger.py`：「不是决策，是故障」）。

## 每条测试能挡什么、不能挡什么

写在各自 docstring 里（CAELUM-MAP 第三·五节：说不清自己在防什么的测试，
下次重构会被当噪音删掉）。

⚠️ 不打真网络、不调真模型：`writer.generate` / `writer.post` 被 monkeypatch
换掉，假 store 是个内存 dict，假 bridge 只记账。第 5 条用**真的**
`AttentionService`，因为「有没有开出一条链」只能在真货上看。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 走模块名拿 `POSTED`：`ledger.py` 里还没定义的那个常量，只有用它那几条红
#: （AttributeError），别的照常绿 —— 不会被一个 collection error 整个盖掉，
#: 红得看得见才知道自己红的是什么（同 `tests/test_moments_loop.py` 开头）
from attention.care import COMPANY  # noqa: E402
from attention.care import ledger as care_ledger  # noqa: E402
from attention.care.ledger import (  # noqa: E402
    BLOCK,
    FAILED,
    SKIP,
    SPEAK,
    CareLedger,
)
from attention.gate import DailyGate  # noqa: E402
from attention.resonance import Drive  # noqa: E402
from attention.service import AttentionService  # noqa: E402
from attention.store import AttentionStore  # noqa: E402
from moments import loop, writer  # noqa: E402
from obs import heartbeat  # noqa: E402

#: 一个固定的「现在」。UTC 12:00 = 她那边 20:00（CST），**同一天**。
T0 = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def _warnings(caplog) -> int:
    return sum(1 for r in caplog.records if r.levelno == logging.WARNING)


# ---------------------------------------------------------------- 账本：POSTED


def test_posted不算开口():
    """🔴 记一条 `POSTED`：`spoke` 还是 0、`considered` 还是 0，只有 `posted` 是 1。

    能挡：拿 `SPEAK` 凑 —— 那是这个任务最要紧的一处。走 `SPEAK` 的话
          `summary()["spoke"]` 会变成 1，而它正是
          `longing.tick(spoke_today=...)` 的入参：`SPOKE_DAMPING ** spoke_today`
          会压住想念的涨速，表现成「他发了条朋友圈，于是不那么想她了」。
          `considered` 同时会被抬起来，界面上看起来他今天多惦记了她一次。
    不能挡：`record()` 存下来的原文有没有被截断，那是第 7、8 条。
    """
    led = CareLedger()
    led.record(source="moment", decision=care_ledger.POSTED, now=T0)

    s = led.summary(T0)

    assert s["spoke"] == 0, "发帖不是开口"
    assert s["posted"] == 1, "但它确实留了一条痕迹"
    assert s["considered"] == 0, "痕迹不进 considered"


def test_posted不动last_spoke():
    """`last_spoke_at` 不许被 `POSTED` 更新 —— 它记的是「上一次开口」。

    能挡：把 `POSTED` 也当开口（那样 `last_spoke_at` 会往前跳，
          界面上「他多久没主动跟我说过话」会凭空变短）。
    不能挡：`last_spoke_at` 的格式（那是 `record()` 本来就有的字段）。
    """
    led = CareLedger()
    led.record(source="random", decision=SPEAK, now=T0)
    before = led.summary(T0)["last_spoke_at"]
    assert before, "先得真有一条开口，这条测试才说明得了什么"

    led.record(source="moment", decision=care_ledger.POSTED,
               now=T0 + timedelta(minutes=30))

    assert led.summary(T0)["last_spoke_at"] == before, "发帖没让上一次开口变新"


def test_considered的公式没被改():
    """🔴 五种决策各一条 → `considered == 3`（**具体的数字**，不是「大于 0」）。

    能挡：把 `POSTED`（或 `FAILED`）加进 `counts[SPEAK] + counts[SKIP] + counts[BLOCK]`
          那一行 —— 糖糖定的公式是三项，谁都不许改。
          断言写成「大于 0」的话，把 `POSTED` 漏进去照样绿。
    不能挡：每一项各自的计数，那是第 1、4 条。
    """
    led = CareLedger()
    for decision in (SPEAK, SKIP, BLOCK, FAILED, care_ledger.POSTED):
        led.record(source="moment", decision=decision, now=T0)

    s = led.summary(T0)

    assert s["considered"] == 3, "considered 只数 speak + skip + block 三项"
    assert (s["spoke"], s["skipped"], s["blocked"], s["failed"], s["posted"]) == (
        1, 1, 1, 1, 1), "五个计数各算各的，一个都不许漏"


def test_by_source里分得开():
    """`by_source["moment"]` 里 `POSTED` 是 1、`SPEAK` 是 0。

    能挡：`by_source` 的初始字典里漏掉 `POSTED`（那条事件会**静默不计数**，
          界面上「今天发了几条」永远显示 0）；也挡「分了组但组内混在一起」。
    不能挡：别的 source 的分布，那是 `test_ledger.py` 的事。
    """
    led = CareLedger()
    led.record(source="moment", decision=care_ledger.POSTED, now=T0)

    s = led.summary(T0)

    assert s["by_source"]["moment"][care_ledger.POSTED] == 1
    assert s["by_source"]["moment"][SPEAK] == 0, "留痕迹不是开口"


# ---------------------------------------------------------------- note_moment


class _Provider:
    """`AttentionService` 要的那一个方法。什么都不给（服务构造不真取数据）。"""

    def get_state(self, turn=None, force_refresh=False):
        return {}


@pytest.fixture
def svc(tmp_path):
    """**真的** `AttentionService` + **真的** `AttentionStore`（临时库）。

    ⚠️ 这里不拿假货替服务：第 5 条要断的正是「有没有多出一条链」，
    而那只能在真的 `ThreadBook` 上看。假货上断 `== []` 是恒真
    （`docs/LOGGING.md` 说的空集陷阱）。

    🔴 先 `declare` 再让 `_persist()` 走到 `heartbeat.beat`：没声明过的话
    `obs/heartbeat.py` 自己会留一条 WARNING，那会把「记账失败留了几条警告」
    这类计数断错。
    """
    heartbeat.reset()
    store = AttentionStore(tmp_path / "attention.db")
    service = AttentionService(store=store, provider=_Provider(), gate=DailyGate())
    heartbeat.declare("attention_persist", every_s=900)
    try:
        yield SimpleNamespace(service=service, store=store)
    finally:
        store.close()
        heartbeat.reset()


def test_note_moment不开care链(svc):
    """🔴 记一笔痕迹，**关心链一条都不许多**，但账本上要有那 1 条。

    这是本任务最重要的一条：设计文档说「照抄 `note_external_speech`」，
    而那个函数的第一句就是 `self.threads.open(COMPANY, ...)`。
    开了链的后果不是报错 —— 是**后来的源会看见它**，于是
    「他刚发了条朋友圈」会把一次真正该有的关心挤掉，安静地挤掉。

    能挡：照抄 `note_external_speech`（链条数会 1 → 2）、顺手开一条链
          「为了溯源」、顺手 `t.step()` / `t.note()`。
    不能挡：链的**内容**有没有被改（只数了条数）—— 不开链就没有这个问题。
    """
    service = svc.service
    #: 先摆一条**已有的**链当基线：空链书上断「还是 0 条」是恒真，
    #: 照抄过来的那个 `threads.open()` 照样能绿
    service.threads.open(COMPANY, "午饭吃了吗", now=T0)
    before = len(service.threads)
    assert before == 1, "基线得有条链，不然这条测试说明不了什么"

    service.note_moment("abc")

    assert len(service.threads) == before, "发帖不开关心链"
    assert service.ledger.summary()["posted"] == 1, "但账本上要留一笔"


def test_note_moment落盘了(svc):
    """记完之后**能从盘上读回来** —— 只改内存的话重启就没了。

    这里不打断言 `_persist` 被调过（那是从实现那一侧看），而是把
    `source_state` 里那份读出来重建一本账本，再数 `posted`：
    能读回来才是真的落盘了。

    能挡：忘了调 `_persist()`、把状态写进别的 key、写了个读不回来的形状。
    不能挡：`_persist` 失败时的重试（那是 `tests/test_persist_failure.py`）。
    """
    svc.service.note_moment("post-abc")

    saved = svc.store.get_source_state(care_ledger.STATE_KEY)
    assert saved, "账本得落在 source_state 里"
    assert [e["decision"] for e in saved["events"]] == [care_ledger.POSTED]

    reloaded = CareLedger.from_dict(saved)
    assert reloaded.summary()["posted"] == 1, "重启后照样数得出来"


def test_溯源信息齐全(svc):
    """那条事件带齐 `message_id` / `source` / `reason`（含 drive 和冲动那句）。

    账本要回答「他为什么说话」。少了 `message_id` 就找不回是哪条帖子，
    少了 `reason` 就只剩「他发过帖」而不知道为什么 —— 那正是
    `source="moment"` 这一笔存在的全部意义。

    能挡：`record()` 少传一个字段、把 drive 丢了、把 why 丢了。
    不能挡：`reason` 里两段的顺序和分隔符（只断了「都在」）。
    """
    svc.service.note_moment("post-123", drive="longing", why="想她 0.40 领头……")

    ev = svc.service.ledger.events[-1]
    assert ev["message_id"] == "post-123"
    assert ev["source"] == "moment"
    assert ev["decision"] == care_ledger.POSTED
    assert "longing" in ev["reason"], "drive 也要能溯源"
    assert "想她" in ev["reason"], "冲动那句要留着"
    assert "thread_id" not in ev, "没有链，就没有链 id 可写"


def test_why被截短(svc):
    """500 字的 why 落进账本要**短**（≤ 220 = 200 + drive 前缀），而且还能序列化。

    账本一天最多 300 条（`MAX_EVENTS`）。不截的话，正文那一段长文本
    会跟着状态一起膨胀，落在 SQLite 里那份会被一条帖子撑大 ——
    而且是**静默**的，只有盘越来越大。

    能挡：`why` 原样塞进 `reason`；也挡「截了但截出一个塞不进 JSON 的东西」
          （`to_dict()` 是要 `json.dumps` 落库的）。
    不能挡：到底截在 200 还是 180 —— 判据是「不超过 220」，不是「正好 200」。
    """
    why = "想她" * 250
    assert len(why) == 500

    svc.service.note_moment("post-long", drive="longing", why=why)

    reason = svc.service.ledger.events[-1]["reason"]
    assert len(reason) <= 220, f"reason 有 {len(reason)} 字，撑不住"
    assert len(reason) < len(why), "得真的截过"
    assert reason.startswith("longing"), "截的是 why，不是把 drive 截掉"
    json.dumps(svc.service.ledger.to_dict(), ensure_ascii=False)


def test_记账失败不炸(svc, monkeypatch, caplog):
    """`ledger.record` 抛异常 → `note_moment` **不往外抛**，只留一条 WARNING。

    帖子**已经发出去了**。这时候记账失败是「账本里少一笔」，不是
    「这次发帖失败」—— 让异常冒出去，调用方（后台线程里的 `post_tick`）
    会把一条真发出去的帖子当成没发，而日志里只有一段 traceback。

    能挡：不包 try、包了但只 `logger.exception` 不 return（那不算挡，
          这里断的是「没往外抛」）、一声不吭地吞掉（那样账本少一笔
          几个月都没人知道）。
    不能挡：`_persist()` 自己抛异常时的行为 —— 那也在这段 try 里面，
          但这条只点了 `record`。
    """
    def boom(**_kw):
        raise RuntimeError("账本写不进去")

    monkeypatch.setattr(svc.service.ledger, "record", boom)

    with caplog.at_level(logging.WARNING):
        caplog.clear()
        svc.service.note_moment("post-1")   # ← 抛出来的话这条测试就红在异常上

    assert _warnings(caplog) == 1, "失败了必须留痕，不然账本少一笔没人知道"
    assert svc.service.ledger.events == [], "没记成就该是空的，不许塞半条"


# ---------------------------------------------------------------- 假货（loop）
#
# 形状照抄 `tests/test_moments_loop.py` 那套：假 store 是内存 dict、
# 假 bridge 只记账、`writer.generate` / `writer.post` 被 monkeypatch 换掉。
# 唯一多出来的旋钮是 `FakeAttention` 上的 `note_moment`（T7 新增的那一步）。


class FakeStore:
    """内存版的 `attention/store.py` 的 source_state 那半。"""

    def __init__(self, state: dict | None = None) -> None:
        self.source_state: dict[str, dict] = {"moments": dict(state)} if state else {}
        self.writes: list[dict] = []

    def get_source_state(self, key: str) -> dict | None:
        return self.source_state.get(key)

    def set_source_state(self, key: str, value: dict) -> None:
        self.writes.append({"key": key, "value": dict(value)})
        self.source_state[key] = dict(value)


class FakeAttention:
    """`drives()` / `longing.last_contact` 和真服务同形状，外加 `note_moment` 的记账。

    🔴 `note_moment` **挂在实例上，不挂在类上** —— 第 12 条要的正是
    「attention 上根本没有这个方法」（假对象 / 老版本），
    类上定义了的话那条测试就构造不出这个状态来。
    """

    def __init__(self, drives: dict | None = None, last_contact=None,
                 has_note_moment: bool = True,
                 note_moment_raises: bool = False) -> None:
        self._drives = dict(drives or {})
        self.longing = SimpleNamespace(last_contact=last_contact)
        self.has_note_moment = has_note_moment
        self.note_moment_raises = note_moment_raises
        self.moment_calls: list[dict] = []
        if has_note_moment:
            self.note_moment = self._note_moment

    def _note_moment(self, post_id: str, drive: str = "", why: str = "",
                     now=None) -> None:
        if self.note_moment_raises:
            raise RuntimeError("记账炸了")
        self.moment_calls.append(
            {"post_id": post_id, "drive": drive, "why": why, "now": now})

    def drives(self, now):
        return {
            name: Drive(
                name=name, intensity=float(v), load=float(v),
                because=[], evidence=[], source_count=0, computed_at=now,
            )
            for name, v in self._drives.items()
        }


class FakeSessions:
    """`data/store.py` 里 `messages_between` 那一个方法。"""

    def __init__(self, messages: list | None = None) -> None:
        self._messages = list(messages or [])

    def messages_between(self, start_iso: str, end_iso: str) -> list[dict]:
        return [dict(m) for m in self._messages]


class FakeBridge:
    """记下被调了什么，不打网络。"""

    def __init__(self, post_id: str = "post-1", recent: list | None = None) -> None:
        self.post_id = post_id
        self.recent = list(recent or [])
        self.posts: list[dict] = []

    def post(self, path: str, body: dict | None = None):
        self.posts.append({"path": path, "body": body})
        return SimpleNamespace(ok=True, data={"id": self.post_id}, error=None)

    def get(self, path: str, params: dict | None = None):
        return SimpleNamespace(
            ok=True, data={"items": [{"body": b} for b in self.recent]}, error=None)


class FakeRng:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


def _fake_writer(monkeypatch, *, body: str | None = "今天风挺大，窗户没关",
                 post_id: str | None = "post-1") -> dict:
    """把 `writer.generate` / `writer.post` 换成记账版。"""
    calls: dict[str, list] = {"generate": [], "post": []}

    def fake_generate(adapter_ref, drives, recent, impulse_why, clock=""):
        calls["generate"].append({"impulse_why": impulse_why})
        return body

    def fake_post(bridge, text, drive, impulse_why):
        calls["post"].append({"drive": drive, "impulse_why": impulse_why})
        return post_id

    monkeypatch.setattr(writer, "generate", fake_generate)
    monkeypatch.setattr(writer, "post", fake_post)
    return calls


class LoopRig:
    """一次 `post_tick` 的全套假货。**默认值是一条能一路发出去的路。**"""

    def __init__(self, monkeypatch, *, mode: str = "on", state: dict | None = None,
                 drives: dict | None = None, last_contact=..., rng: float = 0.0,
                 now: datetime = T0, body: str | None = "今天风挺大，窗户没关",
                 post_id: str | None = "post-1", has_note_moment: bool = True,
                 note_moment_raises: bool = False) -> None:
        self.mode = mode
        self.now = now
        self.store = FakeStore(state)
        self.attention = FakeAttention(
            drives=drives if drives is not None else {"longing": 0.7, "playfulness": 0.6},
            last_contact=(
                now - timedelta(minutes=999) if last_contact is ... else last_contact
            ),
            has_note_moment=has_note_moment,
            note_moment_raises=note_moment_raises,
        )
        self.sessions = FakeSessions()
        self.bridge = FakeBridge(post_id=post_id or "post-1")
        self.rng = FakeRng(rng)
        self.calls = _fake_writer(monkeypatch, body=body, post_id=post_id)

    def run(self):
        return loop.post_tick(
            mode=self.mode,
            store=self.store,
            attention=self.attention,
            sessions=self.sessions,
            bridge=self.bridge,
            adapter_ref=lambda: None,
            now=self.now,
            rng=self.rng,
        )


# ---------------------------------------------------------------- loop：发成功才记


def test_loop发成功会记一笔(monkeypatch):
    """🔴 发出去之后**恰好记一笔**：`post_id` 是 bridge 给的那个、`why` 是冲动那句。

    这条是「账本能溯源」那一半的入口。少了这一句，账本里就只有开口，
    发帖对它完全隐形（`source="moment"` 永远不会出现）——
    而设计文档第一节要的正是「他为什么说话能沿账本溯源」。

    能挡：整个调用被删掉（`moment_calls == []`）、记了两次（双计会让
          「他留过几条痕迹」这个数翻倍）、传错 `post_id`（拿不到是哪条帖子）、
          传错 `why`（只剩「他发过帖」，不知道为什么）。
    不能挡：`drive` 之外的字段（`recorded_at` 之类）—— 这里只断这三个。
    """
    rig = LoopRig(monkeypatch, post_id="post-777")

    rec = rig.run()

    assert rec.posted is True and rec.post_id == "post-777"
    assert len(rig.attention.moment_calls) == 1, "恰好一次，不多不少"
    call = rig.attention.moment_calls[0]
    assert call["post_id"] == "post-777", "要拿 bridge 给的那个 id"
    assert call["why"] == rec.impulse.why, "冲动那句原样传下去"
    assert call["drive"] == "longing", "主导 drive 的名字（0.7 压着 0.6）"


def test_没发出去就不记账(monkeypatch):
    """🔴 六条「没发出去」的路各跑一次，`note_moment` **一次都不许被调过**。

    记了的话账本里会出现一堆「他留了一条痕迹」而**实际什么都没发生** ——
    而账本是给糖糖看得见的东西：她会去 Moments 里找那条不存在的帖子，
    或者以为他今天发了六条。闸门拦下的那几次本来也不该在这里出现：
    它们各有自己的 reason，那是 `record.py` 三段式要管的。

    ⚠️ 六种各跑一次，**少一条不算完** —— 只测一两条、或者用一个空集去断
    `== []`，都是恒真的通过（`docs/LOGGING.md` 的空集陷阱）。
    下面每一步都先断言 `reason` 走对了路，否则「没记账」证明不了任何事。

    能挡：把那句 `note_moment` 提到闸门之前（`below_threshold` 也记）、
          放进 `_finish`（每条路都记）、挪到 shadow 那条分支上。
    不能挡：`reason` 的措辞（只断等值，不断文案）。
    """
    cases = {
        "below_threshold": (
            dict(drives={"longing": 0.4}, last_contact=T0 - timedelta(minutes=5)),
            "below_threshold"),
        "daily_cap": (
            dict(state={"date": "2026-09-15", "count": 2,
                        "last_post_at": (T0 - timedelta(minutes=30)).isoformat()}),
            "daily_cap"),
        "min_gap": (
            dict(state={"date": "2026-09-15", "count": 1,
                        "last_post_at": (T0 - timedelta(minutes=90)).isoformat()}),
            "min_gap"),
        "dice": (
            dict(rng=0.99, state={
                "date": "2026-09-15", "count": 1,
                "last_post_at": (T0 - timedelta(minutes=400)).isoformat()}),
            "dice"),
        "shadow": (dict(mode="shadow", rng=0.0), "shadow"),
        "write_failed": (dict(body=None), "write_failed"),
    }
    assert len(cases) == 6, "六条路径一条都不能少"

    for path, (kw, expected) in cases.items():
        rig = LoopRig(monkeypatch, **kw)

        rec = rig.run()

        assert rec.reason == expected, f"{path} 走错了路，这条断言就没意义了"
        assert rec.posted is False
        assert rig.attention.moment_calls == [], f"{path} 没发出去，不该记账"


def test_attention没有note_moment也不炸(monkeypatch, caplog):
    """attention 上没有 `note_moment`（假对象 / 老版本）→ 帖子照发，留一句 WARNING。

    这条挡的是**接线期间的空窗**：`moments/loop.py` 先上了、别的实现
    还没跟上时，直接 `attention.note_moment(...)` 会 AttributeError ——
    而它跑在后台线程里，冒出去就是整条循环死掉，症状是「他忽然不发帖了」。
    记不上账是小事，发出去的帖子被当成没发是大事。

    能挡：不做 `getattr` 判断直接调（这条会红在 AttributeError 上）、
          判断了但不吭声（那种「老版本 attention」能跑几个月没人知道）。
    不能挡：`getattr` 拿到一个不可调用的东西时怎么办（没造这个状态）。
    """
    rig = LoopRig(monkeypatch, has_note_moment=False)

    with caplog.at_level(logging.WARNING):
        caplog.clear()
        rec = rig.run()   # ← 抛出来的话这条测试就红在异常上

    assert rec is not None and rec.posted is True and rec.post_id == "post-1"
    assert _warnings(caplog) == 1, "少一次记账要留痕"


def test_记账抛异常不影响帖子(monkeypatch, caplog):
    """`note_moment` 抛异常 → `post_tick` 照样 `posted=True`、`post_id` 还在。

    🔴 帖子**真的发出去了**，瞒着不如记着。让异常冒出去的话调用方会把
    一条已经在她 Moments 里的帖子当成没发（计数不加、日志说失败），
    而现场只剩一段 traceback。

    能挡：不包 try 地调 `note_moment`（红在异常上）、包了但顺手把
          `posted` 改成 False（那会让他重发一条一模一样的）。
    不能挡：记账失败之后账本的内容（那是 `note_moment` 自己的事，
          第 9 条盯着）。
    """
    rig = LoopRig(monkeypatch, note_moment_raises=True)

    with caplog.at_level(logging.WARNING):
        caplog.clear()
        rec = rig.run()   # ← 抛出来的话这条测试就红在异常上

    assert rec is not None
    assert rec.posted is True, "帖子发出去了就是发出去了"
    assert rec.post_id == "post-1"
    assert _warnings(caplog) == 1, "账本没记上要留痕"
