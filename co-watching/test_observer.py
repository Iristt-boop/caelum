# -*- coding: utf-8 -*-
"""observer 的纯逻辑测试。**不打 ffmpeg、不打网络。**

分块归属、metadata 解析、事件推导 —— 这三样是最容易悄悄错的：
错了不会崩，只会让触发器在错误的时刻亮灯，而那要看片时才发现。

跑：`python -m pytest test_observer.py -q`
"""
from __future__ import annotations

import sys

import pytest

import observer as ob


# ---------------------------------------------------------------- 分块

def test_覆盖整部片子不留缝():
    chunks = ob.plan_chunks(20 * 60 * 1000)
    assert chunks[0]["own_from"] == 0
    # 归属区间首尾相接，既不重叠也不留缝
    for a, b in zip(chunks, chunks[1:]):
        assert a["own_to"] == b["own_from"], (a, b)
    assert chunks[-1]["own_to"] == 20 * 60 * 1000


def test_分析范围重叠但归属不重叠():
    """⚠️ 这是整个分块设计的重点。

    往前多吃 2 秒是为了让帧差有前文（不然每块第一帧是跟黑屏比，必然误判成切换）；
    但归属只认逻辑区间，否则同一秒会被两块各记一遍事件。
    """
    chunks = ob.plan_chunks(15 * 60 * 1000)
    second = chunks[1]
    assert second["from"] < second["own_from"], "第二块该往前多吃一点"
    assert second["own_from"] - second["from"] == ob.BOUNDARY_MS
    # 第一块没有前文可吃
    assert chunks[0]["from"] == chunks[0]["own_from"] == 0


def test_短片也只切一块():
    chunks = ob.plan_chunks(30_000)
    assert len(chunks) == 1
    assert chunks[0]["own_to"] == 30_000


def test_时长非法直接报错():
    with pytest.raises(ValueError):
        ob.plan_chunks(0)


# ---------------------------------------------------------------- 进度

def _chunks(*statuses):
    cs = ob.plan_chunks(len(statuses) * (ob.CHUNK_MS - ob.BOUNDARY_MS))
    for c, s in zip(cs, statuses):
        c["status"] = s
    return cs


def test_只数从头连续完成的块():
    """⚠️ 后面的块先跑完不算数。

    不然会出现「显示 80%，但第 3 分钟那块是空的」—— 她照着进度条以为
    准备好了，结果那一段他什么都不知道。
    """
    cs = _chunks("complete", "failed", "complete", "complete")
    through = ob.completed_through(cs)
    assert through == cs[0]["own_to"], "断在第二块，就只能算到第一块结束"


def test_全完成就是整部():
    cs = _chunks("complete", "complete")
    assert ob.completed_through(cs) == cs[-1]["own_to"]


def test_第一块没完成就是零():
    assert ob.completed_through(_chunks("running", "complete")) == 0


# ---------------------------------------------------------------- planHash

def test_参数变了plan就变():
    a = ob.plan_hash("k", 1000)
    assert a == ob.plan_hash("k", 1000)
    assert a != ob.plan_hash("k", 2000), "时长变了要重算"
    assert a != ob.plan_hash("other", 1000), "换了片子当然不一样"

    old = ob.SAMPLE_FPS
    try:
        ob.SAMPLE_FPS = old + 1
        assert a != ob.plan_hash("k", 1000), "取样率变了旧结果必须作废"
    finally:
        ob.SAMPLE_FPS = old


# ---------------------------------------------------------------- 解析

# 实测的真实输出格式（2026-08-22 在 VPS 上抓的）
REAL = """frame:0    pts:0       pts_time:0
lavfi.scd.mafd=0.000
lavfi.scd.score=0.000
frame:1    pts:1       pts_time:0.5
lavfi.scd.mafd=15.431
lavfi.scd.score=15.431
lavfi.scd.time=0.5
frame:2    pts:2       pts_time:1
lavfi.scd.mafd=5.082
lavfi.scd.score=5.082
lavfi.scd.time=1
"""


