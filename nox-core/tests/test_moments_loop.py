"""Moments 的循环（T5）：节奏、每日上限、间隔、随机。

规格见 `Caelum-Moments-设计.md` 第三节「节奏」和第七节「线上验证：两步走」，
实现在 `moments/loop.py`。

## 这一组守的是什么

🔴 **闸门顺序 + 落盘。** 顺序错了 `reason` 就对不上：先算上限再算冲动的话，
「今天已经发满 2 条」会盖住「这一 tick 本来也没想发」，shadow 跑出来的分布
整片都是 `daily_cap`，看不出他一天真正想发几条。第 3~9 条各钉一个闸门。

🔴 **跨重启记得今天发过几条。** 计数只在内存里的话，每次部署完 Nox 都以为
自己今天还没说话，可以立刻再发一条（第 5 条）。跨天只清 `count`、
**不清 `last_post_at`**（第 8 条）—— 清了的话昨晚 23:50 发过，
今天 00:05 还能再发，`MIN_GAP` 在零点破一个洞，而且不报错。

🔴 **shadow 只算不发。** 第 12 条挡的是「shadow 里落了帖」和
「shadow 偷偷吃掉了真实配额」。

🔴 **没过阈值绝不掷骰子。** 第 3 条断言假 rng 一次都没被调过 ——
随机不是随机抽时间，是随机发生在有理由的内在状态上（设计文档第三节）。

🔴 **心跳记的是「这一 tick 跑完了」，不是「这一 tick 发了帖」。** 第 15 条
把第 3~9 步每一条返回路径都点了一遍；`off` 时一次都不许 beat。

## 每条测试能挡什么、不能挡什么

都写在各自 docstring 里（CAELUM-MAP 第三·五节：说不清自己在防什么的测试，
下次重构会被当噪音删掉）。

⚠️ 不打真网络、不调真模型、不碰真库：假 store 是个内存 dict，
假 bridge 只记账，`writer.generate` / `writer.post` 被 monkeypatch 换掉。
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 走模块名而不是直接 import 那几个函数：`loop.py` 里还没写出来的那一步，
#: 每条测试各自红（AttributeError），不会被一个 collection error 整个盖掉 ——
#: 红得看得见，才知道自己红的是什么
from attention.resonance import Drive  # noqa: E402
from moments import loop, writer  # noqa: E402

#: 一个固定的「现在」。UTC 12:00 = 她那边 20:00（CST），**同一天**，
#: 所以本地日和 UTC 日在大部分用例里一致 —— 只有跨零点那两条故意错开。
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- 假货


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
    """有 `.drives(now)` 和 `.longing.last_contact`，和真 attention 服务同一形状。

    `drives()` 返回的是**真的** `Drive`（`attention/resonance.py`），
    不是个只有 intensity 的替身 —— 真货改了字段，这里跟着红，
    测试才不会在形状变了之后继续绿着。
    """

    def __init__(self, drives: dict | None = None, last_contact=None,
                 raise_on_drives: bool = False) -> None:
        self._drives = dict(drives or {})
        self.longing = SimpleNamespace(last_contact=last_contact)
        self.raise_on_drives = raise_on_drives
        self.calls: list = []

    def drives(self, now):
        self.calls.append(now)
        if self.raise_on_drives:
            raise RuntimeError("resonance 挂了")
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
        self.calls: list[tuple] = []

    def messages_between(self, start_iso: str, end_iso: str) -> list[dict]:
        self.calls.append((start_iso, end_iso))
        return [dict(m) for m in self._messages]


class FakeBridge:
    """记下被调了什么，不打网络。"""

    def __init__(self, post_id: str = "post-1", recent: list | None = None) -> None:
        self.post_id = post_id
        #: 最近发过的正文，`GET /api/moments` 会返回的形状（`items[].body`）
        self.recent = list(recent or [])
        self.posts: list[dict] = []
        self.gets: list[dict] = []

    def post(self, path: str, body: dict | None = None):
        self.posts.append({"path": path, "body": body})
        return SimpleNamespace(ok=True, data={"id": self.post_id}, error=None)

    def get(self, path: str, params: dict | None = None):
        self.gets.append({"path": path, "params": params})
        return SimpleNamespace(
            ok=True, data={"items": [{"body": b} for b in self.recent]}, error=None)


class FakeRng:
    """`.random()` 返回预设值，并数被调了几次 —— 第 3 条钉的就是「不许掷」。"""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value
        self.calls = 0

    def random(self) -> float:
        self.calls += 1
        return self.value


def _fake_writer(monkeypatch, *, body: str | None = "今天风挺大，窗户没关",
                 post_id: str | None = "post-1", boom_at: str | None = None) -> dict:
    """把 `writer.generate` / `writer.post` 换成记账版。

    `body=None` → 生成失败；`post_id=None` → 落库失败；
    `boom_at="generate"/"post"` → 那个函数抛异常。三种坏法都要各自红一条。
    """
    calls: dict[str, list] = {"generate": [], "post": []}

    def fake_generate(adapter_ref, drives, recent, impulse_why, clock=""):
        calls["generate"].append({
            "adapter_ref": adapter_ref, "drives": dict(drives),
            "recent": list(recent), "impulse_why": impulse_why, "clock": clock,
        })
        if boom_at == "generate":
            raise RuntimeError("模型炸了")
        return body

    def fake_post(bridge, text, drive, impulse_why):
        calls["post"].append({
            "bridge": bridge, "text": text, "drive": drive,
            "impulse_why": impulse_why,
        })
        if boom_at == "post":
            raise RuntimeError("bridge 炸了")
        return post_id

    monkeypatch.setattr(writer, "generate", fake_generate)
    monkeypatch.setattr(writer, "post", fake_post)
    return calls


class Rig:
    """一次 `post_tick` 的全套假货。**默认值是一条能一路发出去的路**。

    每条测试只改自己要测的那一个旋钮 —— 默认值就是「冲动够、闸门全过、
    骰子中」，所以某条测试红了，红的一定是它自己改的那处。
    """

    def __init__(self, monkeypatch, *, mode: str = "on", state: dict | None = None,
                 drives: dict | None = None, last_contact=..., messages: list | None = None,
                 rng: float = 0.0, now: datetime = NOW, attention_raises: bool = False,
                 body: str | None = "今天风挺大，窗户没关", post_id: str | None = "post-1",
                 boom_at: str | None = None, recent: list | None = None) -> None:
        self.mode = mode
        self.now = now
        self.store = FakeStore(state)
        self.attention = FakeAttention(
            drives=drives if drives is not None else {"longing": 0.7, "playfulness": 0.6},
            last_contact=(
                now - timedelta(minutes=999) if last_contact is ... else last_contact
            ),
            raise_on_drives=attention_raises,
        )
        self.sessions = FakeSessions(messages)
        self.bridge = FakeBridge(post_id=post_id or "post-1", recent=recent)
        self.rng = FakeRng(rng)
        self.calls = _fake_writer(monkeypatch, body=body, post_id=post_id, boom_at=boom_at)

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


def _warnings(caplog) -> int:
    return sum(1 for r in caplog.records if r.levelno == logging.WARNING)


def _loop_source() -> str:
    """`loop.py` 的**文本** —— 第 18 条读的就是它（不是调用图）。"""
    return (Path(__file__).resolve().parents[1] / "moments" / "loop.py").read_text(
        encoding="utf-8")


# ---------------------------------------------------------------- 闸门：模式


def test_off_does_nothing_at_all(monkeypatch):
    """🔴 `mode="off"` 什么都不做：不碰 store、不打 bridge、不调 writer、不掷骰子。

    能挡：把开关检查放在读状态 / 掷骰子 / 调 writer 之后 —— 那样关掉的循环
          照样在花钱、照样可能落帖；也挡「off 时照样 beat 心跳」，
          那会让 `/health` 一直显示这条循环活着（第 15 条另钉一次）。
    不能挡：mode 拼错（`"ON"`）时的行为，那是下一条。
    """
    rig = Rig(monkeypatch, mode="off")

    assert rig.run() is None
    assert rig.store.writes == [], "off 不许碰 store"
    assert rig.bridge.posts == [] and rig.bridge.gets == [], "off 不许打 bridge"
    assert rig.calls["generate"] == [] and rig.calls["post"] == [], "off 不许花钱调模型"
    assert rig.rng.calls == 0, "off 连骰子都不该掷"


def test_unknown_mode_does_nothing_and_warns(monkeypatch, caplog):
    """mode 只认 `off` / `shadow` / `on`。别的值**什么都不做**，但要留一句 WARNING。

    能挡：写成「不是 off 就当 on」——`NOX_MOMENTS=ON`（大写）会让它真的开始
          发帖，而那是这个功能最坏的坏法：一个字母的差别，没人看得出来。
    不能挡：WARNING 的措辞（只数了有没有一条），也没挡大小写之外的其他拼错。
    """
    for mode in ("ON", "yes", "On", "on "):
        rig = Rig(monkeypatch, mode=mode)

        with caplog.at_level(logging.WARNING):
            caplog.clear()
            assert rig.run() is None, f"{mode!r} 不该做任何事"

        assert _warnings(caplog) == 1, f"{mode!r} 该留一句 WARNING"
        assert rig.store.writes == [] and rig.rng.calls == 0
        assert rig.bridge.posts == [] and rig.calls["generate"] == []


# ---------------------------------------------------------------- 闸门：冲动


def test_below_threshold_never_rolls_the_dice(monkeypatch):
    """🔴 没过阈值就**连骰子都不掷**：`dice` / `dice_p` 都是 None，rng 一次都没被调。

    挡的是「随机抽时间」：先掷骰子再算冲动的话，一个心里没事的下午也会
    掷出一堆「差点就发」，那条线就成了定时器加噪声（设计文档第三节）。

    能挡：把 `post_probability` 的 `value < threshold → 0` 去掉（那样概率
          是个负数，`rng.random() >= 负数` 永远成立 → 会发出去）；
          也挡「先掷骰子再判断」的实现。
    不能挡：阈值本身是多少（那是 `impulse.py` 的事），也不能挡骰子中了之后
          走哪条路（第 10、11 条）。
    """
    rig = Rig(monkeypatch, drives={"longing": 0.4},
              last_contact=NOW - timedelta(minutes=5))

    rec = rig.run()

    assert rec is not None and rec.reason == "below_threshold"
    assert rec.posted is False and rec.dice is None and rec.dice_p is None
    assert rig.rng.calls == 0, "没过阈值不许掷骰子"
    assert rig.calls["generate"] == [] and rig.bridge.posts == []


# ---------------------------------------------------------------- 闸门：上限


def test_daily_cap_stops_at_two_even_when_the_impulse_is_high(monkeypatch):
    """🔴 一天最多 2 条（糖糖定）：`count=2` 时冲动再足也是 `daily_cap`。

    这一条的 `last_post_at` 只有 30 分钟前 —— 所以它也钉住了**顺序**：
    上限在间隔之前判，`reason` 才是 `daily_cap` 而不是 `min_gap`。

    能挡：把上限改成 99 / 删掉；把上限判断放到间隔之后（reason 会变成
          `min_gap`，几天后按 reason 数分布时会以为间隔是主要瓶颈）；
          以及「发满之后还调 generate」——那是白花一次模型的钱。
    不能挡：计数从哪来（内存还是落盘），那是第 5、6 条。
    """
    rig = Rig(monkeypatch, state={
        "date": "2026-09-15", "count": 2,
        "last_post_at": (NOW - timedelta(minutes=30)).isoformat(),
    })

    rec = rig.run()

    assert loop.MAX_POSTS_PER_DAY == 2, "糖糖定的是一天两条"
    assert rec is not None and rec.reason == "daily_cap"
    assert rec.posted is False and rec.dice is None and rec.dice_p is None
    assert rig.calls["generate"] == [], "发满了不该再花钱生成正文"
    assert rig.store.writes == [], "没发就不该动计数"


def test_todays_count_survives_a_restart(monkeypatch):
    """🔴 计数落在 `source_state` 里，**重启后照样记得今天发过几条**。

    进程内存里什么都没有，只有库里那份状态 —— 这条挡的就是「计数只存在
    内存里，重启就归零」：每次部署完 Nox 都会以为自己今天还没说话，
    可以立刻再发一条。

    能挡：把状态只留在模块级变量 / 实例属性里（那样这条会红成能发出去）；
          也挡「读了状态但不认 date=（今天是哪天）」的写法。
    不能挡：跨天该不该清（第 9 条）、间隔与零点的关系（第 8 条）。
    """
    rig = Rig(monkeypatch, state={
        "date": "2026-09-15", "count": 2,
        "last_post_at": (NOW - timedelta(hours=30)).isoformat(),
    })

    rec = rig.run()

    assert rec is not None and rec.reason == "daily_cap"
    assert rec.signals.posts_today == 2
    assert rig.calls["generate"] == [] and rig.bridge.posts == []
    assert rig.store.writes == []


# ---------------------------------------------------------------- 闸门：间隔


def test_min_gap_holds_on_both_sides(monkeypatch):
    """🔴 两帖之间至少 3 小时 —— **边界两侧都测**，不然写成 `<=` 也不会红。

    90 分钟前发过 → `min_gap`，连骰子都不掷、也不生成正文；
    181 分钟前 → 过；
    正好 180 分钟 → 过（规格说的是「**不到** MIN_GAP_MIN 分钟」，
    所以 180 本身是放行的 —— 这一档钉的就是 `<` 和 `<=` 的差别）。

    能挡：MIN_GAP 改成别的数、比较方向写反、把间隔判断整个删掉。
    不能挡：跨天时 `last_post_at` 还在不在，那是下一条。
    """
    assert loop.MIN_GAP_MIN == 180, "糖糖定的是 3 小时"

    for minutes, expected in ((90, "min_gap"), (181, None), (180, None)):
        rig = Rig(monkeypatch, state={
            "date": "2026-09-15", "count": 1,
            "last_post_at": (NOW - timedelta(minutes=minutes)).isoformat(),
        })
        rec = rig.run()
        if expected:
            assert rec.reason == expected, f"{minutes} 分钟前该挡住"
            assert rec.posted is False
            assert rec.dice is None and rec.dice_p is None, "没到间隔不许掷骰子"
            assert rig.calls["generate"] == [], "没到间隔不该花钱生成正文"
            assert rig.store.writes == []
        else:
            assert rec.posted is True, f"{minutes} 分钟前已经够远了，该能发"
            assert rig.calls["generate"], "过闸之后该去生成正文"


def test_min_gap_still_holds_across_midnight(monkeypatch):
    """🔴 跨零点照样管住：本地昨天 23:50 发过，本地今天 00:10 不许再发。

    这条挡的是「跨天把 `last_post_at` 一起清掉」。清了的话，`count` 归零
    （跨天该清的）、`last_post_at` 也没了 → 间隔闸门形同不存在，
    0 点过后可以立刻再发一条。**而且不报错**：`reason` 会是空串、
    `posted=True`，日志里看着一切正常。

    能挡：跨天时把整个状态重置（连 `last_post_at` 一起清）。
    不能挡：本地日算得对不对 —— 这里两边差 20 分钟，算错成 8 小时差
          也会是 `min_gap`（那是 `temporal` 的测试该管的）。
    """
    now = datetime(2026, 9, 14, 16, 10, tzinfo=timezone.utc)   # 她那边 09-15 00:10
    last = datetime(2026, 9, 14, 15, 50, tzinfo=timezone.utc)  # 她那边 09-14 23:50
    rig = Rig(monkeypatch, now=now, state={
        "date": "2026-09-14", "count": 2, "last_post_at": last.isoformat(),
    })

    rec = rig.run()

    assert rec is not None and rec.reason == "min_gap", "跨零点这一刻 MIN_GAP 还该在"
    assert rec.reason != "daily_cap", "昨天那两条已经跨天了，不该按今天的上限算"
    assert rec.posted is False
    assert rig.calls["generate"] == [] and rig.bridge.posts == []


# ---------------------------------------------------------------- 闸门：随机


def test_the_dice_can_lose(monkeypatch):
    """骰子没中 → `reason="dice"`，`dice` / `dice_p` 都记下来。

    这一条和「没到阈值」是**两件事**：过了阈值、闸门全过，只是这一 tick
    没轮到。记录里两个数都要在，几天后才知道「他是没想发，还是想发没中」。

    能挡：把 `dice`/`dice_p` 留成 None（那样和「没掷」分不开）；把概率写成
          常量（`dice_p` 就不再随冲动走）；骰子没中还照样调 generate。
    不能挡：概率函数本身对不对，那是第 16 条。
    """
    rig = Rig(monkeypatch, rng=0.99, state={
        "date": "2026-09-15", "count": 1,
        "last_post_at": (NOW - timedelta(minutes=400)).isoformat(),
    })

    rec = rig.run()

    value = rec.impulse.value
    assert rec.reason == "dice" and rec.posted is False
    assert rec.dice == 0.99
    assert rec.dice_p == loop.post_probability(value)
    #: 顺手按公式再算一遍：和实现对不对得上，不能只靠实现自己说
    assert rec.dice_p == pytest.approx(0.15 * (value - 0.45) / 0.55)
    assert rig.rng.calls == 1
    assert rig.calls["generate"] == [], "骰子没中不该花钱生成正文"
    assert rig.bridge.posts == [] and rig.store.writes == []


def test_the_dice_can_win(monkeypatch):
    """骰子中了（`rng < dice_p`）→ 一路发出去。

    能挡：把比较方向写反（`dice <= dice_p` 才发）、把 rng 的返回值丢掉。
    不能挡：发出去之后落了什么，那是第 6 条。
    """
    rig = Rig(monkeypatch, rng=0.0, state={
        "date": "2026-09-15", "count": 1,
        "last_post_at": (NOW - timedelta(minutes=400)).isoformat(),
    })

    rec = rig.run()

    assert rec.posted is True and rec.post_id == "post-1"
    assert rec.dice == 0.0 and rec.dice_p > 0
    assert len(rig.calls["generate"]) == 1 and len(rig.calls["post"]) == 1
    assert rig.rng.calls == 1


def test_shadow_only_computes_and_never_posts(monkeypatch):
    """🔴 shadow 只算不发：骰子中了也**不生成、不落帖、不动计数**。

    这是「线上验证两步走」的第一步（设计文档第七节）：先看它一天想发几条。
    影子模式里落一条真帖是这个功能最坏的一种坏 —— 她会在 Moments 里看到
    一整批本该不存在的帖子，而且事后分不清哪些是实验产物；
    偷偷加 `count` 也一样坏：影子会吃掉真实配额，等开 `on` 的时候
    一天只剩一条。

    能挡：shadow 分支后面忘了 `return`（接着往下真的发一条）；
          shadow 里调 `generate`（白花模型的钱）；shadow 里落盘计数。
    不能挡：`why_not_posted` 的措辞 —— 只钉了「本来会发」这个意思在不在。
    """
    rig = Rig(monkeypatch, mode="shadow", rng=0.0)

    rec = rig.run()

    assert rec.reason == "shadow" and rec.posted is False and rec.post_id is None
    assert rec.dice == 0.0, "shadow 也要照常掷骰子 —— 不然分布是假的"
    assert rec.dice_p is not None and rec.dice_p > 0
    assert "本来会发" in rec.why_not_posted, "影子记录要说清这一刻本来会发"
    assert rig.calls["generate"] == [], "shadow 不花模型的钱"
    assert rig.calls["post"] == []
    assert rig.bridge.posts == [], "shadow 绝不落帖"
    assert rig.store.writes == [], "shadow 不许动计数（那会偷偷吃掉真实配额）"


# ---------------------------------------------------------------- 成功落盘


def test_a_successful_post_is_written_to_the_store(monkeypatch):
    """🔴 发成功之后**先落盘再返回**：`count+1`、`last_post_at=now`、本地今天。

    这三个字段缺一个都会静默坏：少了 count 就一天发无数条，少了
    `last_post_at` 就没有 MIN_GAP，少了 `date` 跨天就永远不清零。
    `last_post_at` 用 **now**（这条帖的时刻），不是「上次的 + 间隔」。

    能挡：删掉落盘那一句（计数只留在内存里）、字段名/类型写错、
          `at` 和 `last_post_at` 用了两个不同的时刻。
    不能挡：落盘失败怎么办（那是 `logger.warning` 之后照样返回，走不出去红）。
    """
    rig = Rig(monkeypatch, messages=[
        {"role": "user", "text": "在吗", "created_at": "2026-09-15T11:00:00+00:00"},
        {"role": "assistant", "text": "在", "created_at": "2026-09-15T11:01:00+00:00"},
    ], recent=["昨天那条"])

    rec = rig.run()

    assert rec.posted is True and rec.post_id == "post-1" and rec.reason == ""
    state = rig.store.source_state["moments"]
    assert state == {
        "date": "2026-09-15",
        "count": 1,
        "last_post_at": NOW.isoformat(),
    }
    assert rig.store.writes == [{"key": "moments", "value": state}]
    #: 「今天」是她的日历天，所以窗口从**她那边**的 00:00 开始（UTC 前一天 16:00）
    assert rig.sessions.calls == [("2026-09-14T16:00:00+00:00", NOW.isoformat())]
    #: 只数她说的（`role == "user"`），不是所有消息
    assert rec.signals.turns_today == 1
    #: 生成和落库拿到的都是**真货**：主导 drive 的名字 + bridge
    assert rig.calls["generate"][0]["drives"] == {"longing": 0.7, "playfulness": 0.6}
    #: 防复读：最近发过的正文要**真的**喂进提示词（设计文档第四节第 3 条）
    assert rig.calls["generate"][0]["recent"] == ["昨天那条"]
    assert rig.calls["post"][0]["bridge"] is rig.bridge
    assert rig.calls["post"][0]["drive"] == "longing", "落库要的是主导 drive 的**名字**"
    assert rig.calls["post"][0]["text"] == "今天风挺大，窗户没关"


def test_the_count_resets_when_the_day_rolls_over(monkeypatch):
    """跨天清 `count`：昨天发满 2 条，今天从 1 开始，不是 3。

    能挡：不清零（今天一开局就是 `daily_cap`，他整天一条都发不出去 ——
          而且是**静默**的，`/health` 全绿）。
    不能挡：`last_post_at` 跨天还在不在，那是第 8 条。
    """
    rig = Rig(monkeypatch, state={
        "date": "2026-09-14", "count": 2,
        "last_post_at": (NOW - timedelta(hours=35)).isoformat(),
    })

    rec = rig.run()

    assert rec.posted is True
    assert rec.signals.posts_today == 0, "跨天之后今天还没发过"
    state = rig.store.source_state["moments"]
    assert state["count"] == 1, "今天的第 1 条，不是昨天的 2+1"
    assert state["date"] == "2026-09-15"
    assert state["last_post_at"] == NOW.isoformat()


# ---------------------------------------------------------------- 失败路径


def test_generate_failure_never_escapes(monkeypatch, caplog):
    """生成挂了两条坏法都要 `write_failed`，而且**异常不许往外冒**。

    这个函数跑在后台线程里：异常冒出去 = 整条循环死掉，而症状不是报错，
    是「他最近怎么不发帖了」。所以「模型返回 None」和「模型接口抛异常」
    在这里必须是同一条路（`write_failed`），而且要和「骰子没中」分开数 ——
    一个是他本来就没想发，一个是坏了。

    能挡：让 `writer.generate` 的异常往外冒、把 None 当空正文往下传
          （那样会落一条空帖）、把 `write_failed` 记成 `dice`。
    不能挡：`writer.generate` 自己内部怎么处理（那是 T4 的测试）。
    """
    rig = Rig(monkeypatch, body=None)
    rec = rig.run()

    assert rec is not None and rec.reason == "write_failed"
    assert rec.posted is False and rec.post_id is None
    assert rig.calls["post"] == [], "没有正文就不该落库"
    assert rig.bridge.posts == [] and rig.store.writes == [], "没发出去不占配额"

    boom = Rig(monkeypatch, boom_at="generate")
    with caplog.at_level(logging.WARNING):
        rec2 = boom.run()  # ← 抛出来的话这条测试就红在异常上

    assert rec2.reason == "write_failed"
    assert boom.bridge.posts == [] and boom.store.writes == []
    assert _warnings(caplog) == 1, "炸了必须留痕，不然线上只有「最近没发帖」"


def test_post_failure_never_escapes_and_does_not_eat_the_quota(monkeypatch, caplog):
    """落库挂了两条坏法都要 `write_failed`，而且**计数不许加**。

    没落成的那条没发出去，占掉配额的话他今天就被自己坑掉一条 ——
    而且是静默的：`count` 加一，帖子里什么都没有。

    能挡：落库失败照样 `set_source_state`；让 bridge 的异常往外冒；
          把 `unposted` 记成成功。
    不能挡：`writer.post` 内部怎么判 `ok`（那是 T4 的测试）。
    """
    rig = Rig(monkeypatch, post_id=None)
    rec = rig.run()

    assert rec is not None and rec.reason == "write_failed"
    assert rec.posted is False and rec.post_id is None
    assert len(rig.calls["post"]) == 1, "落库这一步**真的调过**（不是没调就报失败）"
    assert rig.store.writes == [], "没发出去不许占配额"

    boom = Rig(monkeypatch, boom_at="post")
    with caplog.at_level(logging.WARNING):
        rec2 = boom.run()  # ← 抛出来的话这条测试就红在异常上

    assert rec2.reason == "write_failed"
    assert boom.store.writes == [] and boom.store.source_state == {}
    assert _warnings(caplog) == 1


# ---------------------------------------------------------------- 心跳


class FakeHeartbeat:
    """替掉 `obs.heartbeat`（第 15 条只看「有没有 beat、beat 了几次」）。"""

    def __init__(self) -> None:
        self.beats: list[str] = []
        self.declares: list[tuple] = []

    def beat(self, job: str) -> None:
        self.beats.append(job)

    def declare(self, job: str, *, every_s: float) -> None:
        self.declares.append((job, every_s))


def test_every_finished_tick_beats_the_heartbeat(monkeypatch):
    """🔴 「这一 tick 跑完了」和「这一 tick 发了帖」是两件事。

    心跳记的是**前者**。只在发帖成功时 beat 的话，一条三天没发的循环
    在 `/health` 里看起来是死的 —— 而它其实好好跑着，只是没想发；
    反过来，`off` 的时候一次都不许 beat：关掉的活计在台账里装作活着，
    就会让看门狗以为这条线归它管。

    能挡：只给成功路径加 beat、某条 `return` 提前跑掉忘了 beat（每条路
          各 beat 一次，多 beat 也会红）、off 也 beat。
    不能挡：`declare` 的时机（T6 在启动时调）——这里只钉参数对不对。
    """
    cases = {
        # path: (造一次的 rig, 该走的 reason)
        "below_threshold": (
            lambda: Rig(monkeypatch, drives={"longing": 0.4}), "below_threshold"),
        "daily_cap": (
            lambda: Rig(monkeypatch, state={
                "date": "2026-09-15", "count": 2, "last_post_at": NOW.isoformat(),
            }), "daily_cap"),
        "min_gap": (
            lambda: Rig(monkeypatch, state={
                "date": "2026-09-15", "count": 1,
                "last_post_at": (NOW - timedelta(minutes=90)).isoformat(),
            }), "min_gap"),
        "dice": (lambda: Rig(monkeypatch, rng=0.99), "dice"),
        "shadow": (lambda: Rig(monkeypatch, mode="shadow"), "shadow"),
        "write_failed": (lambda: Rig(monkeypatch, body=None), "write_failed"),
        "posted": (lambda: Rig(monkeypatch), ""),
    }

    for path, (build, expected) in cases.items():
        hb = FakeHeartbeat()
        monkeypatch.setattr(loop, "heartbeat", hb)

        rec = build().run()

        assert rec.reason == expected, f"{path} 走错了路，心跳那条断言就没意义了"
        assert hb.beats == ["post_tick"], f"{path} 该 beat 恰好一次"

    hb = FakeHeartbeat()
    monkeypatch.setattr(loop, "heartbeat", hb)
    assert Rig(monkeypatch, mode="off").run() is None
    assert hb.beats == [], "关掉的活计不该在台账里装作活着"

    hb = FakeHeartbeat()
    monkeypatch.setattr(loop, "heartbeat", hb)
    loop.declare_heartbeat()
    assert hb.declares == [("post_tick", 900)], "节奏要和 TICK_SECONDS 一致"
    assert loop.TICK_SECONDS == 900
    assert hb.beats == [], "declare 不是 beat —— 声明过不等于跑过"


# ---------------------------------------------------------------- 概率函数


def test_post_probability_is_flat_below_the_threshold_and_rises_after(monkeypatch):
    """`post_probability` 穷举：阈值下恒为 0、阈值上单调不减、顶上正好是 P_MAX。

    🔴 「阈值下恒为 0」是这一整套里最要紧的一条：它保证没过阈值的 tick
    **连骰子都不掷**。少了这个 0，概率会变成负数，`rng.random() >= 负数`
    永远成立 —— 一个心里没事的下午也会准点发出帖子来。

    能挡：删掉 `value < threshold → 0`、写成 `<=`、把斜率写成常量、
          让函数在阈值以上反而下降（那样冲动越高越不发）。
    不能挡：`P_MAX` 这个数选得好不好 —— 那是等 shadow 分布出来才调的事。
    """
    assert loop.post_probability(0.0) == 0.0
    assert loop.post_probability(loop.THRESHOLD) == 0.0, "正好等于阈值也是 0"
    assert loop.post_probability(loop.THRESHOLD - 1e-9) == 0.0
    assert loop.post_probability(loop.THRESHOLD + 1e-9) > 0.0
    assert loop.post_probability(1.0) == pytest.approx(loop.P_MAX)

    values = [i / 20 for i in range(21)]          # 0 到 1，21 档
    assert len(values) >= 21
    probs = [loop.post_probability(v) for v in values]

    assert all(p == 0.0 for v, p in zip(values, probs) if v < loop.THRESHOLD)
    assert all(b >= a for a, b in zip(probs, probs[1:])), "越高越该发，不许回头"


def test_drives_failure_does_not_blow_up(monkeypatch, caplog):
    """`attention.drives()` 抛异常 → 当作 `{}`、留一条 WARNING、不往外冒。

    resonance 那条线挂了的表现是「他从此不发帖」，没有报错。兜底成
    「心里没事」是**安全的**方向（冲动 0 → 不发），兜成默认值就等于
    替他想了一件心事。

    能挡：异常往外冒（后台线程整条死掉）、兜一个非空 drives 上去、
           一声不吭地吞掉（那样这个源挂了几年都没人知道）。
    """
    rig = Rig(monkeypatch, drives={"longing": 0.7, "playfulness": 0.6},
              attention_raises=True)

    with caplog.at_level(logging.WARNING):
        rec = rig.run()

    assert rec is not None and rec.reason == "below_threshold"
    assert rec.signals.drives == {}
    assert rec.impulse.inner == 0.0
    assert _warnings(caplog) == 1


def test_loop_source_stays_inside_its_boundaries():
    """🔴 R9 / R10 的源码哨兵（读的是**文本**，不是调用图）。

    R9：UTC+8 只在 `temporal` 包里定义一次。这里再写一遍
        `timezone(timedelta(hours=8))`，「她的一天」就有了第二处口径，
        哪天她出国或者作息挪了，改一处漏一处，而且不报错。
    R10：发帖**不是开口**，不弹她的锁屏。产出路径里出现 `api/push/send`
        或 `notify`，它就成了第五条主动消息渠道（糖糖 2026-08-18 点名）。

    能挡：在这两个边界上偷懒（就地造一个时区 / 顺手推送一下）。
    不能挡：绕开这两个字面写法的做法（T7 的哨兵扫整个 `moments/`）。
    """
    src = _loop_source()

    assert "timezone(timedelta(hours=8" not in src, "R9：本地时区只能在 temporal 里造"
    assert "api/push/send" not in src, "R10：发帖不推送"
    assert "notify" not in src, "R10：发帖不推送"
    assert "from temporal import to_local" in src, "本地日要问 temporal 要"
