"""往 GitHub 上那份 `todo.md` 里写待办。

数据源和 `context/providers/todo.py` 是同一份文件（`Iristt-boop/Claude` 的
`todo.md`），那边只读、进上下文；这边负责写。

## 为什么现在能写了

provider 的注释里写着「只读，不写 —— 她明确定的」，理由是两个 Claude
（routines 和 Nox）同时改一个文件会冲突。2026-08-05 糖糖改了主意：
她在 App 里记的待办和 todo.md 是两个孤岛，晨报只看得见后者。

冲突用 GitHub 的 sha 乐观锁兜：读的时候拿 sha，写的时候带上，
文件在这中间被改过就是 409，这里**重试一次**（重新读、重新算、重新写）。
再冲突就如实报错，绝不假装记上了。

routines 走 `claude/xxx` 分支再 PR 合 main，这边直接写 main。
真撞上的话是 PR 合并时的文本冲突，那由她在 GitHub 上解 —— 比静默丢一条待办好。

## 已完成的条目怎么处理

不删，改成 `- [x]` 并挪到 `## 已完成` 区最上面。她要拿那段回顾，
而 provider 明确把「已完成」排除在上下文外，所以不会挤占预算。
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import NamedTuple

from agent.llm import ToolSpec
from tools.github_obsidian import GithubObsidian, GithubObsidianError

logger = logging.getLogger(__name__)


class TodoOutcome(NamedTuple):
    """changed=False 不代表出错 —— 「没找到那条」也是一种如实的结果。

    调用方靠这个区分「改好了」和「什么都没改」。以前 complete() 只回一句
    话，接口一律当成功，于是 App 勾掉了、todo.md 没动，谁也没发现。
    """

    changed: bool
    message: str


class _NoChange(Exception):
    """edit 看完内容后决定不改。内部信号，不外泄。"""

#: 可以往里加的区。「已完成」不接受新增
_WRITABLE = ("进行中", "近期", "定期")
_DONE_SECTION = "已完成"

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_UNDONE_RE = re.compile(r"^(\s*-\s*)\[\s\](\s*)(.+?)\s*$")


# ⚠️ 这里**不注册** add_todo。`tools/daily.py` 已经有一个同名工具（写 App 的
# 「今天」页），重名会互相覆盖 —— 2026-08-05 实测就是我这个赢了，
# 结果 Nox 反而没法往 App 清单里记东西。
#
# 现在是一条路：`daily.add_todo` → bridge `/api/today` → 本地入库 +
# 写透到这里的 `/todo/add`。他只需要认一个工具，两边都到。

# ⚠️ 这里原来有 `COMPLETE_SPEC`（`complete_todo` 工具，往 GitHub 的 todo.md 写）。
#
# **2026-08-18 GitHub todo.md 退役**（`Todo-Daily-Planner-设计.md`）：
# 前端 todo 成为唯一活清单，`complete_todo` 搬去 `tools/daily.py` 走 bridge，
# **工具名不变**，他不用重新学。
#
# 这个模块只剩 `TodoWriter` / `read_open_items` 两个纯函数，
# 给 `api/server.py` 那几个 `/todo*` 端点用（现在没人调，留作存档访问）。
# **不要在这里再定义任何 ToolSpec** —— 和 daily.py 重名会静默覆盖，
# 2026-08-05 已经栽过一次（见 `tests/test_tool_names_unique.py`）。


class TodoWriter:
    """读改写 todo.md。每次操作都重新读，不缓存 —— 待办这东西不能写岔。"""

    def __init__(self, token: str, repo: str, path: str = "todo.md") -> None:
        self.gh = GithubObsidian(token, repo)
        self.path = path.strip().lstrip("/")

    # -------------------------------------------------------------- 增

    def add(self, text: str, section: str = "近期") -> str:
        if section not in _WRITABLE:
            section = "近期"
        line = f"- [ ] {text}"

        def edit(content: str) -> str:
            lines = content.splitlines()
            idx = _section_end(lines, section)
            if idx is None:
                # 区不存在就在文件末尾补一个，别把待办丢了
                lines += ["", f"## {section}", "", line]
            else:
                lines.insert(idx, line)
            return "\n".join(lines) + "\n"

        self._rewrite(edit, f"add todo: {text[:50]}")
        return f"记上了，在「{section}」：{text}"

    # -------------------------------------------------------------- 改

    def complete(self, keyword: str) -> TodoOutcome:
        """匹配 + 改写在同一次读里做完。

        原来是先读一遍匹配、再让 `_rewrite` 读一遍改 —— 两次读之间
        GitHub 可能还没把刚写的内容传播过来（实测：App 里加一条、
        立刻勾掉，第二次读回的是旧版本），于是「刚才明明找到了」变成
        「匹配变了，没有改」。一次读到底就没这问题。
        """
        target = ""
        note = ""

        def edit(text: str) -> str:
            nonlocal target, note
            lines = text.splitlines()
            found = _find_undone(lines, keyword)
            if not found:
                note = f"清单里没找到含「{keyword}」的未完成待办。别硬记，先跟她确认。"
                raise _NoChange
            if len(found) > 1:
                listed = "、".join(f"「{t}」" for _, t in found[:5])
                note = f"含「{keyword}」的有 {len(found)} 条：{listed}。换个更准的关键词再调一次。"
                raise _NoChange
            i, target = found[0]
            m = _UNDONE_RE.match(lines[i])
            # 不用 %-m/%-d，那是 glibc 扩展，Windows 上跑测试会炸
            today = date.today()
            done_line = (f"- [x] {m.group(3)}（{today.month}月{today.day}日完成）"
                         if m else lines[i])
            del lines[i]
            at = _section_start(lines, _DONE_SECTION)
            if at is None:
                lines += ["", f"## {_DONE_SECTION}", "", done_line]
            else:
                lines.insert(at, done_line)
            return "\n".join(lines) + "\n"

        if not self._rewrite(edit, f"done: {keyword[:50]}"):
            return TodoOutcome(False, note)
        return TodoOutcome(True, f"划掉了：{target}")

    # -------------------------------------------------------------- 读改写

    def _rewrite(self, edit, message: str) -> bool:
        """读 → 改 → 带 sha 写。409 就整个重来一次。

        edit 抛 `_NoChange` 表示「看清楚了，但没什么可改」——
        返回 False，由调用方把原因告诉她。这不是错误，不该抛。
        """
        for attempt in (1, 2):
            content, sha = self.gh.read(self.path)
            if not content:
                raise GithubObsidianError(f"{self.path} 是空的或读不到，没有写")
            try:
                new = edit(content)
            except _NoChange:
                return False
            try:
                self._put(new, sha, message)
                return True
            except GithubObsidianError as exc:
                # 409 = 这中间被 routines 改过了，重读一遍再算
                if "409" not in str(exc) or attempt == 2:
                    raise
                logger.warning("todo.md 写入撞 sha，重试一次")
        return False

    def _put(self, content: str, sha: str, message: str) -> None:
        import base64

        body: dict = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        self.gh._api("PUT", self.path, body)


# ------------------------------------------------------------------ 文本处理


def _section_start(lines: list[str], name: str) -> int | None:
    """区标题下第一个可插入位置（跳过紧跟的空行）。"""
    for i, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m and m.group(1).strip() == name:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            return j
    return None


def _section_end(lines: list[str], name: str) -> int | None:
    """区里最后一个条目的下一行 —— 新条目追加在区尾，保持她原来的顺序。"""
    start = _section_start(lines, name)
    if start is None:
        return None
    last = start
    for i in range(start, len(lines)):
        if _SECTION_RE.match(lines[i]):
            break
        if lines[i].strip():
            last = i + 1
    return last


def _find_undone(lines: list[str], keyword: str) -> list[tuple[int, str]]:
    """所有含 keyword 的未完成条目 [(行号, 正文)]。「已完成」区不参与匹配。"""
    kw = keyword.strip().lower()
    out: list[tuple[int, str]] = []
    section = ""
    for i, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            continue
        if section == _DONE_SECTION:
            continue
        hit = _UNDONE_RE.match(line)
        if hit and kw in hit.group(3).lower():
            out.append((i, hit.group(3).strip()))
    return out


# ------------------------------------------------------------------ 读


def read_open_items(writer: TodoWriter) -> dict[str, list[str]]:
    """所有未完成项，按区分组。给 App「今天」页用。

    和 `context/providers/todo.py` 的区别：那边为了省 token 会裁标题、
    只留破折号前那截；这边给她自己看，所以给全文。
    """
    content, _ = writer.gh.read(writer.path)
    lines = content.splitlines()
    out: dict[str, list[str]] = {}
    section = ""
    for line in lines:
        m = _SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            continue
        if section not in _WRITABLE:
            continue
        hit = _UNDONE_RE.match(line)
        if hit:
            out.setdefault(section, []).append(hit.group(3).strip())
    return out


# ------------------------------------------------------------------ 注册
#
# 这个模块**不再注册任何工具**（2026-08-18）。
# `complete_todo` 在 `tools/daily.py`，走 bridge 的本地清单。
# GitHub todo.md 是只读存档，没有代码再写它。