def test_按真实输出格式解析():
    got = ob.parse_metadata(REAL)
    # 第 0 帧被丢掉了 —— 它永远是 0（没有前一帧可比）
    assert got == [(0.5, 15.431), (1.0, 5.082)]


def test_第一帧必须丢掉():
    """留着会把每块的分位数往下拉，阈值跟着偏低，误报变多。"""
    assert all(s > 0 for _, s in ob.parse_metadata(REAL))


def test_空输入不崩():
    assert ob.parse_metadata("") == []
    assert ob.parse_metadata("什么都不是") == []


# ---------------------------------------------------------------- 事件

def _samples(scores, fps=2):
    return [(i / fps, s) for i, s in enumerate(scores)]


def test_尖峰算切镜头():
    # 一片平静里插一个大尖峰
    scores = [3.0] * 40 + [60.0] + [3.0] * 40
    got = ob.derive_events(_samples(scores), 0, 0, 999_000)
    cuts = [e for e in got["events"] if e["kind"] == "scene_change"]
    assert len(cuts) == 1
    assert cuts[0]["at"] == 20_000  # 第 40 帧 @2fps = 20 秒


def test_整块静止不许报出切镜头():
    """⚠️ 只按分位数定阈值的话，一块全静止的画面里最大的那点噪声
    也会变成"相对突出" → 报一个假的切换。绝对下限就是拦这个的。"""
    got = ob.derive_events(_samples([0.4] * 60 + [0.9] + [0.4] * 60), 0, 0, 999_000)
    assert [e for e in got["events"] if e["kind"] == "scene_change"] == []


def test_归属之外的事件被丢掉():
    """分析范围往前多吃了 2 秒，那一截属于上一块，不能重复记。"""
    scores = [3.0] * 10 + [60.0] + [3.0] * 30
    # 分析从 8000 开始，但这一块只拥有 10000 之后
    got = ob.derive_events(_samples(scores), 8_000, 10_000, 999_000)
    at = [e["at"] for e in got["events"]]
    assert all(a >= 10_000 for a in at), at


def test_持续晃动是运动不是切镜头():
    """⚠️ **这条是第一版写错、被测试抓出来的。**

    只按分位数判的话，一段持续 6 秒、帧差稳定在 18 的晃动会因为
    「高于本块 p98」被报成**连续 12 次切镜头**。

    真实的剪辑点是「突然不一样」（尖峰），持续晃动是「一直在变」（平台）。
    所以切镜头额外要求高出前几帧均值 SPIKE_RATIO 倍。
    """
    scores = [2.0] * 20 + [18.0] * 12 + [2.0] * 20
    got = ob.derive_events(_samples(scores), 0, 0, 999_000)
    cuts = [e for e in got["events"] if e["kind"] == "scene_change"]

    # 切进这段动作戏的**那一刻**确实是剪辑点，报一次是对的 ——
    # 第一版我以为该报 0 次，是我把语义想窄了。要守的是「不许报 12 次」
    assert len(cuts) == 1, f"切入点该报一次，实际 {len(cuts)} 次"
    assert cuts[0]["at"] == 10_000, "报的该是切入的那一刻"

    peaks = [e for e in got["events"] if e["kind"] == "motion_peak"]
    assert 0 < len(peaks) <= 3, f"一段持续运动不该报 {len(peaks)} 次"


def test_动作戏里的真剪辑点还认得出来():
    """反过来也要成立：底噪高不代表没有剪辑点。
    平台上再叠一个尖峰，那个尖峰仍然是切镜头。"""
    scores = [2.0] * 10 + [18.0] * 10 + [70.0] + [18.0] * 10
    got = ob.derive_events(_samples(scores), 0, 0, 999_000)
    cuts = [e for e in got["events"] if e["kind"] == "scene_change"]
    assert len(cuts) == 1
    assert cuts[0]["at"] == 10_000  # 第 20 帧 @2fps


