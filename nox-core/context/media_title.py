"""片名清洗 —— 把文件名变回人会说出口的名字。

## 为什么需要

共影支持本地文件，而本地文件**没有别的名字可用**，记下来的就是文件名：

    The.Sheep.Detectives.2026.1080p中英字幕.mp4

这个字符串会出现在两个他会说出口的地方：

    topic_pool/pool.py   「一起看完了《XXX》，还没聊过——结局值得说说。」
    tools/watching.py     他回忆「我们一起看过什么」时报的片名

糖糖 2026-09-04 说的那个场景正好撞在这儿：她想要的是
「今天这部有点像我们之前看的《绵羊侦探团》」，
而现在他会念出一个带 `.1080p中英字幕.mp4` 的文件名。

## 🔴 只对**看起来像文件名的**动手

这是这个模块最要紧的一条。线上真实数据里有这么一条：

    片单｜2026年所期待的恐怖片VOL.3（秋季/万圣节档）

那是 B站的视频标题，**不是文件名** —— 它带着点、带着年份、还带着
`VOL.3`。任何"见到年份就截断""见到点就当分隔符"的规则都会把它毁掉。

所以判据是**有没有媒体扩展名**：

    有 .mp4/.mkv/…  →  按文件名清洗
    没有            →  原样返回，一个字都不动

宁可漏清洗一个，也不要把一个好好的标题剪坏 —— 剪坏了她会看到
一个残缺的片名，而且不知道是哪儿弄的。
"""

from __future__ import annotations

import re

#: 认这些扩展名才动手。列表之外的一律原样返回。
_EXT = re.compile(
    r"\.(mp4|mkv|avi|mov|wmv|flv|m4v|rmvb|rm|ts|webm|mpg|mpeg|m2ts)$",
    re.IGNORECASE,
)

#: 从这些标记**往后全砍掉** —— 它们后面不会再有片名。
#:
#: 顺序无关，取**最早出现**的那个作为切点。
#: 年份必须是独立一段（前后是分隔符或首尾），否则
#: 《2012》《1917》这种以年份为名的片子会被砍成空的。
_JUNK = re.compile(
    r"(?:^|[.\s_\-\[\(])(?:"
    r"(?:19|20)\d{2}"                       # 年份
    r"|\d{3,4}[pi]"                         # 1080p / 720p / 480i
    r"|4k|2160p|8k"
    r"|blu-?ray|bdrip|brrip|web-?dl|webrip|hdtv|dvdrip|remux|hdr|dolby"
    r"|x26[45]|h\.?26[45]|hevc|avc|aac|ac3|dts|flac|10bit"
    r"|中英字幕|中英双字|中文字幕|双语字幕|简繁|简体|繁体|中字|内嵌|外挂字幕"
    r")",
    re.IGNORECASE,
)


def clean(title: str) -> str:
    """文件名 → 片名。不像文件名的原样返回。

    >>> clean("The.Sheep.Detectives.2026.1080p中英字幕.mp4")
    'The Sheep Detectives'
    >>> clean("痴迷.mkv")
    '痴迷'
    >>> clean("片单｜2026年所期待的恐怖片VOL.3（秋季/万圣节档）")
    '片单｜2026年所期待的恐怖片VOL.3（秋季/万圣节档）'
    """
    raw = (title or "").strip()
    if not raw:
        return raw

    #: 🔴 没有媒体扩展名 = 不是文件名 = 不碰（见模块头）
    if not _EXT.search(raw):
        return raw

    name = _EXT.sub("", raw)

    #: 砍掉第一个垃圾标记以及它后面的一切
    m = _JUNK.search(name)
    if m and m.start() > 0:
        name = name[: m.start()]

    #: 分隔符还原成空格。**不动中文之间的点** —— 那可能是名字的一部分
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\s{2,}", " ", name).strip(" -_~")

    #: 全砍没了就退回原始文件名（去扩展名）——
    #: 给一个残缺的名字，不如给一个丑但完整的
    return name or _EXT.sub("", raw).strip()
