"""好奇 —— 第一个和她无关的情绪（2026-09-04）。

糖糖：「我想要的是能让 nox 的 feeling 情感多一些，**并且不单单是因为我**」。

在这之前 Registry 里每一条都指向她：她的睡眠、她的活动量、她说了什么、
他后悔打扰了她。这是第一条从头到尾跟她没关系的。

## 守的几条

1. **一条料只产一次事件**（同 sleep.py 的语义压缩）——
   同一篇论文报十次会把 Registry 刷屏，而"他一直好奇"该由强度不衰减表达。
2. **强度够不着开口阈值** —— 他对一篇论文好奇不该变成一次主动开口。
3. **说不出为什么就不要** —— resonance.py 边界三。
4. **池子没启用就永远安静** —— 不报错也不假装。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from attention.evaluator import AttentionEvaluator  # noqa: E402
from attention.events import ExperienceEvent  # noqa: E402
from attention.relationship import RelationshipState  # noqa: E402
from attention.registry import KINDS  # noqa: E402
from attention.sources.curiosity import (  # noqa: E402
    MAX_STRENGTH, SOURCE, TYPE, CuriositySource,
)

NOW = datetime(2026, 9, 4, 18, 30, tzinfo=timezone.utc)
#: 他开口的阈值。好奇必须够不着（见模块头）
GENERATE_THRESHOLD = 0.55


@dataclass
class _Topic:
    id: str
    source_title: str = ""
    hook: str = ""
    relevance: float = 0.5
    category: str = "ai"


class _Store:
    """attention.db 的 source_state 那两个方法。"""

    def __init__(self):
        self.state = {}

    def get_source_state(self, k):
        return self.state.get(k)

    def set_source_state(self, k, v):
        self.state[k] = v


class _Pool:
    def __init__(self, topics, boom=False):
        outer = self

        class _S:
            def open_topics(self, now, *, include_surfaced=True, limit=50):
                if outer.boom:
                    raise RuntimeError("池子挂了")
                outer.calls.append(include_surfaced)
                return outer.topics

        self.topics = topics
        self.boom = boom
        self.calls = []
        self.store = _S()


def _src(topics, store=None, boom=False):
    return CuriositySource(store or _Store(), _Pool(topics, boom))


# --------------------------------------------------------------- 产事件

def test_池子里有新料就被勾住():
    e = _src([_Topic("t1", "Attention Is All You Need", "一篇讲注意力的")]).poll(NOW)
    assert e is not None
    assert e.source == SOURCE and e.type == TYPE
    assert e.payload["title"] == "Attention Is All You Need"


def test_这是他自己的事():
    """`target="agent"` —— 同 regret 那条，但方向完全不同：
    regret 仍然是关于她的（后悔打扰了她），这条跟她没关系。"""
    e = _src([_Topic("t1", "某论文", "钩子")]).poll(NOW)
    assert e.target == "agent"


def test_只要没跟她聊过的():
    """聊过的他已经说出去了，不该再算作"心里有件事"。"""
    p = _Pool([_Topic("t1", "x", "y")])
    CuriositySource(_Store(), p).poll(NOW)
    assert p.calls == [False], "必须 include_surfaced=False"


def test_同一条料只产一次():
    """🔴 同 sleep.py：只在状态变化时产事件，不是每次 poll 都报。"""
    s = _src([_Topic("t1", "某论文", "钩子")])
    assert s.poll(NOW) is not None
    assert s.poll(NOW) is None, "第二次不该再报同一条"


def test_有新料时接着报():
    store = _Store()
    p = _Pool([_Topic("t1", "旧的", "h")])
    s = CuriositySource(store, p)
    assert s.poll(NOW) is not None
    p.topics = [_Topic("t1", "旧的", "h"), _Topic("t2", "新的", "h2")]
    e = s.poll(NOW)
    assert e is not None and e.payload["title"] == "新的"


def test_重启之后不把整池子重报一遍():
    store = _Store()
    p = _Pool([_Topic("t1", "某论文", "钩子")])
    CuriositySource(store, p).poll(NOW)
    #: 换一个实例（模拟重启），状态从 store 读回来
    assert CuriositySource(store, p).poll(NOW) is None


# --------------------------------------------------------------- 该安静的时候

def test_池子没启用就永远安静():
    assert CuriositySource(_Store(), None).poll(NOW) is None


def test_池子读挂了不炸():
    """一个源坏了不许带塌别人（service.py 那条）。"""
    assert _src([], boom=True).poll(NOW) is None


def test_没标题也没钩子的料不要():
    """说不出"因为什么"的东西不该变成情绪 —— 边界三。"""
    assert _src([_Topic("t1", "", "")]).poll(NOW) is None


def test_没有料就没有好奇():
    assert _src([]).poll(NOW) is None


# --------------------------------------------------------------- Evaluator

def _evaluator():
    #: Evaluator 要一份关系状态；world/appraiser 这条路用不上，留默认
    return AttentionEvaluator(RelationshipState())


def _decide(topic):
    e = _src([topic]).poll(NOW)
    return _evaluator().evaluate(e, registry=None, now=NOW)


def test_评成_curiosity_并且写得出理由():
    d = _decide(_Topic("t1", "某论文", "一篇讲注意力机制的论文"))
    assert d.action == "upsert"
    assert d.kind == "curiosity"
    assert "注意力机制" in d.summary


def test_subject_是具体那条东西():
    """🔴 不能用笼统的"他好奇的东西" —— Registry 按 subject 去重，
    笼统的会把所有料挤成一条，而 Drive 的 because 正是从 subject 来的。"""
    d = _decide(_Topic("t1", "Attention Is All You Need", "h"))
    assert "Attention" in d.subject


def test_强度够不着开口阈值():
    """🔴 他对一篇论文好奇，不该变成一次主动开口打扰她。"""
    d = _decide(_Topic("t1", "某论文", "h", relevance=1.0))
    assert d.strength <= MAX_STRENGTH < GENERATE_THRESHOLD


def test_好奇衰减得快():
    """今天有意思的东西三天后多半不惦记了 —— 和睡眠的 slow 正相反。"""
    d = _decide(_Topic("t1", "某论文", "h"))
    assert d.decay == "fast"


def test_相关度高的更强():
    a = _decide(_Topic("t1", "低", "h", relevance=0.2)).strength
    b = _decide(_Topic("t2", "高", "h", relevance=0.9)).strength
    assert b > a


def test_不是他自己的事就拒掉():
    """防呆：哪天有人复制这段去处理别的事件，这行会拦住。"""
    #: ExperienceEvent 是 frozen 的，改不了字段 —— 直接造一个 target 不对的
    e = ExperienceEvent(source=SOURCE, type=TYPE, target="user",
                        payload={"title": "x", "hook": "y"})
    d = _evaluator().evaluate(e, registry=None, now=NOW)
    assert d.action == "ignore"


# --------------------------------------------------------------- 白名单

def test_curiosity_进了_KINDS():
    """没进白名单的话 upsert 会直接抛 —— 事件产出来也落不了地。"""
    assert "curiosity" in KINDS


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