def test_归一化分数要能排序不能全是一():
    """⚠️ **真机上跑一部片子才发现的（2026-08-22）。**

    第一版是 `min(1, score / gate)` —— 而事件只在 `score >= gate` 时才产生，
    所以比值永远 ≥1，clamp 完**21 个事件的分数全是 1.0**。

    P3 的触发器要靠这个分数排序选帧（架构 14.3）。全是 1.0 的话排序等于随机，
    而且**不会报错**，只会让他在平庸的时刻开口。
    """
    # ⚠️ 样本要够长。真实一块是 600 帧（5 分钟 @2fps）——
    # 只放三个尖峰的话 p98 会正好落在最大值上，跨度为 0，测不出想测的东西
    scores = []
    for i in range(20):
        scores += [2.0] * 29 + [15.0 + i * 2.5]   # 强度从 15 递增到 62.5
    got = ob.derive_events(_samples(scores), 0, 0, 9_999_000)
    cuts = [e for e in got["events"] if e["kind"] == "scene_change"]
    vals = [e["sceneScore"] for e in cuts]

    assert len(set(vals)) > 1, f"强度差这么多，分数却一样：{vals}"
    # 越强的分越高
    assert vals == sorted(vals), f"分数该跟着强度走：{vals}"
    assert all(0.0 <= v <= 1.0 for v in vals), vals


def test_整块只有一个强度时不假装很突出():
    """所有尖峰一样高 → 跨度为 0。这时给 0.5，不给 1.0 ——
    「无法区分」不该被表达成「最重要」。"""
    scores = ([2.0] * 10 + [40.0]) * 4
    got = ob.derive_events(_samples(scores), 0, 0, 999_000)
    cuts = [e for e in got["events"] if e["kind"] == "scene_change"]
    assert cuts, "该有切镜头"
    assert all(e["sceneScore"] == 0.5 for e in cuts), [e["sceneScore"] for e in cuts]


def test_分布原样留着好调阈值():
    """⚠️ 以后想改阈值不该重新解码一遍片子 —— 分位数必须存下来。"""
    got = ob.derive_events(_samples([1.0, 5.0, 20.0, 3.0, 40.0]), 0, 0, 999_000)
    for k in ("p50", "p90", "p98", "max", "scene_gate", "motion_gate"):
        assert k in got["stats"], k


def test_没有样本时不崩():
    got = ob.derive_events([], 0, 0, 1000)
    assert got["events"] == []
    assert got["samples"] == 0


# ---------------------------------------------------------------- 读

def test_只回事件不回原始分数(tmp_path):
    """⚠️ 架构 14.4：原始遥测留服务端。上层要的是「这里切了一刀」，
    不是 600 个浮点数。"""
    an = ob.Analyzer(str(tmp_path), "k", 600_000)
    m = an.fresh()
    m["chunks"][0]["status"] = "complete"
    m["chunks"][0]["events"] = [{"at": 5_000, "kind": "scene_change", "sceneScore": 1.0}]
    m["chunks"][0]["stats"] = {"p50": 3.0}
    an.save(m)

    events = an.events(0, 600_000)
    assert len(events) == 1
    assert "stats" not in events[0]
    assert all("p50" not in e for e in events)


def test_没跑完的块不给事件(tmp_path):
    an = ob.Analyzer(str(tmp_path), "k", 600_000)
    m = an.fresh()
    m["chunks"][0]["status"] = "running"
    m["chunks"][0]["events"] = [{"at": 1_000, "kind": "scene_change"}]
    an.save(m)
    assert an.events(0, 600_000) == [], "没跑完的块里的事件不算数"


