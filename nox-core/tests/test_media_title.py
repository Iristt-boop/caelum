"""片名清洗（2026-09-04）。

糖糖要的场景：「今天看的电影很像我们之前的看过的那部 XXX」。
而共影记本地文件时记的是文件名，于是他会念出

    《The.Sheep.Detectives.2026.1080p中英字幕.mp4》

## 🔴 这个文件里最要紧的一组是「不许碰」那几条

线上真实数据里有 `片单｜2026年所期待的恐怖片VOL.3（秋季/万圣节档）` ——
B站标题，带点、带年份、带 `VOL.3`。任何"见年份就截"的规则都会毁了它。

**剪坏一个好标题，比漏清洗一个文件名糟得多** ——
后者只是丑，前者她会看到一个残缺的片名而且不知道哪儿弄的。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.media_title import clean  # noqa: E402


# --------------------------------------------------------------- 线上真实数据

@pytest.mark.parametrize("raw,want", [
    #: 这四条全部来自 2026-09-04 线上 watch_sessions 表
    ("The.Sheep.Detectives.2026.1080p中英字幕.mp4", "The Sheep Detectives"),
    ("痴迷.mkv", "痴迷"),
    #: 🔴 B站标题，不是文件名 —— 一个字都不许动
    ("片单｜2026年所期待的恐怖片VOL.3（秋季/万圣节档）",
     "片单｜2026年所期待的恐怖片VOL.3（秋季/万圣节档）"),
    ("挽救计划", "挽救计划"),
])
def test_线上真实数据(raw, want):
    assert clean(raw) == want


# --------------------------------------------------------------- 🔴 不许碰的

def test_没有扩展名的一律不动():
    """判据是「有没有媒体扩展名」，不是「有没有点/年份」。"""
    for s in [
        "盗梦空间 2010 重映版",
        "Blade Runner 2049",
        "第 3.5 集 番外",
        "VOL.3 恐怖片盘点",
        "1080p 是什么意思",          # 标题里真的在讲 1080p
    ]:
        assert clean(s) == s, s


def test_以年份为名的片子不会被砍空():
    """《2012》《1917》这种，砍了就什么都不剩。"""
    assert clean("2012.mp4") == "2012"
    assert clean("1917.mkv") == "1917"


def test_砍空了就退回去而不是给空串():
    """宁可给一个丑但完整的名字，也不要给一个空的。"""
    out = clean("1080p.mp4")
    assert out and out.strip()


# --------------------------------------------------------------- 会清的

@pytest.mark.parametrize("raw,want", [
    ("Inception.2010.1080p.BluRay.x264.mp4", "Inception"),
    ("Interstellar.2014.2160p.WEB-DL.HEVC.mkv", "Interstellar"),
    ("流浪地球2.2023.HDTV.中字.mp4", "流浪地球2"),
    ("Some_Movie_Name.720p.mp4", "Some Movie Name"),
    ("A.B.C.mp4", "A B C"),                    # 没有垃圾标记，点全变空格
])
def test_文件名清洗(raw, want):
    assert clean(raw) == want


def test_扩展名大小写都认():
    assert clean("Movie.2020.1080p.MP4") == "Movie"
    assert clean("Movie.2020.1080p.MkV") == "Movie"


# --------------------------------------------------------------- 边界

@pytest.mark.parametrize("raw", ["", "   ", None])
def test_空值不炸(raw):
    clean(raw)


def test_清完不留首尾的分隔符():
    out = clean("Movie.Name.-.2020.1080p.mp4")
    assert out == out.strip(" -_~")
    assert "Movie Name" in out


def test_幂等():
    """清过的再清一次不该变 —— 万一哪天在两处都调了。"""
    once = clean("The.Sheep.Detectives.2026.1080p中英字幕.mp4")
    assert clean(once) == once


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
