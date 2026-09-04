"""`/api/nox/state` 把「关于她的」和「他自己的」分开（2026-09-04）。

## 这条测试在守什么

2026-09-04 接好奇那天当场炸的：话题池抓来的新闻标题（每条 40 字）
跟着进了 `cares`，手机端那行

    `Thinking about ${cares.join(" & ")}`

变成一屏字，而且读起来是

    Thinking about your sleep & 前端圈沸腾！Claude造出15KB引擎，渲染狂飙1200倍…

**前半句是惦记她，后半句是他刷到的新闻，两种语气焊在一句里。**

分流按 `kind`，不靠前端猜字符串 —— 所以这条测试守的是**后端的契约**：
`cares` 里永远不许出现自指的那些 kind。

⚠️ 以后加新的自指情绪（挫败、投入…）时，要往 `_SELF_KINDS` 里加一条。
漏了的表现就是它又跑进那句话里 —— 这条测试会拦住。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import server as srv  # noqa: E402


#: 一份 snapshot 里 attentions 该长的样子（service.py:863 的形状）
ATTENTIONS = [
    {"subject": "糖糖的睡眠", "kind": "concern", "strength": 0.55},
    {"subject": "糖糖的活动量", "kind": "concern", "strength": 0.40},
    {"subject": "他挑的说话时机", "kind": "regret", "strength": 0.35},
    {"subject": "前端圈沸腾！Claude造出15KB引擎，渲染狂飙1200倍", "kind": "curiosity",
     "strength": 0.42},
    {"subject": "作家莫言出新书：请像刷短视频一样「刷」我的短小说", "kind": "curiosity",
     "strength": 0.38},
]


def _split(attentions):
    """照 server.py 那两行的规则分流。"""
    cares = [a["subject"] for a in attentions if a.get("kind") not in srv._SELF_KINDS]
    curious = [a["subject"] for a in attentions if a.get("kind") in srv._SELF_KINDS]
    return cares, curious


def test_好奇不许进_cares():
    """🔴 这是那次事故的核心。"""
    cares, _ = _split(ATTENTIONS)
    for c in cares:
        assert "Claude造出" not in c
        assert "莫言" not in c


def test_关于她的还在_cares_里():
    """分流别把该留的也分走了 —— 那会让她手机上那一栏空掉。"""
    cares, _ = _split(ATTENTIONS)
    assert "糖糖的睡眠" in cares
    assert "糖糖的活动量" in cares


def test_regret_留在_cares():
    """⚠️ regret 虽然是 `target="agent"`，内容仍然**关于她**
    （他后悔打扰了她），所以它属于那句话，不是自指的那一类。"""
    cares, curious = _split(ATTENTIONS)
    assert "他挑的说话时机" in cares
    assert "他挑的说话时机" not in curious


def test_好奇都在_curious_里():
    _, curious = _split(ATTENTIONS)
    assert len(curious) == 2
    assert all("Claude造出" in c or "莫言" in c for c in curious)


def test_两边加起来不多不少():
    """别漏也别重 —— 漏了她看不到，重了那行会出现两次同一件事。"""
    cares, curious = _split(ATTENTIONS)
    assert len(cares) + len(curious) == len(ATTENTIONS)
    assert not (set(cares) & set(curious))


def test_没有好奇时_curious_是空的():
    """池子没启用 / 他这会儿没被什么勾住 —— 空列表，不是缺字段。"""
    only_hers = [a for a in ATTENTIONS if a["kind"] != "curiosity"]
    cares, curious = _split(only_hers)
    assert curious == []
    assert len(cares) == 3


def test_缺_kind_的旧数据当成关于她的():
    """老 snapshot 里可能没有 kind。**默认留在 cares** ——
    宁可多显示一条，也不要让她那一栏莫名其妙少东西。"""
    cares, curious = _split([{"subject": "某个老话题", "strength": 0.5}])
    assert cares == ["某个老话题"]
    assert curious == []


def test_SELF_KINDS_里有_curiosity():
    """加新自指情绪时忘了往这儿加，就会又炸一次那一行。"""
    assert "curiosity" in srv._SELF_KINDS


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