def test_plan变了就重新开始(tmp_path):
    an = ob.Analyzer(str(tmp_path), "k", 600_000)
    m = an.ensure()
    m["chunks"][0]["status"] = "complete"
    an.save(m)
    assert an.ensure()["chunks"][0]["status"] == "complete"

    # 同一个 key，但时长变了（换了清晰度/重新解析）→ 旧结果作废
    an2 = ob.Analyzer(str(tmp_path), "k", 900_000)
    assert an2.ensure()["chunks"][0]["status"] == "pending"


def test_没有manifest时进度是none(tmp_path):
    an = ob.Analyzer(str(tmp_path), "nothing", 1000)
    assert an.progress()["status"] == "none"
    assert an.events(0, 1000) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


# ---------------------------------------------------------------- 本地那条路

def test_绝对时间的样本也能推出事件():
    """⚠️ 本地片子走的是**滚动窗口**，样本自带绝对视频时间（不是块内偏移）。

    `/api/director/local` 调用时传 `offset_ms=0`、归属范围放开 ——
    这条盯着那个用法不会因为归属过滤把事件全丢掉。
    """
    # 第 600 秒附近一个尖峰
    base = 600.0
    scores = [2.0] * 40 + [60.0] + [2.0] * 40
    samples = [(base + i / 2, s) for i, s in enumerate(scores)]

    got = ob.derive_events(samples, 0, 0, 10 ** 12)
    cuts = [e for e in got["events"] if e["kind"] == "scene_change"]
    assert len(cuts) == 1, got["events"]
    # 时间要落在真实的视频时刻上（620 秒），不是从 0 开始重新算
    assert cuts[0]["at"] == 620_000, cuts[0]


def test_样本太少时不硬凑事件():
    """刚开播只有几帧，分位数没意义 —— 那时候宁可没有事件。

    （端点那边也挡了一道：少于 8 个样本直接不算。）
    """
    got = ob.derive_events([(0.0, 3.0), (0.5, 40.0)], 0, 0, 10 ** 12)
    # 两个样本算不出可信的分布，至多给出一个事件，绝不能刷一串
    assert len(got["events"]) <= 1


def test_静止画面不许刷出运动峰():
    """🔴 **2026-08-23 用合成数据探出来的，而且方向是反的。**

    分位数是相对的：一段完全静止的画面里 p90 就等于底噪，于是每隔
    MOTION_MIN_GAP 就刷一个「运动峰」。更糟的是静止时
    `motion_gate == motion_top`，跨度为 0 → 分数给 0.5 →
    超过 director 的 TENSION_GATE(0.35) → **静止长镜头被判成「正紧张着」**。

    那恰恰是最该开口的时刻，却被抑制器堵死。绝对下限就是拦这个的。
    """
    got = ob.derive_events(_samples([2.0] * 120), 0, 0, 10 ** 9)
    assert got["events"] == [], f"静止画面刷出了 {len(got['events'])} 个事件"


def test_真运动还认得出来():
    """反向：加了下限之后不能把真的运动也掐掉。"""
    scores = [2.0] * 30 + [20.0] * 12 + [2.0] * 30
    got = ob.derive_events(_samples(scores), 0, 0, 10 ** 9)
    peaks = [e for e in got["events"] if e["kind"] == "motion_peak"]
    assert peaks, "真的持续运动该报出来"


def test_切镜头的回声不算运动():
    """🔴 **两层之间的交互 bug，2026-08-23 探本地那条路时发现的。**

    一次干净的切镜头（帧差 60）会让之后几帧的滑动均值抬到 11.7，
    超过门槛 → 报一个运动峰 → director 看到「刚才有运动峰」判定
    「正紧张着」→ **每一次换场都把自己抑制掉，他永远开不了口。**

    单看 observer 或单看 director 都发现不了 —— 只有把两层串起来跑才露出来。
    """
    scores = [2.0] * 40 + [60.0] + [2.0] * 40
    got = ob.derive_events(_samples(scores), 0, 0, 10 ** 9)
    kinds = [e["kind"] for e in got["events"]]
    assert kinds == ["scene_change"], f"切镜头后面跟出了运动峰：{got['events']}"
