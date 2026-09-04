"""第 4 道闸：同一件事连着提够几次就收口（2026-09-04）。

## 在防什么

第 3 道闸（SUBJECT_INTERVAL 22h）只管「一天最多一次」，
**管不住「天天都提」**。她连着一周睡不好，他就合规地提七次 ——
每一次都有新数据支撑，每一次都不违反任何规则，
而她听到的是同一句话说了七遍。

她多半不会说，他也就永远不知道。所以这个闸只能靠测试守。

## 🔴 最要紧的一条不是"能压住"

是 **"情况变了必须能突破它"**。

压不住顶多是唠叨；压过头是**对一件正在恶化的事沉默** —— 那严重得多。
所以下面「突破」那一组比「压住」那一组更重要。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.intent import Intent  # noqa: E402
from attention.scheduler import Scheduler  # noqa: E402

CN = timezone(timedelta(hours=8), "CST")


def _cn(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=CN)


def _intent(strength=0.8, subject="睡眠"):
    return Intent(
        subject=subject,
        title="昨晚睡得少",
        reason="只睡了 5.7 小时",
        attention_strength=strength,
    )


def _sched():
    #: 关掉全局冷却，单测第 4 道闸。它们是正交的两道，混在一起测
    #: 会分不清是谁挡下的
    return Scheduler(base_interval=timedelta(0))


def _speak_days(sched, days, strength=0.8, start=None, subject="睡眠"):
    """连着 N 天，每天 21:00 说一次。返回最后一天的时刻。"""
    start = start or _cn(2026, 9, 1, 21)
    for i in range(days):
        t = start + timedelta(days=i)
        sched.note_spoke(_intent(strength, subject), t)
    return start + timedelta(days=days - 1)


# ------------------------------------------------------------ 压得住吗

def test_连着说满三次之后就不再说了():
    s = _sched()
    last = _speak_days(s, 3)
    d = s.tick([_intent(0.8)], last + timedelta(days=1))
    assert not d.will_speak


def test_说两次还能再说第三次():
    """3 是上限不是 2 —— 别把闸关早了。"""
    s = _sched()
    last = _speak_days(s, 2)
    d = s.tick([_intent(0.8)], last + timedelta(days=1))
    assert d.will_speak


def test_被压住时说得出是哪一道闸():
    """⚠️ 「今天说过了」和「说够了」必须分得开。

    dry-run 只能看到 reason，混成一句的话这道闸生效了也没人知道。
    """
    s = _sched()
    last = _speak_days(s, 3)
    d = s.tick([_intent(0.8)], last + timedelta(days=1))
    assert "连着说满" in d.reason

    #: 同一天再来一次，那是第 3 道闸，措辞该不一样
    d2 = s.tick([_intent(0.8)], last + timedelta(hours=1))
    assert "今天说过了" in d2.reason


# ------------------------------------------------------------ 🔴 突破：情况变了

def test_明显恶化要能突破():
    """压过头 = 对正在变糟的事闭嘴。这条比"能压住"更要紧。"""
    s = _sched()
    last = _speak_days(s, 3, strength=0.6)
    d = s.tick([_intent(0.9)], last + timedelta(days=1))
    assert d.will_speak


def test_明显好转也要能突破():
    """「你这两天睡得好多了」是句好话，不是唠叨。

    用绝对值判变化量就是为了这个 —— 只认恶化的话，
    他就成了一个只会报坏消息的人。
    """
    s = _sched()
    last = _speak_days(s, 3, strength=0.9)
    d = s.tick([_intent(0.5)], last + timedelta(days=1))
    assert d.will_speak


def test_小幅浮动不算变了():
    """今天 843 步、明天 1100 步 —— 这种天天都有，不值得重开一次口。"""
    s = _sched()
    last = _speak_days(s, 3, strength=0.80)
    d = s.tick([_intent(0.85)], last + timedelta(days=1))
    assert not d.will_speak


def test_突破之后计数重新算():
    """情况变了是**新的一件事**，不是同一句话的第 4 遍。

    不重置的话，突破一次之后立刻又被压住，等于突破没用。
    """
    s = _sched()
    last = _speak_days(s, 3, strength=0.6)

    t = last + timedelta(days=1)
    s.note_spoke(_intent(0.9), t)          # 突破，计数归 1
    d = s.tick([_intent(0.9)], t + timedelta(days=1))
    assert d.will_speak, "重置之后该还能再说两次"


def test_停够久了就翻篇():
    s = _sched()
    last = _speak_days(s, 3)
    d = s.tick([_intent(0.8)], last + timedelta(days=6))
    assert d.will_speak


def test_停得不够久不算翻篇():
    """中间没说可能是没额度、也可能时机不对，不该算成"她已经不烦了"。"""
    s = _sched()
    last = _speak_days(s, 3)
    d = s.tick([_intent(0.8)], last + timedelta(days=2))
    assert not d.will_speak


# ------------------------------------------------------------ 别误伤

def test_不同话题各算各的():
    """睡眠说够了，不该连累"活动量"。"""
    s = _sched()
    last = _speak_days(s, 3, subject="睡眠")
    d = s.tick([_intent(0.8, subject="活动量")], last + timedelta(days=1))
    assert d.will_speak
    assert d.intent.subject == "活动量"


def test_睡眠被压住时会选别的话题说():
    """压住一条不等于整个人闭嘴 —— 队列里还有别的就该说别的。"""
    s = _sched()
    last = _speak_days(s, 3, subject="睡眠")
    d = s.tick(
        [_intent(0.9, subject="睡眠"), _intent(0.5, subject="活动量")],
        last + timedelta(days=1),
    )
    assert d.will_speak
    assert d.intent.subject == "活动量", "不该硬挑那条被压住的"


# ------------------------------------------------------------ 重启之后还记得

def test_计数要落盘():
    """🔴 不存的话，一台经常重启的机器上这道闸等于不存在 ——
    而且是**静默失效**，看起来一切正常。"""
    s = _sched()
    last = _speak_days(s, 3)

    revived = _sched()
    revived.load_state(s.dump_state())

    d = revived.tick([_intent(0.8)], last + timedelta(days=1))
    assert not d.will_speak


def test_读老状态不炸():
    """这一版之前存的状态里没有 streak_by_subject 这个键。"""
    s = _sched()
    s.load_state({"last_spoke_at": None, "last_by_subject": {}})
    assert s.tick([_intent(0.8)], _cn(2026, 9, 4, 21)).will_speak


def test_落盘的东西能被_json_序列化():
    """状态是要写进 attention.db 的，带 datetime 对象会在那一步才炸。"""
    import json

    s = _sched()
    _speak_days(s, 2)
    json.dumps(s.dump_state()["streak_by_subject"])


# ------------------------------------------------------------ 边界不能破

def test_这一层不碰_intent_的强度():
    """`resonance.py` 边界一：Drive 只读。

    他闭嘴不等于他不在意了 —— concern 该多重还是多重。
    """
    s = _sched()
    last = _speak_days(s, 3)
    i = _intent(0.8)
    s.tick([i], last + timedelta(days=1))
    assert i.attention_strength == 0.8


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
