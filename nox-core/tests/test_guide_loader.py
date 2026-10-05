"""手册加载与目录：结构测试。

测的是**结构**（目录和文件一致、文件头合法、没写状态词、能读到），
不测内容该怎么写 —— 手册是参考不是台词，给内容做回归测试等于冻他（CAELUM-MAP 三·二）。
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import pytest

from guide.loader import (
    DIRECTORY_HEAD,
    DIRECTORY_TAIL,
    TOPICS_DIR,
    GuideError,
    Topic,
    guide_read,
    load_topics,
    render_directory,
)

#: **故意写死。** 删一篇、改一个 id，这里必须红 —— 手册篇目是有意识的决定，不是顺手长出来的。
EXPECTED_TOPICS = [
    "where-am-i",
    "what-can-i-do",
    "models-and-routes",
    "memory",
    "speaking-up",
    "confirm-and-permissions",
    "where-in-os",
]

#: 手册只写「怎么运作」，不写「现在怎样」：这几个词一出现，多半是在写状态。
BANNED = ("当前", "目前", "已开启", "已配置", "现在是", "现在用")
BODY_LIMIT = 1500


def _write(d: Path, name: str, *, topic: str, title: str = "标题", ask: str = "何时翻",
           body: str = "正文", head_extra: str = "") -> Path:
    p = d / name
    p.write_text(
        f"---\ntopic: {topic}\ntitle: {title}\nask: {ask}\n{head_extra}---\n{body}\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------- 真实手册


def test_篇目恰好是预期的七篇():
    assert [t.id for t in load_topics()] == EXPECTED_TOPICS


def test_目录等于磁盘上的文件集合():
    """不靠 load_topics 自己数：独立 glob 一遍，和加载结果对账。"""
    on_disk = sorted(
        re.match(r"^\d{2}-(.+)\.md$", p.name).group(1)
        for p in TOPICS_DIR.glob("*.md")
        if re.match(r"^\d{2}-.+\.md$", p.name)
    )
    assert sorted(t.id for t in load_topics()) == on_disk


@pytest.mark.parametrize("topic", load_topics(), ids=lambda t: t.id)
def test_每篇合格(topic: Topic):
    assert topic.title and topic.ask
    assert topic.body.strip(), "正文不许为空"
    assert len(topic.body) <= BODY_LIMIT, f"{topic.id} 正文 {len(topic.body)} 字符，超了就拆"
    assert "<!--" not in topic.body, "来源注释必须在加载时被剥掉，不能返回给模型"
    assert "做完看：" in topic.body, "每篇至少要有一条「做完看：」（做法之后怎么确认）"
    hit = [w for w in BANNED if w in topic.body]
    assert not hit, f"{topic.id} 写了状态词 {hit} —— 状态要走 caelum_map，不写进手册"
    if "[待核]" in topic.body:
        warnings.warn(f"{topic.id} 还有 [待核] 没清", stacklevel=1)


# ---------------------------------------------------------------- 目录


def test_目录格式逐行():
    topics = load_topics()
    lines = render_directory(topics).split("\n")
    assert lines[0] == DIRECTORY_HEAD
    assert lines[-1] == DIRECTORY_TAIL
    body_lines = lines[1:-1]
    assert len(body_lines) == len(topics)
    for line, t in zip(body_lines, topics):
        assert line == f"- {t.id}：{t.title}（{t.ask}）"


def test_目录不超过1200字符():
    assert len(render_directory(load_topics())) <= 1200


def test_目录里没有任何一篇正文():
    d = render_directory(load_topics())
    for t in load_topics():
        assert t.body[:30] not in d


# ---------------------------------------------------------------- 加载错误


@pytest.mark.parametrize(
    "case, kwargs, extra_file",
    [
        ("缺 key", {"head_extra": ""}, "missing"),
        ("多余 key", {"head_extra": "mood: x\n"}, None),
        ("topic 与文件名不符", {"topic": "other-name"}, None),
        ("id 含大写", {"topic": "Bad-Id"}, "01-Bad-Id.md"),
    ],
)
def test_坏文件一律报错(tmp_path, case, kwargs, extra_file):
    if extra_file == "missing":
        (tmp_path / "00-a.md").write_text("---\ntopic: a\ntitle: t\n---\n正文", encoding="utf-8")
    elif extra_file:
        _write(tmp_path, extra_file, **{"topic": kwargs["topic"]})
    else:
        base = {"topic": "a"}
        base.update(kwargs)
        _write(tmp_path, "00-a.md", **base)
    with pytest.raises(GuideError):
        load_topics(tmp_path)


def test_topic_重复报错(tmp_path):
    _write(tmp_path, "00-a.md", topic="a")
    # 文件名不同但 front-matter 相同 → 先因为与文件名不符报错；
    # 要测重复，需要两个文件名都对应同一个 id —— 用两位数字不同前缀
    _write(tmp_path, "01-a.md", topic="a")
    with pytest.raises(GuideError, match="重复"):
        load_topics(tmp_path)


def test_空目录不是通过(tmp_path):
    with pytest.raises(GuideError):
        load_topics(tmp_path)
    (tmp_path / "README.md").write_text("不是手册", encoding="utf-8")
    with pytest.raises(GuideError):
        load_topics(tmp_path)


def test_不合格命名的文件被忽略(tmp_path):
    _write(tmp_path, "00-a.md", topic="a")
    (tmp_path / "README.md").write_text("不是手册", encoding="utf-8")
    (tmp_path / "x-b.md").write_text("也不是", encoding="utf-8")
    assert [t.id for t in load_topics(tmp_path)] == ["a"]


def test_html注释整段删除含跨行(tmp_path):
    _write(tmp_path, "00-a.md", topic="a",
           body="前<!-- 源：某处 -->中\n<!--\n跨行\n来源\n-->后")
    (t,) = load_topics(tmp_path)
    assert "<!--" not in t.body and "来源" not in t.body and "某处" not in t.body
    assert t.body.startswith("前") and t.body.endswith("后")


def test_文件头支持BOM和CRLF(tmp_path):
    raw = "---\r\ntopic: a\r\ntitle: t\r\nask: k\r\n---\r\n正文\r\n"
    (tmp_path / "00-a.md").write_bytes(b"\xef\xbb\xbf" + raw.encode("utf-8"))
    (t,) = load_topics(tmp_path)
    assert (t.id, t.title, t.ask, t.body) == ("a", "t", "k", "正文")


# ---------------------------------------------------------------- guide_read


def test_命中返回正文():
    topics = load_topics()
    assert guide_read(topics, "memory") == topics[3].body
    assert guide_read(topics, "  memory  ") == topics[3].body


def test_没命中不抛_返回目录():
    topics = load_topics()
    out = guide_read(topics, "no-such-topic")
    assert "no-such-topic" in out
    assert render_directory(topics) in out


@pytest.mark.parametrize("bad", [None, 123, ["memory"], "", "   "])
def test_非字符串或空不抛(bad):
    topics = load_topics()
    out = guide_read(topics, bad)
    assert render_directory(topics) in out
