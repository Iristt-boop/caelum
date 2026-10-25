"""SKILL.md 解析。和 Claude 的 skill 同一种格式（设计稿第二节），所以网上的能直接读：

    ---
    name: xhs-topics
    description: 一句话：做什么、什么时候用
    when: 选题, 写什么, 小红书        ← Caelum 自己加的触发词，可不填
    ---
    正文（做法本身）

`description` 可以是 YAML 的多行写法（`|` / `>`，Claude 的 skill 常这么写），所以用 safe_load。
别的键（license、metadata……）收下不用，不报错 —— 网上的 skill 头里什么都有。
不合格就抛 `SkillError`，带一句她看得懂的原因（页面上原样显示）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
#: 正文上限。每轮命中时整篇递进去的门槛另算（render.INLINE_MAX），这里只防贴进来一本书
BODY_MAX = 20000
DESC_MAX = 1024
#: 触发词至少两个字 —— 单字（「读」「写」）几乎每句话都撞（同 GoalsProvider._mentioned）
TRIGGER_MIN = 2
_SPLIT = re.compile(r"[,，、;；\n]+")


class SkillError(ValueError):
    """这份 SKILL.md 收不了。"""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    when: tuple[str, ...]
    body: str


def _triggers(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    items = raw if isinstance(raw, list) else _SPLIT.split(str(raw))
    out: list[str] = []
    for it in items:
        w = str(it).strip()
        if len(w) >= TRIGGER_MIN and w not in out:
            out.append(w)
    return tuple(out)


def parse(text: str) -> Skill:
    if not isinstance(text, str) or not text.strip():
        raise SkillError("是空的")
    lines = text.lstrip("﻿").replace("\r\n", "\n").split("\n")
    if lines[0].strip() != "---":
        raise SkillError("第一行要是 ---（文件头起点），SKILL.md 的开头是一段 --- 包着的 name / description")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise SkillError("文件头没有结尾的 ---") from None
    try:
        head = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"文件头读不懂：{exc}") from None
    if not isinstance(head, dict):
        raise SkillError("文件头要是 key: value 的写法")

    name = str(head.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise SkillError(f"name「{name}」只许小写字母、数字和 -，64 个字以内")
    desc = " ".join(str(head.get("description") or "").split())
    if not desc:
        raise SkillError("缺 description（一句话说它做什么、什么时候用）")
    if len(desc) > DESC_MAX:
        raise SkillError(f"description 太长了（{len(desc)} 字，最多 {DESC_MAX}）")
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        raise SkillError("正文是空的")
    if len(body) > BODY_MAX:
        raise SkillError(f"正文太长了（{len(body)} 字，最多 {BODY_MAX}）")
    return Skill(name=name, description=desc, when=_triggers(head.get("when")), body=body)
