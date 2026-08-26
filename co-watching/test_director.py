# -*- coding: utf-8 -*-
"""本地控制器的测试。不打网络。

最要紧的一条不是「某条规则对不对」，是**整部片子下来他到底说几次**。
第十一节定的是 5-15 次 —— 所以最后那个密度模拟才是真正的验收。

跑：`python -m pytest test_director.py -q`
"""
from __future__ import annotations

import random
import sys

import pytest

import director as dr


def _st(**kw):
    st = dr.DirectorState(duration_s=7200)
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def _cut(at_ms, score=0.8):
    return {"at": at_ms, "kind": "scene_change", "sceneScore": score, "raw": 30.0}


def _motion(at_ms, score=0.6):
    return {"at": at_ms, "kind": "motion_peak", "motionScore": score, "raw": 15.0}


def _sub(start, end, text="说话"):
    return {"start": start, "end": end, "text": text}


NOW = 600_000  # 第 10 分钟


# ---------------------------------------------------------------- 会说话

def test_安静之后换场就开口():
    d = dr.decide(NOW, [_cut(NOW - 4000)], [], _st())
    assert d.speak, d.reason
    assert d.at_ms == NOW - 4000


def test_说的是最强的那个换场():
    """架构 14.3 第二步：按重要度选。"""
    events = [_cut(NOW - 6000, score=0.2), _cut(NOW - 3000, score=0.9)]
    # 两个换场互相干扰「前面要安静」，所以拉开到各自的静区之外
    events = [_cut(NOW - 3000, score=0.9), _cut(NOW - 2500, score=0.2)]
    d = dr.decide(NOW, events, [], _st())
    # 最强的那个被挑中（另一个太近，会被「前面要安静」挡掉）
    assert d.speak is False or d.event["sceneScore"] == 0.9


# ---------------------------------------------------------------- 不说话

def test_有台词就闭嘴():
    """⚠️ 压着台词插嘴是最讨厌的一种打断。"""
    subs = [_sub(NOW / 1000 - 1, NOW / 1000 + 1)]
    d = dr.decide(NOW, [_cut(NOW - 4000)], subs, _st())
    assert not d.speak
    assert "台词" in d.reason


def test_动作戏里闭嘴():
    """🔴 **运动峰是抑制器不是触发器。** 动作戏正是最不该说话的时候。"""
    events = [_cut(NOW - 4000), _motion(NOW - 3000, score=0.7)]
    d = dr.decide(NOW, events, [], _st())
    assert not d.speak
    assert "紧张" in d.reason


def test_刚说过就不说():
    d = dr.decide(NOW, [_cut(NOW - 4000)], [], _st(last_spoke_ms=NOW - 30_000))
    assert not d.speak
    assert "刚说过" in d.reason


def test_配额用完就不说():
    d = dr.decide(NOW, [_cut(NOW - 4000)], [], _st(spoke_count=dr.MAX_PER_FILM))
    assert not d.speak
    assert "够了" in d.reason


def test_她刚说过话就让她看():
    d = dr.decide(NOW, [_cut(NOW - 4000)], [], _st(last_user_ms=NOW - 5000))
    assert not d.speak
    assert "让她看" in d.reason


def test_密集剪辑不是气口():
    """动作戏里每隔几秒切一刀 —— 那种切换不是可以聊天的换场。"""
    events = [_cut(NOW - 4000), _cut(NOW - 9000), _cut(NOW - 14000)]
    d = dr.decide(NOW, events, [], _st())
    assert not d.speak
    assert "气口" in d.reason


def test_开场先让她进片子():
    """⚠️ **真数据逼出来的。**

    拿一部真片子从头 tick 到尾，他**在第 4 秒就开口了** —— 她刚按下播放。
    逻辑上没错（前面确实没有事件 = "安静"），但片头那段安静是
    「还没开始」，不是「气口」。
    """
    early = 4_000
    d = dr.decide(early, [_cut(early - 2000)], [], _st())
    assert not d.speak
    assert "让她先进去" in d.reason

    # 过了热身就正常
    late = int(dr.WARMUP_S * 1000) + 10_000
    d2 = dr.decide(late, [_cut(late - 4000)], [], _st())
    assert d2.speak, d2.reason


def test_热身按片长缩不能把短片吃光():
    """⚠️ 也是真数据逼出来的：定死 90 秒的话，一条 4 分钟的 B站视频
    有 36% 是"片头"，整条下来一句不说。她主要看的就是这种长度。"""
    short = dr.DirectorState(duration_s=249)      # 4 分钟
    film = dr.DirectorState(duration_s=7200)      # 2 小时
    assert short.warmup_s() < 30, short.warmup_s()
    assert film.warmup_s() == dr.WARMUP_S

    # 4 分钟的片子，第 40 秒该能说话了
    d = dr.decide(40_000, [_cut(36_000)], [], short)
    assert d.speak, d.reason


