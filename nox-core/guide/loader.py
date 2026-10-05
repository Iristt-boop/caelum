"""Nox 使用手册：加载、目录渲染、按篇取正文。

手册讲「Caelum 怎么运作」，**从不写「现在怎样」** —— 现在怎样由 `caelum_map`
（`guide/world_map.py`）或对应工具现查。分开的理由：手册会被当事实背出来，
状态写进去就会过期；地图每次现取，不会。

正文是仓库里的 markdown（`guide/topics/NN-xxx.md`），文件头三行：

    ---
    topic: models-and-routes
    title: 模型与线路
    ask: 她说「换模型」「识图不对」时翻
    ---

目录（`render_directory`）进静态前缀，**由文件生成，不手写第二份**。
正文不进前缀，随时可改；目录变了 = 前缀变了 = 缓存冷一次，所以改目录要走重启
（同 `personality/prompt.py` 开头那段说明）。

⚠️ 正文里的 `<!-- ... -->` 是给人审稿用的「来源标注」，加载时整段删掉，
不会返回给模型。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TOPICS_DIR = Path(__file__).resolve().parent / "topics"

_FILE_RE = re.compile(r"^(\d{2})-(.+)\.md$")
_ID_RE = re.compile(r"^[a-z0-9-]+$")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_KEYS = ("topic", "title", "ask")

DIRECTORY_HEAD = "Caelum 使用手册（用 guide_read 翻）与环境地图（用 caelum_map 查）："
DIRECTORY_TAIL = (
    "手册只讲怎么做，不讲现在怎样；现在怎样用 caelum_map 或对应工具查，以返回为准。"
)


class GuideError(ValueError):
    """手册文件不合格。启动时炸，好过某天他翻出一篇半截的。"""


@dataclass(frozen=True)
class Topic:
    id: str
    title: str
    ask: str
    body: str


def _parse(path: Path) -> Topic:
    m = _FILE_RE.match(path.name)
    assert m is not None  # 调用方已按文件名筛过
    expected_id = m.group(2)

    text = path.read_text(encoding="utf-8-sig")
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    if not lines or lines[0].strip() != "---":
        raise GuideError(f"{path.name}：第一行必须是 ---（文件头起点）")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise GuideError(f"{path.name}：文件头没有结尾的 ---") from None

    head: dict[str, str] = {}
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        if ":" not in raw:
            raise GuideError(f"{path.name}：文件头这行不是 key: value —— {raw!r}")
        key, value = raw.split(":", 1)
        key, value = key.strip(), value.strip()
        if key not in _KEYS:
            raise GuideError(f"{path.name}：文件头有多余的 key「{key}」（只许 {', '.join(_KEYS)}）")
        if key in head:
            raise GuideError(f"{path.name}：文件头的「{key}」写了两遍")
        head[key] = value
    for key in _KEYS:
        if not head.get(key):
            raise GuideError(f"{path.name}：文件头缺「{key}」或为空")

    topic_id = head["topic"]
    if not _ID_RE.match(topic_id):
        raise GuideError(f"{path.name}：topic「{topic_id}」只许小写字母、数字和 -")
    if topic_id != expected_id:
        raise GuideError(
            f"{path.name}：topic「{topic_id}」和文件名不符（文件名对应「{expected_id}」）"
        )

    body = _COMMENT_RE.sub("", "\n".join(lines[end + 1:])).strip()
    return Topic(id=topic_id, title=head["title"], ask=head["ask"], body=body)


def load_topics(directory: Path = TOPICS_DIR) -> list[Topic]:
    """读目录下全部合格篇目，按文件名排序。任何一篇不合格就整体报错。"""
    files = sorted(
        p for p in Path(directory).glob("*.md") if _FILE_RE.match(p.name)
    )
    if not files:
        # 空集不是通过：目录被删 / 改名之后，手册会静默地变成「什么都没有」
        raise GuideError(f"{directory} 下没有任何 NN-xxx.md 手册文件")
    topics = [_parse(p) for p in files]
    seen: dict[str, str] = {}
    for p, t in zip(files, topics):
        if t.id in seen:
            raise GuideError(f"topic「{t.id}」重复：{seen[t.id]} 和 {p.name}")
        seen[t.id] = p.name
    return topics


def render_directory(topics: list[Topic]) -> str:
    """常驻前缀里的目录。格式固定（测试逐行断言），改格式 = 前缀变 = 缓存冷。"""
    lines = [DIRECTORY_HEAD]
    lines += [f"- {t.id}：{t.title}（{t.ask}）" for t in topics]
    lines.append(DIRECTORY_TAIL)
    return "\n".join(lines)


def guide_read(topics: list[Topic], topic_id: object) -> str:
    """返回一篇正文。没命中**不抛**，把目录递回去让他自己纠正。"""
    key = topic_id.strip() if isinstance(topic_id, str) else ""
    for t in topics:
        if t.id == key:
            return t.body
    shown = key or repr(topic_id)
    return f"没有叫「{shown}」的篇目。现有篇目：\n" + render_directory(topics)
