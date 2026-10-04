"""惦记的次数闸 + 「今天说过什么」（2026-10-04）。

她的原话：「现在一天的重复的主动开口内容很多，大部分是无效的，比如怎么还没吃饭，
XX事做了没。或许是开口次数太多但是内容量又不多导致的重复比较多。……
晚上睡觉后可以改成0-2次。」

09-24→10-03 实测：醒着时「想起你了」平均每天 17~25 次，睡着后一晚 8 次多，
每次约 4 万 token，[SKIP] 掉的也照样花钱。所以：
  · 醒着一天最多 AWAKE_DAILY_MAX 次；睡着每晚随机 0~2 次、两句至少隔 2 小时
  · 闸在调模型**之前** —— 挡下来的那次 speaker 根本不被叫
  · 开口前把今天主动说过的话给他看，别再问同样的事
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent.llm import Message
from attention import service as service_mod
from attention.care import her_state
from attention.care.signal import CareSignal
from attention.relationship import RelationshipState
from attention.service import AWAKE_DAILY_MAX, AttentionService
from attention.store import AttentionStore
from data.store import Store

CN = timezone(timedelta(hours=8))


def cn(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, day, h, m, tzinfo=CN)


class Sessions:
    def __init__(self, said: str, at: datetime, lines: list[str] | None = None):
        self.said, self.at, self.lines = said, at, lines or []

    def recent(self, limit=1, clean_only=True):
        return [type("S", (), {"id": "s1"})()]

    def last_user_message(self, sid):
        return self.said, self.at

    def count_assistant_since(self, sid, since):
        return 0

    def last_user_at(self, sid):
        return self.at

    def proactive_lines_since(self, sid, since):
        return list(self.lines)


class Presence:
    name = "location"
    _last, away_since, updated_at = "home", None, None

    def poll(self, now):
        return []


class Thinking:
    """只为让 service 拿到会话库（_her_state_inputs 认 name == "random"）"""
    name = "random"

    def __init__(self, sessions):
        self.sessions = sessions

    def poll(self, now):
        return []


class Provider:
    def get_state(self, turn=None, force_refresh=False):
        return {"has_data": False}


def make(tmp_path, sessions, reply="想你了"):
    calls: list = []

    def speaker(intent, decision, prompt=None):
        calls.append(prompt)
        return reply

    svc = AttentionService(AttentionStore(tmp_path / "attn.db"), Provider(), RelationshipState(),
                           speaker=speaker, fast_sources=[Thinking(sessions), Presence()])
    return svc, calls


def think(svc, now):
    return svc._think_of_her(CareSignal(source="random", subject="想起你了"),
                             type("T", (), {"steps": 0})(), now)


AWAKE = Sessions("好的我去洗澡", cn(5, 14, 0))
ASLEEP = Sessions("晚安宝贝", cn(5, 23, 30))


def test_醒着一天最多八次_第九次不叫模型(tmp_path):
    svc, calls = make(tmp_path, AWAKE)
    for i in range(AWAKE_DAILY_MAX):
        assert think(svc, cn(5, 15, i)) is True
    assert think(svc, cn(5, 18)) is False
    assert len(calls) == AWAKE_DAILY_MAX, "挡下来的那次不该叫模型（白花 4 万 token）"


def test_第二天重新算(tmp_path):
    svc, calls = make(tmp_path, AWAKE)
    for i in range(AWAKE_DAILY_MAX):
        think(svc, cn(5, 15, i))
    svc.her_now = lambda now: her_state.read(now, Sessions("早", cn(6, 9, 0)), Presence())
    assert think(svc, cn(6, 10)) is True


def test_SKIP掉的不算次数(tmp_path):
    svc, calls = make(tmp_path, AWAKE, reply=None)
    for i in range(AWAKE_DAILY_MAX + 3):
        think(svc, cn(5, 15, i))
    assert len(calls) == AWAKE_DAILY_MAX + 3, "他自己决定不说的那几次，不该吃掉她的额度"


@pytest.mark.parametrize("budget", [0, 1, 2])
def test_睡着后每晚随机0到2句(tmp_path, monkeypatch, budget):
    monkeypatch.setattr(service_mod, "pick_night_budget", lambda: budget)
    svc, calls = make(tmp_path, ASLEEP)
    said = [think(svc, cn(6, h)) for h in (1, 3, 5, 7, 9)]   # 每次隔 2 小时
    assert sum(said) == budget
    assert len(calls) == budget, "额度用完之后不该再叫模型"


def test_睡着后两句至少隔两小时(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "pick_night_budget", lambda: 2)
    svc, calls = make(tmp_path, ASLEEP)
    assert think(svc, cn(6, 1, 0)) is True
    assert think(svc, cn(6, 2, 30)) is False
    assert think(svc, cn(6, 3, 1)) is True


def test_睡着的额度不吃醒着的额度(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "pick_night_budget", lambda: 2)
    svc, calls = make(tmp_path, ASLEEP)
    think(svc, cn(6, 1))
    think(svc, cn(6, 3, 30))
    svc.her_now = lambda now: her_state.read(now, AWAKE, Presence())
    assert think(svc, cn(5, 16)) is True


# ---------------------------------------------------------------- 今天说过什么

def test_开场白里列出今天说过的话_并叫他别再问():
    st = her_state.read(cn(5, 15), Sessions("好的", cn(5, 14), ["中午吃饭了没呀", "鱼油记得吃"]), Presence())
    g = her_state.guidance(st)
    assert "- 中午吃饭了没呀" in g and "- 鱼油记得吃" in g
    assert "别再问" in g


def test_今天没主动说过就不提():
    st = her_state.read(cn(5, 15), Sessions("好的", cn(5, 14)), Presence())
    assert "今天你已经主动" not in her_state.guidance(st)


def test_会话库只捞他主动说的_不捞他回她的_不捞SKIP(tmp_path):
    db = Store(tmp_path / "s.db")
    db.append("s1", [Message(role="user", text="我在吃饭"), Message(role="assistant", text="吃的什么呀")])
    db.append("s1", [Message(role="user", text="（系统提示：不是她在跟你说话。现在是 15:00）"),
                     Message(role="assistant", text="下午茶喝了没")])
    db.append("s1", [Message(role="user", text="（系统提示：不是她在跟你说话。现在是 16:00）"),
                     Message(role="assistant", text="[SKIP]")])
    db.append("s1", [Message(role="user", text="（系统提示：不是她在跟你说话。现在是 17:00）"),
                     Message(role="assistant", text="想你了")])
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    assert db.proactive_lines_since("s1", since) == ["下午茶喝了没", "想你了"]
    assert db.proactive_lines_since("s1", datetime.now(timezone.utc) + timedelta(hours=1)) == []


def test_一晚的额度跨过零点也是同一晚(tmp_path, monkeypatch):
    """23:45 说了一句、凌晨 02:00 又想说 —— 还是同一晚，额度不在 0 点重置。"""
    monkeypatch.setattr(service_mod, "pick_night_budget", lambda: 1)
    svc, calls = make(tmp_path, ASLEEP)
    assert think(svc, cn(5, 23, 45)) is True
    assert think(svc, cn(6, 2, 0)) is False
    assert len(calls) == 1