def test_什么都没有就不说():
    d = dr.decide(NOW, [], [], _st())
    assert not d.speak


def test_每个决定都有理由():
    """同 Care 的 CareOutcome：静默丢弃是最难查的病。"""
    for events, subs, st in [
        ([], [], _st()),
        ([_cut(NOW - 4000)], [_sub(NOW / 1000, NOW / 1000)], _st()),
        ([_cut(NOW - 4000)], [], _st(spoke_count=99)),
    ]:
        assert dr.decide(NOW, events, subs, st).reason


# ---------------------------------------------------------------- 防剧透

def test_证据只取T之前的台词():
    """⚠️ **红线**（架构 12.1）：他随口剧透一句，整部电影就毁了。"""
    at = NOW - 4000
    t = at / 1000
    # ⚠️ 「后面才说的」要挪到当前时刻之外 —— 放太近会先被「这会儿有台词」
    # 挡掉，那样测的就不是防剧透了（第一版就是这么写错的）
    subs = [_sub(t - 10, t - 8, "之前说的"), _sub(t + 20, t + 23, "后面才说的")]
    d = dr.decide(NOW, [_cut(at)], subs, _st())
    assert d.speak
    assert "之前说的" in d.dialogue
    assert "后面才说的" not in d.dialogue, "拿到了她还没看到的台词！"


# ---------------------------------------------------------------- 不重复

def test_同一个换场不说第二遍():
    st = _st()
    e = _cut(NOW - 4000)
    d = dr.decide(NOW, [e], [], st)
    assert d.speak
    dr.note_spoke(st, d, NOW)

    st.last_spoke_ms = -10 ** 9        # 把冷却摘掉，单验去重
    d2 = dr.decide(NOW + 1000, [e], [], st)
    assert not d2.speak


# ---------------------------------------------------------------- 密度（验收）

def _simulate(duration_s, cuts_per_min, motion_per_min, dialogue_ratio, seed=7):
    """按给定密度造一部片子，每 2 秒 tick 一次，数他说了几句。"""
    rng = random.Random(seed)
    total_ms = int(duration_s * 1000)

    events = []
    for _ in range(int(duration_s / 60 * cuts_per_min)):
        events.append(_cut(rng.randrange(0, total_ms), score=rng.uniform(0.2, 1.0)))
    for _ in range(int(duration_s / 60 * motion_per_min)):
        events.append(_motion(rng.randrange(0, total_ms), score=rng.uniform(0.1, 0.9)))
    events.sort(key=lambda e: e["at"])

    subs = []
    t = 0.0
    while t < duration_s:
        if rng.random() < dialogue_ratio:
            subs.append(_sub(t, t + 2.5))
        t += 3.0

    st = dr.DirectorState(duration_s=duration_s)
    spoke = []
    for now_ms in range(0, total_ms, 2000):
        # 只喂已经发生过的事件（真实播放时也只知道过去的）
        seen = [e for e in events if e["at"] <= now_ms]
        d = dr.decide(now_ms, seen, subs, st)
        if d.speak:
            dr.note_spoke(st, d, now_ms)
            spoke.append(now_ms)
    return spoke


def test_一部两小时的片子说5到15次():
    """⚠️ **这条是 P3 的真正验收。**

    单条规则对不对没那么要紧，要紧的是**整部片子下来的节奏**。
    第十一节定的是 5-15 次。密度用 P2 在真片子上实测的数字
    （切镜头 2.2 次/分、运动峰 2.9 次/分）。
    """
    spoke = _simulate(7200, cuts_per_min=2.2, motion_per_min=2.9, dialogue_ratio=0.4)
    assert 5 <= len(spoke) <= 15, f"两小时说了 {len(spoke)} 次"


def test_点评摊在整部片子上不挤在开头():
    """间隔按片长拉开，就是为了防这个。"""
    spoke = _simulate(7200, 2.2, 2.9, 0.4)
    assert spoke, "一次都没说"
    first_half = sum(1 for t in spoke if t < 3_600_000)
    # 不要求绝对均匀，但不能一半以上挤在前半场之外的极端
    assert 0 < first_half < len(spoke), f"全挤在一边了：{[t//60000 for t in spoke]}"


def test_动作片话更少():
    """密集动作 = 一直在紧张 = 该更安静。"""
    calm = _simulate(7200, 2.0, 1.0, 0.3)
    action = _simulate(7200, 6.0, 12.0, 0.3)
    assert len(action) < len(calm), f"动作片 {len(action)} 次 vs 文艺片 {len(calm)} 次"


def test_没有视觉分析时几乎不说话():
    """P2 没跑过的片子 —— 没有证据就没有值得说的话，**宁可安静**。"""
    st = dr.DirectorState(duration_s=7200)
    spoke = [t for t in range(0, 7_200_000, 2000)
             if dr.decide(t, [], [], st).speak]
    assert spoke == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
